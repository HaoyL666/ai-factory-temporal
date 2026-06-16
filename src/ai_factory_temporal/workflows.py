from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.exceptions import ApplicationError

from ai_factory_temporal.models import StepStatus, WorkflowStatus


@workflow.defn
class AIFactoryWorkflow:
    def __init__(self) -> None:
        self.status = WorkflowStatus.PENDING.value
        self.current_step_id: str | None = None
        self.pending_approval_step_id: str | None = None
        self.pending_failure: dict[str, Any] | None = None
        self.approvals: dict[str, dict[str, Any]] = {}
        self.feedback_events: list[dict[str, Any]] = []
        self.cancel_requested = False
        self.cancel_reason: str | None = None
        self.last_checkpoint_commit: str | None = None

    @workflow.run
    async def run(self, spec: dict[str, Any]) -> dict[str, Any]:
        initial_checkpoint = await self._activity(
            "get_workspace_checkpoint",
            {"workspace_path": spec["workspace_path"]},
            activity_id="workspace-checkpoint-initial",
            summary="Capture initial workspace checkpoint",
        )
        self.last_checkpoint_commit = initial_checkpoint.get("commit")

        await self._set_workflow_status(
            spec,
            WorkflowStatus.RUNNING.value,
            activity_id="workflow-status-running-started",
            summary="Workflow RUNNING: started",
        )

        previous_outputs: list[dict[str, Any]] = []
        for step_order, step in enumerate(spec["steps"], start=1):
            if await self._maybe_cancel(spec):
                return {"workflow_id": spec["workflow_id"], "status": WorkflowStatus.CANCELLED.value}

            self.current_step_id = step["id"]
            await self._record_step_timeline_event(spec, "start", step, step_order)
            if step["kind"] == "approval":
                result = await self._run_approval_step(
                    spec,
                    step,
                    step_order,
                    self.last_checkpoint_commit,
                )
                previous_outputs.append(result)
                self._update_last_checkpoint(result)
                await self._record_step_timeline_event(
                    spec,
                    "finish",
                    step,
                    step_order,
                    status=str(result.get("status")),
                )
                if result["status"] == StepStatus.FAILED.value:
                    self.current_step_id = None
                    await self._set_workflow_status(
                        spec,
                        WorkflowStatus.FAILED.value,
                        activity_id=(
                            f"workflow-status-failed-"
                            f"{self._timeline_step_ref(step_order, step['id'])}"
                        ),
                        summary=self._workflow_status_summary(
                            WorkflowStatus.FAILED.value,
                            step_order,
                            step["id"],
                        ),
                    )
                    raise ApplicationError(
                        result.get("error") or f"Workflow failed at step {step['id']}",
                        {
                            "workflow_id": spec["workflow_id"],
                            "step_id": step["id"],
                            "status": self.status,
                        },
                        type="AIFactoryStepFailed",
                        non_retryable=True,
                    )
                if result["status"] == WorkflowStatus.CANCELLED.value:
                    return {"workflow_id": spec["workflow_id"], "status": self.status}
                continue

            result = await self._run_retryable_step(
                spec,
                step,
                previous_outputs,
                step_order,
                self.last_checkpoint_commit,
            )
            previous_outputs.append(result)
            self._update_last_checkpoint(result)
            await self._record_step_timeline_event(
                spec,
                "finish",
                step,
                step_order,
                status=str(result.get("status")),
            )
            if result["status"] == StepStatus.FAILED.value:
                self.current_step_id = None
                await self._set_workflow_status(
                    spec,
                    WorkflowStatus.FAILED.value,
                    activity_id=f"workflow-status-failed-{self._timeline_step_ref(step_order, step['id'])}",
                    summary=self._workflow_status_summary(
                        WorkflowStatus.FAILED.value,
                        step_order,
                        step["id"],
                    ),
                )
                raise ApplicationError(
                    result.get("error") or f"Workflow failed at step {step['id']}",
                    {
                        "workflow_id": spec["workflow_id"],
                        "step_id": step["id"],
                        "status": self.status,
                    },
                    type="AIFactoryStepFailed",
                    non_retryable=True,
                )
            if result["status"] == WorkflowStatus.CANCELLED.value:
                return {"workflow_id": spec["workflow_id"], "status": self.status}

        self.current_step_id = None
        await self._set_workflow_status(
            spec,
            WorkflowStatus.COMPLETED.value,
            activity_id="workflow-status-completed",
            summary="Workflow COMPLETED",
        )
        return {"workflow_id": spec["workflow_id"], "status": self.status}

    @workflow.signal
    async def approve(self, payload: dict[str, Any]) -> None:
        self.approvals[payload["step_id"]] = payload

    @workflow.signal
    async def submit_feedback(self, payload: dict[str, Any]) -> None:
        self.feedback_events.append(payload)

    @workflow.signal
    async def cancel(self, reason: str = "cancel requested") -> None:
        self.cancel_requested = True
        self.cancel_reason = reason

    @workflow.query
    def state(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "current_step_id": self.current_step_id,
            "pending_approval_step_id": self.pending_approval_step_id,
            "pending_failure": self.pending_failure,
            "queued_feedback": len(self.feedback_events),
            "cancel_requested": self.cancel_requested,
            "last_checkpoint_commit": self.last_checkpoint_commit,
        }

    async def _run_approval_step(
        self,
        spec: dict[str, Any],
        step: dict[str, Any],
        step_order: int,
        last_checkpoint_commit: str | None,
    ) -> dict[str, Any]:
        self.pending_approval_step_id = step["id"]
        await self._activity(
            "record_approval_wait",
            {
                "db_path": spec["db_path"],
                "workflow_id": spec["workflow_id"],
                "step_id": step["id"],
                "message": step.get("message", f"Approval required for {step['id']}"),
            },
            activity_id=f"approval-wait-{self._timeline_step_ref(step_order, step['id'])}",
            summary=self._timeline_summary("Step waiting for approval", step_order, step["id"]),
        )
        await self._set_workflow_status(
            spec,
            WorkflowStatus.WAITING_FOR_APPROVAL.value,
            activity_id=f"workflow-status-waiting-approval-{self._timeline_step_ref(step_order, step['id'])}",
            summary=self._workflow_status_summary(
                WorkflowStatus.WAITING_FOR_APPROVAL.value,
                step_order,
                step["id"],
            ),
        )
        await workflow.wait_condition(
            lambda: step["id"] in self.approvals or self.cancel_requested
        )
        if await self._maybe_cancel(spec):
            return {"step_id": step["id"], "status": WorkflowStatus.CANCELLED.value}

        approval = self.approvals.pop(step["id"])
        approved = bool(approval.get("approved", True))
        await self._activity(
            "record_approval_decision",
            {
                "db_path": spec["db_path"],
                "workflow_id": spec["workflow_id"],
                "step_id": step["id"],
                "approved": approved,
                "comment": approval.get("comment"),
            },
            activity_id=f"approval-decision-{self._timeline_step_ref(step_order, step['id'])}",
            summary=self._timeline_summary("Approval decision", step_order, step["id"]),
        )
        self.pending_approval_step_id = None
        await self._set_workflow_status(
            spec,
            WorkflowStatus.RUNNING.value,
            activity_id=(
                f"workflow-status-running-after-approval-"
                f"{self._timeline_step_ref(step_order, step['id'])}"
            ),
            summary=self._timeline_summary("Workflow running after approval", step_order, step["id"]),
        )

        if not approved:
            return await self._activity(
                "record_step_skipped",
                {
                    "db_path": spec["db_path"],
                    "workflow_id": spec["workflow_id"],
                    "step_id": step["id"],
                    "reason": "approval denied",
                },
                activity_id=f"skip-{self._timeline_step_ref(step_order, step['id'])}",
                summary=self._timeline_summary("Approval denied; step skipped", step_order, step["id"]),
            )

        approved_step = step.get("approved_step")
        if not approved_step:
            return await self._activity(
                "record_step_succeeded",
                {
                    "db_path": spec["db_path"],
                    "workflow_id": spec["workflow_id"],
                    "step_id": step["id"],
                    "output": {"decision": "approved"},
                },
                activity_id=f"approve-complete-{self._timeline_step_ref(step_order, step['id'])}",
                summary=self._timeline_summary("Approval step complete", step_order, step["id"]),
            )

        nested_step = dict(approved_step)
        nested_step.setdefault("id", step["id"])
        nested_step.setdefault("kind", "deterministic")
        nested_step.setdefault("runner", "script")
        nested_step.setdefault("name", step.get("name", step["id"]))
        return await self._run_retryable_step(
            spec,
            nested_step,
            [],
            step_order,
            last_checkpoint_commit,
        )

    async def _run_retryable_step(
        self,
        spec: dict[str, Any],
        step: dict[str, Any],
        previous_outputs: list[dict[str, Any]],
        step_order: int,
        last_checkpoint_commit: str | None,
    ) -> dict[str, Any]:
        feedback_retry_count = 0
        retry_feedback: dict[str, Any] | None = None
        max_feedback_retries = int(step.get("max_feedback_retries", 3))

        while True:
            if await self._maybe_cancel(spec):
                return {"step_id": step["id"], "status": WorkflowStatus.CANCELLED.value}

            activity_name = "run_codex_step" if step["kind"] == "codex" else "run_script_step"
            result = await self._activity(
                activity_name,
                {
                    **spec,
                    "step": step,
                    "previous_outputs": previous_outputs,
                    "feedback_retry_count": feedback_retry_count,
                    "retry_feedback": retry_feedback,
                },
                timeout_seconds=int(step.get("timeout_seconds", 1800)) + 60,
                activity_id=(
                    f"run-{self._timeline_step_ref(step_order, step['id'])}"
                    f"-step-run-{feedback_retry_count + 1}"
                ),
                summary=self._timeline_summary(
                    f"Run {step['kind']} step run {feedback_retry_count + 1}",
                    step_order,
                    step["id"],
                ),
            )
            if result["status"] == StepStatus.SUCCEEDED.value:
                self.pending_failure = None
                self.status = WorkflowStatus.RUNNING.value
                return result

            self.pending_failure = {
                "step_id": step["id"],
                "error": result.get("error"),
                "artifact_uri": result.get("artifact_uri"),
                "feedback_retry_count": feedback_retry_count,
            }
            await self._activity(
                "record_step_waiting_for_feedback",
                {
                    "db_path": spec["db_path"],
                    "workflow_id": spec["workflow_id"],
                    "step_id": step["id"],
                    "output": result.get("output", {}),
                    "artifact_uri": result.get("artifact_uri"),
                    "error": result.get("error"),
                    "feedback_retry_count": feedback_retry_count,
                },
                activity_id=(
                    f"waiting-feedback-{self._timeline_step_ref(step_order, step['id'])}"
                    f"-step-run-{feedback_retry_count + 1}"
                ),
                summary=self._timeline_summary(
                    f"Step waiting for feedback after step run {feedback_retry_count + 1}",
                    step_order,
                    step["id"],
                ),
            )
            await self._set_workflow_status(
                spec,
                WorkflowStatus.WAITING_FOR_FEEDBACK.value,
                activity_id=(
                    f"workflow-status-waiting-feedback-{self._timeline_step_ref(step_order, step['id'])}"
                    f"-step-run-{feedback_retry_count + 1}"
                ),
                summary=self._workflow_status_summary(
                    WorkflowStatus.WAITING_FOR_FEEDBACK.value,
                    step_order,
                    step["id"],
                ),
            )
            await workflow.wait_condition(
                lambda: self._has_feedback_for_step(step["id"]) or self.cancel_requested
            )
            if await self._maybe_cancel(spec):
                return {"step_id": step["id"], "status": WorkflowStatus.CANCELLED.value}

            feedback = self._pop_feedback_for_step(step["id"])
            await self._activity(
                "record_feedback",
                {
                    "db_path": spec["db_path"],
                    "workflow_id": spec["workflow_id"],
                    "step_id": step["id"],
                    "action": feedback.get("action", "retry"),
                    "message": feedback.get("message", ""),
                },
                activity_id=(
                    f"feedback-{feedback.get('action', 'retry')}-"
                    f"{self._timeline_step_ref(step_order, step['id'])}"
                    f"-step-run-{feedback_retry_count + 1}"
                ),
                summary=self._timeline_summary(
                    f"Feedback {feedback.get('action', 'retry')}",
                    step_order,
                    step["id"],
                ),
            )
            action = feedback.get("action", "retry")
            if action == "retry" and feedback_retry_count < max_feedback_retries:
                await self._reset_workspace_to_checkpoint(
                    spec,
                    last_checkpoint_commit,
                    activity_id=(
                        f"reset-retry-{self._timeline_step_ref(step_order, step['id'])}"
                        f"-step-run-{feedback_retry_count + 1}"
                    ),
                    summary=self._timeline_summary(
                        "Reset workspace before retry",
                        step_order,
                        step["id"],
                    ),
                )
                feedback_retry_count += 1
                retry_feedback = feedback
                self.pending_failure = None
                await self._set_workflow_status(
                    spec,
                    WorkflowStatus.RUNNING.value,
                    activity_id=(
                        f"workflow-status-running-retry-{self._timeline_step_ref(step_order, step['id'])}"
                        f"-step-run-{feedback_retry_count + 1}"
                    ),
                    summary=self._timeline_summary(
                        f"Workflow running for feedback retry, step run {feedback_retry_count + 1}",
                        step_order,
                        step["id"],
                    ),
                )
                continue
            if action == "skip" and step.get("allow_skip", False):
                self.pending_failure = None
                reset_result = await self._reset_workspace_to_checkpoint(
                    spec,
                    last_checkpoint_commit,
                    activity_id=(
                        f"reset-skip-{self._timeline_step_ref(step_order, step['id'])}"
                        f"-step-run-{feedback_retry_count + 1}"
                    ),
                    summary=self._timeline_summary(
                        "Reset workspace before skip",
                        step_order,
                        step["id"],
                    ),
                )
                await self._set_workflow_status(
                    spec,
                    WorkflowStatus.RUNNING.value,
                    activity_id=(
                        f"workflow-status-running-skip-"
                        f"{self._timeline_step_ref(step_order, step['id'])}"
                    ),
                    summary=self._timeline_summary("Workflow running after skip", step_order, step["id"]),
                )
                return await self._activity(
                    "record_step_skipped",
                    {
                        "db_path": spec["db_path"],
                        "workflow_id": spec["workflow_id"],
                        "step_id": step["id"],
                        "reason": feedback.get("message", "skipped after feedback"),
                        "reset": reset_result,
                    },
                    activity_id=f"skip-{self._timeline_step_ref(step_order, step['id'])}",
                    summary=self._timeline_summary("Step skipped", step_order, step["id"]),
                )

            reset_result = await self._reset_workspace_to_checkpoint(
                spec,
                last_checkpoint_commit,
                activity_id=(
                    f"reset-final-failure-{self._timeline_step_ref(step_order, step['id'])}"
                    f"-step-run-{feedback_retry_count + 1}"
                ),
                summary=self._timeline_summary(
                    "Reset workspace before final failure",
                    step_order,
                    step["id"],
                ),
            )
            failed_output = dict(result.get("output", {}))
            failed_output["workspace_reset"] = reset_result
            await self._activity(
                "record_step_failed",
                {
                    "db_path": spec["db_path"],
                    "workflow_id": spec["workflow_id"],
                    "step_id": step["id"],
                    "output": failed_output,
                    "artifact_uri": result.get("artifact_uri"),
                    "error": result.get("error"),
                    "feedback_retry_count": feedback_retry_count,
                },
                activity_id=f"fail-{self._timeline_step_ref(step_order, step['id'])}",
                summary=self._timeline_summary("Step failed", step_order, step["id"]),
            )
            return {
                "step_id": step["id"],
                "status": StepStatus.FAILED.value,
                "error": result.get("error"),
            }

    async def _maybe_cancel(self, spec: dict[str, Any]) -> bool:
        if not self.cancel_requested:
            return False
        await self._set_workflow_status(
            spec,
            WorkflowStatus.CANCELLED.value,
            activity_id="workflow-status-cancelled",
            summary=f"Workflow CANCELLED: {self.cancel_reason or 'cancel requested'}",
        )
        return True

    async def _reset_workspace_to_checkpoint(
        self,
        spec: dict[str, Any],
        checkpoint_commit: str | None,
        *,
        activity_id: str,
        summary: str,
    ) -> dict[str, Any]:
        return await self._activity(
            "reset_workspace_to_checkpoint",
            {
                "workspace_path": spec["workspace_path"],
                "checkpoint_commit": checkpoint_commit,
                "db_path": spec["db_path"],
                "artifacts_path": spec["artifacts_path"],
            },
            activity_id=activity_id,
            summary=summary,
        )

    def _update_last_checkpoint(self, result: dict[str, Any]) -> None:
        output = result.get("output")
        if not isinstance(output, dict):
            return
        checkpoint = output.get("checkpoint")
        if not isinstance(checkpoint, dict):
            return
        commit = checkpoint.get("commit")
        if isinstance(commit, str) and commit:
            self.last_checkpoint_commit = commit

    def _has_feedback_for_step(self, step_id: str) -> bool:
        return any(event.get("step_id") == step_id for event in self.feedback_events)

    def _pop_feedback_for_step(self, step_id: str) -> dict[str, Any]:
        for index, event in enumerate(self.feedback_events):
            if event.get("step_id") == step_id:
                return self.feedback_events.pop(index)
        raise RuntimeError(f"No feedback queued for step {step_id}")

    async def _activity(
        self,
        name: str,
        payload: dict[str, Any],
        timeout_seconds: int = 60,
        activity_id: str | None = None,
        summary: str | None = None,
    ) -> Any:
        return await workflow.execute_activity(
            name,
            payload,
            start_to_close_timeout=timedelta(seconds=timeout_seconds),
            activity_id=activity_id,
            summary=summary,
        )

    async def _set_workflow_status(
        self,
        spec: dict[str, Any],
        status: str,
        *,
        activity_id: str,
        summary: str,
    ) -> None:
        self.status = status
        await self._activity(
            "set_workflow_status",
            {"db_path": spec["db_path"], "workflow_id": spec["workflow_id"], "status": status},
            activity_id=activity_id,
            summary=summary,
        )

    async def _record_step_timeline_event(
        self,
        spec: dict[str, Any],
        event: str,
        step: dict[str, Any],
        step_order: int,
        status: str | None = None,
    ) -> None:
        payload = {
            "workflow_id": spec["workflow_id"],
            "step_id": step["id"],
            "step_order": step_order,
            "step_name": step.get("name", step["id"]),
            "kind": step["kind"],
            "event": event,
            "status": status,
        }
        suffix = f"-{self._safe_activity_id(status)}" if status else ""
        await self._activity(
            "record_step_timeline_event",
            payload,
            activity_id=f"{event}-{self._timeline_step_ref(step_order, step['id'])}{suffix}",
            summary=self._timeline_summary(event.title(), step_order, step["id"], status),
        )

    def _timeline_step_ref(self, step_order: int, step_id: str) -> str:
        return f"step-{step_order:02d}-{self._safe_activity_id(step_id)}"

    def _timeline_summary(
        self,
        action: str,
        step_order: int,
        step_id: str,
        status: str | None = None,
    ) -> str:
        status_text = f" -> {status}" if status else ""
        return f"{action}: step {step_order:02d} {step_id}{status_text}"

    def _workflow_status_summary(
        self,
        status: str,
        step_order: int,
        step_id: str,
    ) -> str:
        return f"Workflow {status}: step {step_order:02d} {step_id}"

    def _safe_activity_id(self, value: str) -> str:
        return "".join(char if char.isalnum() or char in "._-" else "-" for char in value)[:120]
