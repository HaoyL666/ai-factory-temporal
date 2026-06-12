from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

from temporalio import activity

from ai_factory_temporal import config
from ai_factory_temporal.models import StepStatus
from ai_factory_temporal.prompting import render_prompt
from ai_factory_temporal.store import SQLiteStore


@activity.defn(name="set_workflow_status")
def set_workflow_status(payload: dict[str, Any]) -> None:
    SQLiteStore(Path(payload["db_path"])).set_workflow_status(
        payload["workflow_id"],
        payload["status"],
    )


@activity.defn(name="record_step_timeline_event")
def record_step_timeline_event(payload: dict[str, Any]) -> dict[str, Any]:
    return payload


@activity.defn(name="record_approval_wait")
def record_approval_wait(payload: dict[str, Any]) -> None:
    store = SQLiteStore(Path(payload["db_path"]))
    store.finish_step(
        workflow_id=payload["workflow_id"],
        step_id=payload["step_id"],
        status=StepStatus.WAITING_FOR_APPROVAL.value,
        output={
            "approval_required": True,
            "message": payload.get("message"),
        },
    )


@activity.defn(name="record_approval_decision")
def record_approval_decision(payload: dict[str, Any]) -> None:
    store = SQLiteStore(Path(payload["db_path"]))
    store.record_approval(
        payload["workflow_id"],
        payload["step_id"],
        bool(payload["approved"]),
        payload.get("comment"),
    )


@activity.defn(name="record_step_skipped")
def record_step_skipped(payload: dict[str, Any]) -> dict[str, Any]:
    output = {"decision": "skipped", "reason": payload.get("reason", "step skipped")}
    SQLiteStore(Path(payload["db_path"])).finish_step(
        workflow_id=payload["workflow_id"],
        step_id=payload["step_id"],
        status=StepStatus.SKIPPED.value,
        output=output,
    )
    return {
        "step_id": payload["step_id"],
        "status": StepStatus.SKIPPED.value,
        "output": output,
    }


@activity.defn(name="record_step_succeeded")
def record_step_succeeded(payload: dict[str, Any]) -> dict[str, Any]:
    output = payload.get("output") or {"status": "succeeded"}
    SQLiteStore(Path(payload["db_path"])).finish_step(
        workflow_id=payload["workflow_id"],
        step_id=payload["step_id"],
        status=StepStatus.SUCCEEDED.value,
        output=output,
        artifact_uri=payload.get("artifact_uri"),
    )
    return {
        "step_id": payload["step_id"],
        "status": StepStatus.SUCCEEDED.value,
        "output": output,
        "artifact_uri": payload.get("artifact_uri"),
    }


@activity.defn(name="record_step_waiting_for_feedback")
def record_step_waiting_for_feedback(payload: dict[str, Any]) -> None:
    store = SQLiteStore(Path(payload["db_path"]))
    store.finish_step(
        workflow_id=payload["workflow_id"],
        step_id=payload["step_id"],
        status=StepStatus.FAILED_WAITING_FOR_FEEDBACK.value,
        output=payload.get("output", {}),
        artifact_uri=payload.get("artifact_uri"),
        error=payload.get("error"),
        retry_count=payload.get("retry_count"),
    )


@activity.defn(name="record_step_failed")
def record_step_failed(payload: dict[str, Any]) -> dict[str, Any]:
    output = payload.get("output", {})
    SQLiteStore(Path(payload["db_path"])).finish_step(
        workflow_id=payload["workflow_id"],
        step_id=payload["step_id"],
        status=StepStatus.FAILED.value,
        output=output,
        artifact_uri=payload.get("artifact_uri"),
        error=payload.get("error"),
        retry_count=payload.get("retry_count"),
    )
    return {
        "step_id": payload["step_id"],
        "status": StepStatus.FAILED.value,
        "output": output,
        "artifact_uri": payload.get("artifact_uri"),
        "error": payload.get("error"),
    }


@activity.defn(name="record_feedback")
def record_feedback(payload: dict[str, Any]) -> None:
    SQLiteStore(Path(payload["db_path"])).record_feedback(
        payload["workflow_id"],
        payload["step_id"],
        payload["action"],
        payload.get("message", ""),
    )


@activity.defn(name="run_script_step")
def run_script_step(payload: dict[str, Any]) -> dict[str, Any]:
    store = SQLiteStore(Path(payload["db_path"]))
    workflow_id = payload["workflow_id"]
    step = payload["step"]
    step_id = step["id"]
    retry_count = int(payload.get("retry_count", 0))
    workspace_path = Path(payload["workspace_path"])
    artifact_dir = _attempt_dir(payload["artifacts_path"], workflow_id, step_id, retry_count)

    store.mark_step_running(workflow_id, step_id, retry_count)
    command = step.get("command")
    if not command:
        raise ValueError("script step is missing command")

    resolved_command = [
        _replace_placeholders(str(part), payload, artifact_dir)
        for part in command
    ]
    cwd = Path(_replace_placeholders(str(step.get("cwd", "{{workspace_path}}")), payload, artifact_dir))
    env = os.environ.copy()
    env.update(
        {
            "AI_FACTORY_WORKFLOW_ID": workflow_id,
            "AI_FACTORY_STEP_ID": step_id,
            "AI_FACTORY_ARTIFACT_DIR": str(artifact_dir),
            "AI_FACTORY_INPUTS_JSON": json.dumps(payload["inputs"], sort_keys=True),
            "AI_FACTORY_WORKSPACE_PATH": str(workspace_path),
            "AI_FACTORY_RETRY_COUNT": str(retry_count),
            "AI_FACTORY_RETRY_FEEDBACK_JSON": json.dumps(
                payload.get("retry_feedback") or {},
                sort_keys=True,
            ),
        }
    )

    stdout_path = artifact_dir / "stdout.log"
    stderr_path = artifact_dir / "stderr.log"
    timeout_seconds = int(step.get("timeout_seconds", 600))
    try:
        completed = subprocess.run(
            resolved_command,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        output = {
            "runner": "script",
            "command": resolved_command,
            "timeout_seconds": timeout_seconds,
        }
        return _finish_failed(
            store,
            workflow_id,
            step_id,
            retry_count,
            artifact_dir,
            output,
            f"script timed out after {timeout_seconds} seconds",
        )

    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    output = {
        "runner": step.get("runner", "script"),
        "command": resolved_command,
        "returncode": completed.returncode,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }
    if completed.returncode != 0:
        return _finish_failed(
            store,
            workflow_id,
            step_id,
            retry_count,
            artifact_dir,
            output,
            f"script failed with exit code {completed.returncode}",
        )

    store.finish_step(
        workflow_id=workflow_id,
        step_id=step_id,
        status=StepStatus.SUCCEEDED.value,
        output=output,
        artifact_uri=str(artifact_dir),
        retry_count=retry_count,
    )
    return {
        "step_id": step_id,
        "status": StepStatus.SUCCEEDED.value,
        "output": output,
        "artifact_uri": str(artifact_dir),
    }


@activity.defn(name="run_codex_step")
def run_codex_step(payload: dict[str, Any]) -> dict[str, Any]:
    store = SQLiteStore(Path(payload["db_path"]))
    workflow_id = payload["workflow_id"]
    step = payload["step"]
    step_id = step["id"]
    retry_count = int(payload.get("retry_count", 0))
    artifact_dir = _attempt_dir(payload["artifacts_path"], workflow_id, step_id, retry_count)

    store.mark_step_running(workflow_id, step_id, retry_count)
    _codex_output_contract(step)
    review_config = _codex_review_config(step)
    prompt = render_prompt(
        project_dir=Path(payload["project_dir"]),
        workflow_id=workflow_id,
        project_id=payload["project_id"],
        task_type=payload["task_type"],
        workspace_path=payload["workspace_path"],
        step=step,
        inputs=payload["inputs"],
        project_pack=payload["project_pack"],
        previous_outputs=payload.get("previous_outputs", []),
        retry_feedback=payload.get("retry_feedback"),
        attempt=retry_count + 1,
    )

    if review_config["enabled"]:
        return _run_reviewed_codex_step(
            store=store,
            payload=payload,
            step=step,
            workflow_id=workflow_id,
            step_id=step_id,
            retry_count=retry_count,
            artifact_dir=artifact_dir,
            initial_prompt=prompt,
            review_config=review_config,
        )

    return _run_single_codex_step(
        store=store,
        payload=payload,
        step=step,
        workflow_id=workflow_id,
        step_id=step_id,
        retry_count=retry_count,
        artifact_dir=artifact_dir,
        prompt=prompt,
    )


def _run_single_codex_step(
    *,
    store: SQLiteStore,
    payload: dict[str, Any],
    step: dict[str, Any],
    workflow_id: str,
    step_id: str,
    retry_count: int,
    artifact_dir: Path,
    prompt: str,
) -> dict[str, Any]:
    prompt_path = artifact_dir / "codex-prompt.md"
    output_path = artifact_dir / "codex-final-response.md"
    metadata_path = artifact_dir / "codex-sdk-result.json"
    try:
        turn_output = _run_codex_turn(
            prompt=prompt,
            prompt_path=prompt_path,
            output_path=output_path,
            metadata_path=metadata_path,
            payload=payload,
            step=step,
            step_id=step_id,
            retry_count=retry_count,
            role="coder",
            review_iteration=1,
            sandbox_value=str(step.get("sandbox", config.CODEX_SANDBOX)),
            approval_mode_value=str(step.get("codex_approval_mode", config.CODEX_APPROVAL_MODE)),
            model=step.get("model") or config.CODEX_MODEL,
            timeout_seconds=int(step.get("timeout_seconds", 1800)),
        )
    except TimeoutError:
        return _finish_failed(
            store,
            workflow_id,
            step_id,
            retry_count,
            artifact_dir,
            {
                "runner": "codex",
                "mode": _codex_mode_name(),
                "timeout_seconds": int(step.get("timeout_seconds", 1800)),
            },
            f"Codex timed out after {int(step.get('timeout_seconds', 1800))} seconds",
        )

    final_response = turn_output["final_response"]
    output = {
        "runner": "codex",
        "mode": turn_output["metadata"]["mode"],
        "summary": final_response[:2000],
        "prompt": str(prompt_path),
        "final_response": str(output_path),
        "metadata": str(metadata_path),
        "thread_id": turn_output["metadata"].get("thread_id"),
        "turn_id": turn_output["metadata"].get("turn_id"),
    }
    contract_result = _evaluate_codex_output_contract(step, final_response)
    output["contract"] = contract_result["payload"]
    output["contract_status"] = contract_result.get("status")
    if contract_result.get("error"):
        return _finish_failed(
            store,
            workflow_id,
            step_id,
            retry_count,
            artifact_dir,
            output,
            contract_result["error"],
        )
    store.finish_step(
        workflow_id=workflow_id,
        step_id=step_id,
        status=StepStatus.SUCCEEDED.value,
        output=output,
        artifact_uri=str(artifact_dir),
        retry_count=retry_count,
    )
    return {
        "step_id": step_id,
        "status": StepStatus.SUCCEEDED.value,
        "output": output,
        "artifact_uri": str(artifact_dir),
    }


def _run_reviewed_codex_step(
    *,
    store: SQLiteStore,
    payload: dict[str, Any],
    step: dict[str, Any],
    workflow_id: str,
    step_id: str,
    retry_count: int,
    artifact_dir: Path,
    initial_prompt: str,
    review_config: dict[str, Any],
) -> dict[str, Any]:
    review_history: list[dict[str, Any]] = []
    coder_prompt = initial_prompt
    max_iterations = int(review_config["max_review_iterations"])

    for review_iteration in range(1, max_iterations + 1):
        iteration_dir = artifact_dir / f"review-{review_iteration}"
        iteration_dir.mkdir(parents=True, exist_ok=True)

        coder_prompt_path = iteration_dir / "coder-prompt.md"
        coder_output_path = iteration_dir / "coder-final-response.md"
        coder_metadata_path = iteration_dir / "coder-sdk-result.json"
        coder_contract_path = iteration_dir / "coder-contract.json"
        reviewer_prompt_path = iteration_dir / "reviewer-prompt.md"
        reviewer_output_path = iteration_dir / "reviewer-final-response.md"
        reviewer_metadata_path = iteration_dir / "reviewer-sdk-result.json"
        reviewer_contract_path = iteration_dir / "reviewer-contract.json"

        try:
            coder_turn = _run_codex_turn(
                prompt=coder_prompt,
                prompt_path=coder_prompt_path,
                output_path=coder_output_path,
                metadata_path=coder_metadata_path,
                payload=payload,
                step=step,
                step_id=step_id,
                retry_count=retry_count,
                role="coder",
                review_iteration=review_iteration,
                sandbox_value=str(step.get("sandbox", config.CODEX_SANDBOX)),
                approval_mode_value=str(
                    step.get("codex_approval_mode", config.CODEX_APPROVAL_MODE)
                ),
                model=step.get("model") or config.CODEX_MODEL,
                timeout_seconds=int(step.get("timeout_seconds", 1800)),
            )
        except TimeoutError:
            return _finish_reviewed_codex_failure(
                store=store,
                workflow_id=workflow_id,
                step_id=step_id,
                retry_count=retry_count,
                artifact_dir=artifact_dir,
                review_history=review_history,
                error=(
                    "Codex coder timed out after "
                    f"{int(step.get('timeout_seconds', 1800))} seconds"
                ),
            )

        coder_contract = _evaluate_codex_output_contract(step, coder_turn["final_response"])
        _write_json(coder_contract_path, coder_contract)
        if coder_contract.get("error"):
            return _finish_reviewed_codex_failure(
                store=store,
                workflow_id=workflow_id,
                step_id=step_id,
                retry_count=retry_count,
                artifact_dir=artifact_dir,
                review_history=review_history,
                error=str(coder_contract["error"]),
                coder_contract=coder_contract,
            )

        reviewer_prompt = _build_reviewer_prompt(
            original_prompt=initial_prompt,
            coder_prompt=coder_prompt,
            coder_response=coder_turn["final_response"],
            coder_contract=coder_contract["payload"],
            workspace_path=payload["workspace_path"],
            review_history=review_history,
        )
        try:
            reviewer_turn = _run_codex_turn(
                prompt=reviewer_prompt,
                prompt_path=reviewer_prompt_path,
                output_path=reviewer_output_path,
                metadata_path=reviewer_metadata_path,
                payload=payload,
                step=step,
                step_id=step_id,
                retry_count=retry_count,
                role="reviewer",
                review_iteration=review_iteration,
                sandbox_value=str(review_config["reviewer_sandbox"]),
                approval_mode_value=str(review_config["reviewer_approval_mode"]),
                model=(
                    review_config.get("reviewer_model")
                    or step.get("model")
                    or config.CODEX_MODEL
                ),
                timeout_seconds=int(review_config["reviewer_timeout_seconds"]),
            )
        except TimeoutError:
            return _finish_reviewed_codex_failure(
                store=store,
                workflow_id=workflow_id,
                step_id=step_id,
                retry_count=retry_count,
                artifact_dir=artifact_dir,
                review_history=review_history,
                error=(
                    "Codex reviewer timed out after "
                    f"{int(review_config['reviewer_timeout_seconds'])} seconds"
                ),
                coder_contract=coder_contract,
            )

        review_contract = _evaluate_codex_review_contract(reviewer_turn["final_response"])
        _write_json(reviewer_contract_path, review_contract)
        history_entry = {
            "review_iteration": review_iteration,
            "verdict": review_contract.get("verdict"),
            "coder_contract": str(coder_contract_path),
            "reviewer_contract": str(reviewer_contract_path),
            "coder_prompt": str(coder_prompt_path),
            "coder_response": str(coder_output_path),
            "reviewer_prompt": str(reviewer_prompt_path),
            "reviewer_response": str(reviewer_output_path),
        }
        review_history.append(history_entry)

        if review_contract.get("error"):
            return _finish_reviewed_codex_failure(
                store=store,
                workflow_id=workflow_id,
                step_id=step_id,
                retry_count=retry_count,
                artifact_dir=artifact_dir,
                review_history=review_history,
                error=str(review_contract["error"]),
                coder_contract=coder_contract,
                reviewer_contract=review_contract,
            )

        if review_contract["verdict"] == "APPROVED":
            return _finish_reviewed_codex_success(
                store=store,
                workflow_id=workflow_id,
                step_id=step_id,
                retry_count=retry_count,
                artifact_dir=artifact_dir,
                review_history=review_history,
                coder_contract=coder_contract,
                reviewer_contract=review_contract,
            )

        if (
            review_contract["verdict"] == "CHANGES_REQUESTED"
            and review_iteration < max_iterations
        ):
            coder_prompt = _build_repair_prompt(
                original_prompt=initial_prompt,
                review_history=review_history,
                coder_contract=coder_contract["payload"],
                reviewer_contract=review_contract["payload"],
            )
            continue

        return _finish_reviewed_codex_failure(
            store=store,
            workflow_id=workflow_id,
            step_id=step_id,
            retry_count=retry_count,
            artifact_dir=artifact_dir,
            review_history=review_history,
            error=(
                "Codex review rejected final attempt: "
                f"{review_contract['payload'].get('summary') or review_contract['verdict']}"
            ),
            coder_contract=coder_contract,
            reviewer_contract=review_contract,
        )

    return _finish_reviewed_codex_failure(
        store=store,
        workflow_id=workflow_id,
        step_id=step_id,
        retry_count=retry_count,
        artifact_dir=artifact_dir,
        review_history=review_history,
        error="Codex review loop ended without an approval verdict",
    )


def _run_codex_turn(
    *,
    prompt: str,
    prompt_path: Path,
    output_path: Path,
    metadata_path: Path,
    payload: dict[str, Any],
    step: dict[str, Any],
    step_id: str,
    retry_count: int,
    role: str,
    review_iteration: int,
    sandbox_value: str,
    approval_mode_value: str,
    model: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    prompt_path.write_text(prompt, encoding="utf-8")

    if config.CODEX_MODE == "stub":
        final_response = _stub_codex_response(
            step,
            step_id,
            retry_count,
            role=role,
            review_iteration=review_iteration,
        )
        output_path.write_text(final_response, encoding="utf-8")
        metadata = {
            "runner": "codex",
            "mode": "stub",
            "role": role,
            "review_iteration": review_iteration,
            "items_count": 0,
        }
        metadata_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {"final_response": final_response, "metadata": metadata}

    try:
        from openai_codex import ApprovalMode, Codex, Sandbox
    except ImportError as exc:
        raise RuntimeError("openai-codex is not installed") from exc

    sandbox = _sandbox(Sandbox, sandbox_value)
    approval_mode = _approval_mode(ApprovalMode, approval_mode_value)
    try:
        with Codex() as codex:
            thread = codex.thread_start(
                approval_mode=approval_mode,
                cwd=payload["workspace_path"],
                model=model,
                sandbox=sandbox,
            )
            turn = thread.turn(
                prompt,
                approval_mode=approval_mode,
                cwd=payload["workspace_path"],
                model=model,
                sandbox=sandbox,
            )
            result = _run_turn_with_timeout(turn, timeout_seconds)
    except TimeoutError:
        raise

    final_response = getattr(result, "final_response", None) or ""
    output_path.write_text(final_response, encoding="utf-8")
    metadata = {
        "runner": "codex",
        "mode": "sdk",
        "role": role,
        "review_iteration": review_iteration,
        "thread_id": getattr(thread, "id", None),
        "turn_id": getattr(result, "id", None),
        "duration_ms": getattr(result, "duration_ms", None),
        "items_count": len(getattr(result, "items", []) or []),
        "usage": _json_safe(getattr(result, "usage", None)),
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"final_response": final_response, "metadata": metadata}


def _finish_reviewed_codex_success(
    *,
    store: SQLiteStore,
    workflow_id: str,
    step_id: str,
    retry_count: int,
    artifact_dir: Path,
    review_history: list[dict[str, Any]],
    coder_contract: dict[str, Any],
    reviewer_contract: dict[str, Any],
) -> dict[str, Any]:
    final_contract = _reviewed_step_contract(
        status="SUCCEEDED",
        artifact_dir=artifact_dir,
        review_history=review_history,
        coder_contract=coder_contract,
        reviewer_contract=reviewer_contract,
        error=None,
    )
    output = _reviewed_step_output(artifact_dir, final_contract, review_history)
    _write_json(artifact_dir / "step-result.json", output)
    store.finish_step(
        workflow_id=workflow_id,
        step_id=step_id,
        status=StepStatus.SUCCEEDED.value,
        output=output,
        artifact_uri=str(artifact_dir),
        retry_count=retry_count,
    )
    return {
        "step_id": step_id,
        "status": StepStatus.SUCCEEDED.value,
        "output": output,
        "artifact_uri": str(artifact_dir),
    }


def _finish_reviewed_codex_failure(
    *,
    store: SQLiteStore,
    workflow_id: str,
    step_id: str,
    retry_count: int,
    artifact_dir: Path,
    review_history: list[dict[str, Any]],
    error: str,
    coder_contract: dict[str, Any] | None = None,
    reviewer_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final_contract = _reviewed_step_contract(
        status="FAILED",
        artifact_dir=artifact_dir,
        review_history=review_history,
        coder_contract=coder_contract,
        reviewer_contract=reviewer_contract,
        error=error,
    )
    output = _reviewed_step_output(artifact_dir, final_contract, review_history)
    _write_json(artifact_dir / "step-result.json", output)
    return _finish_failed(
        store,
        workflow_id,
        step_id,
        retry_count,
        artifact_dir,
        output,
        error,
    )


def _reviewed_step_contract(
    *,
    status: str,
    artifact_dir: Path,
    review_history: list[dict[str, Any]],
    coder_contract: dict[str, Any] | None,
    reviewer_contract: dict[str, Any] | None,
    error: str | None,
) -> dict[str, Any]:
    coder_payload = (coder_contract or {}).get("payload") or {}
    reviewer_payload = (reviewer_contract or {}).get("payload") or {}
    final_verdict = (reviewer_contract or {}).get("verdict")
    review_iterations = len(review_history)

    if status == "SUCCEEDED":
        summary = (
            f"Codex step passed review after {review_iterations} review iteration(s). "
            f"{coder_payload.get('summary', '')}"
        ).strip()
    else:
        summary = f"Codex step failed review after {review_iterations} review iteration(s)."

    evidence = _as_list(coder_payload.get("evidence"))
    evidence.extend(
        [
            str(artifact_dir / "step-result.json"),
            *[entry["coder_contract"] for entry in review_history],
            *[entry["reviewer_contract"] for entry in review_history],
        ]
    )
    checks = _as_list(coder_payload.get("checks"))
    if status == "SUCCEEDED":
        checks.append("reviewer approved final attempt")

    return {
        "status": status,
        "summary": summary,
        "changed_files": _as_list(coder_payload.get("changed_files")),
        "checks": checks,
        "evidence": evidence,
        "risks": _as_list(coder_payload.get("risks")),
        "error": error,
        "review": {
            "enabled": True,
            "final_verdict": final_verdict,
            "review_iterations": review_iterations,
            "repair_attempts": max(0, review_iterations - 1),
            "history": [
                {
                    "review_iteration": entry["review_iteration"],
                    "verdict": entry.get("verdict"),
                    "coder_contract": entry["coder_contract"],
                    "reviewer_contract": entry["reviewer_contract"],
                }
                for entry in review_history
            ],
        },
        "coder_contract": coder_payload,
        "reviewer_contract": reviewer_payload,
    }


def _reviewed_step_output(
    artifact_dir: Path,
    final_contract: dict[str, Any],
    review_history: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "runner": "codex",
        "mode": _codex_mode_name(),
        "summary": final_contract["summary"],
        "contract": final_contract,
        "contract_status": final_contract["status"],
        "review": final_contract["review"],
        "review_history": review_history,
        "artifact_uri": str(artifact_dir),
        "step_result": str(artifact_dir / "step-result.json"),
    }


def _build_reviewer_prompt(
    *,
    original_prompt: str,
    coder_prompt: str,
    coder_response: str,
    coder_contract: dict[str, Any],
    workspace_path: str,
    review_history: list[dict[str, Any]],
) -> str:
    return f"""# Codex Review Step

You are the read-only reviewer for a Codex coding step. Review the coder result,
current workspace diff, and prior review history. Do not edit files.

## Original Task Prompt

{original_prompt}

## Coder Prompt For This Iteration

{coder_prompt}

## Coder Final Response

{coder_response}

## Coder Contract

```json
{json.dumps(coder_contract, indent=2, sort_keys=True)}
```

## Current Workspace Diff

```diff
{_workspace_diff(Path(workspace_path))}
```

## Prior Review History

```json
{json.dumps(review_history, indent=2, sort_keys=True)}
```

## Review Contract

Respond with exactly one JSON object and no surrounding Markdown:

```json
{{
  "status": "SUCCEEDED",
  "verdict": "APPROVED",
  "summary": "Concise review summary.",
  "findings": [],
  "required_fixes": [],
  "error": null
}}
```

Use `"verdict": "CHANGES_REQUESTED"` when the coder must repair the result.
Use `"verdict": "BLOCKED"` when review cannot be completed safely.
"""


def _build_repair_prompt(
    *,
    original_prompt: str,
    review_history: list[dict[str, Any]],
    coder_contract: dict[str, Any],
    reviewer_contract: dict[str, Any],
) -> str:
    return f"""# Codex Repair Attempt

Continue the original task by addressing the reviewer feedback. Keep the patch
scoped and do not redo unrelated work.

## Original Task Prompt

{original_prompt}

## Previous Coder Contract

```json
{json.dumps(coder_contract, indent=2, sort_keys=True)}
```

## Reviewer Feedback

```json
{json.dumps(reviewer_contract, indent=2, sort_keys=True)}
```

## Review History

```json
{json.dumps(review_history, indent=2, sort_keys=True)}
```

Return the same coder output contract required by the original task.
"""


def _evaluate_codex_review_contract(final_response: str) -> dict[str, Any]:
    try:
        payload = _extract_json_object(final_response)
    except ValueError as exc:
        return {
            "payload": {},
            "status": None,
            "verdict": None,
            "error": f"Codex review contract violation: {exc}",
        }

    status_value = payload.get("status")
    if not isinstance(status_value, str) or not status_value.strip():
        return {
            "payload": payload,
            "status": None,
            "verdict": None,
            "error": "Codex review contract violation: missing string field `status`",
        }
    status = status_value.strip().upper().replace("-", "_")
    if status not in {"SUCCEEDED", "SUCCESS", "OK"}:
        return {
            "payload": payload,
            "status": status,
            "verdict": None,
            "error": str(payload.get("error") or payload.get("summary") or status_value),
        }

    verdict_value = payload.get("verdict")
    if not isinstance(verdict_value, str) or not verdict_value.strip():
        return {
            "payload": payload,
            "status": status,
            "verdict": None,
            "error": "Codex review contract violation: missing string field `verdict`",
        }
    verdict = verdict_value.strip().upper().replace("-", "_")
    verdict_aliases = {
        "PASS": "APPROVED",
        "PASSED": "APPROVED",
        "ACCEPTED": "APPROVED",
        "APPROVE": "APPROVED",
        "REJECTED": "CHANGES_REQUESTED",
        "NEEDS_CHANGES": "CHANGES_REQUESTED",
        "NEEDS_FEEDBACK": "CHANGES_REQUESTED",
    }
    verdict = verdict_aliases.get(verdict, verdict)
    if verdict not in {"APPROVED", "CHANGES_REQUESTED", "BLOCKED"}:
        return {
            "payload": payload,
            "status": status,
            "verdict": verdict,
            "error": f"Codex review contract violation: unsupported verdict `{verdict_value}`",
        }
    return {"payload": payload, "status": status, "verdict": verdict, "error": None}


def _finish_failed(
    store: SQLiteStore,
    workflow_id: str,
    step_id: str,
    retry_count: int,
    artifact_dir: Path,
    output: dict[str, Any],
    error: str,
) -> dict[str, Any]:
    store.finish_step(
        workflow_id=workflow_id,
        step_id=step_id,
        status=StepStatus.FAILED.value,
        output=output,
        artifact_uri=str(artifact_dir),
        error=error,
        retry_count=retry_count,
    )
    return {
        "step_id": step_id,
        "status": StepStatus.FAILED.value,
        "output": output,
        "artifact_uri": str(artifact_dir),
        "error": error,
    }


def _attempt_dir(artifacts_path: str, workflow_id: str, step_id: str, retry_count: int) -> Path:
    artifact_dir = Path(artifacts_path) / workflow_id / step_id / f"attempt-{retry_count + 1}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return artifact_dir


def _replace_placeholders(value: str, payload: dict[str, Any], artifact_dir: Path) -> str:
    return (
        value.replace("{{project_dir}}", payload["project_dir"])
        .replace("{{workspace_path}}", payload["workspace_path"])
        .replace("{{workflow_id}}", payload["workflow_id"])
        .replace("{{step_id}}", payload["step"]["id"])
        .replace("{{artifact_dir}}", str(artifact_dir))
    )


def _sandbox(sandbox_type: Any, value: str) -> Any:
    normalized = value.replace("-", "_")
    if normalized in {"read_only", "workspace_write", "full_access"}:
        return getattr(sandbox_type, normalized)
    if normalized in {"danger_full_access", "danger"}:
        return getattr(sandbox_type, "full_access")
    raise ValueError(f"Unsupported Codex sandbox: {value}")


def _approval_mode(approval_type: Any, value: str) -> Any:
    normalized = value.replace("-", "_")
    if normalized in {"never", "deny_all", "deny"}:
        return getattr(approval_type, "deny_all")
    if normalized in {"auto_review", "on_request", "untrusted"}:
        return getattr(approval_type, "auto_review")
    raise ValueError(f"Unsupported Codex approval mode: {value}")


def _run_turn_with_timeout(turn: Any, timeout_seconds: int) -> Any:
    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            result_queue.put(("result", turn.run()))
        except BaseException as exc:  # noqa: BLE001
            result_queue.put(("error", exc))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        try:
            turn.interrupt()
        except Exception:
            pass
        thread.join(timeout=5)
        raise TimeoutError

    kind, payload = result_queue.get_nowait()
    if kind == "error":
        raise payload
    return payload


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if hasattr(value, "value"):
        return _json_safe(value.value)
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _codex_mode_name() -> str:
    return "stub" if config.CODEX_MODE == "stub" else "sdk"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _workspace_diff(workspace_path: Path) -> str:
    if not (workspace_path / ".git").exists():
        return ""
    try:
        completed = subprocess.run(
            ["git", "diff", "--", "."],
            cwd=workspace_path,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return completed.stderr[:4000]
    return completed.stdout[:20000]


def _codex_review_config(step: dict[str, Any]) -> dict[str, Any]:
    review = step.get("review", False)
    if review in (None, False):
        return {"enabled": False}
    if review is True:
        review = {}
    if not isinstance(review, dict):
        raise ValueError("Codex review config must be a boolean or mapping")

    enabled = bool(review.get("enabled", True))
    if not enabled:
        return {"enabled": False}

    max_review_iterations = review.get("max_review_iterations", 2)
    max_review_iterations = int(max_review_iterations)
    if max_review_iterations < 1:
        raise ValueError("Codex review max_review_iterations must be positive")

    return {
        "enabled": True,
        "max_review_iterations": max_review_iterations,
        "reviewer_sandbox": review.get("reviewer_sandbox", "read-only"),
        "reviewer_approval_mode": review.get("reviewer_approval_mode", "deny_all"),
        "reviewer_timeout_seconds": int(review.get("reviewer_timeout_seconds", 900)),
        "reviewer_model": review.get("reviewer_model"),
        "stub_verdicts": review.get("stub_verdicts"),
    }


def _stub_codex_response(
    step: dict[str, Any],
    step_id: str,
    retry_count: int,
    *,
    role: str = "coder",
    review_iteration: int = 1,
) -> str:
    if role == "reviewer":
        review_config = _codex_review_config(step)
        verdicts = review_config.get("stub_verdicts") or ["APPROVED"]
        verdict = str(verdicts[min(review_iteration - 1, len(verdicts) - 1)])
        verdict = verdict.upper().replace("-", "_")
        findings = []
        required_fixes = []
        if verdict == "CHANGES_REQUESTED":
            findings = [
                {
                    "severity": "medium",
                    "issue": "Stub reviewer requested one repair iteration.",
                    "required_fix": "Run another coder iteration.",
                }
            ]
            required_fixes = ["Run another coder iteration."]
        return json.dumps(
            {
                "status": "SUCCEEDED",
                "verdict": verdict,
                "summary": f"CODEX_STUB_REVIEW_{verdict} for {step_id}",
                "findings": findings,
                "required_fixes": required_fixes,
                "error": None,
                "review_iteration": review_iteration,
            },
            indent=2,
            sort_keys=True,
        )

    return json.dumps(
        {
            "status": "SUCCEEDED",
            "summary": f"CODEX_STUB_DONE for {step_id}",
            "changed_files": [],
            "checks": ["stub mode returned a contract-compliant response"],
            "error": None,
            "attempt": retry_count + 1,
            "review_iteration": review_iteration,
        },
        indent=2,
        sort_keys=True,
    )


def _codex_output_contract(step: dict[str, Any]) -> dict[str, Any]:
    contract = step.get("output_contract", {})
    if contract is None or contract is True or contract == {}:
        return {"type": "status_json"}
    if contract is False:
        raise ValueError("Codex output contracts are required and cannot be disabled")
    if isinstance(contract, str):
        return {"type": contract}
    if isinstance(contract, dict):
        return {"type": "status_json", **contract}
    raise ValueError("output_contract must be a boolean, string, or mapping")


def _evaluate_codex_output_contract(
    step: dict[str, Any],
    final_response: str,
) -> dict[str, Any]:
    contract = _codex_output_contract(step)

    contract_type = str(contract.get("type", "status_json"))
    if contract_type not in {"status_json", "json_status"}:
        raise ValueError(f"Unsupported Codex output contract type: {contract_type}")

    try:
        payload = _extract_json_object(final_response)
    except ValueError as exc:
        return {
            "payload": {},
            "status": None,
            "error": f"Codex output contract violation: {exc}",
        }

    status_field = str(contract.get("status_field", "status"))
    status_value = payload.get(status_field)
    if not isinstance(status_value, str) or not status_value.strip():
        return {
            "payload": payload,
            "status": None,
            "error": f"Codex output contract violation: missing string field `{status_field}`",
        }

    status = status_value.strip().upper().replace("-", "_")
    success_statuses = {
        str(value).upper().replace("-", "_")
        for value in contract.get("success_statuses", ["SUCCEEDED", "SUCCESS", "OK"])
    }
    failure_statuses = {
        str(value).upper().replace("-", "_")
        for value in contract.get(
            "failure_statuses",
            ["FAILED", "FAILURE", "ERROR", "NEEDS_FEEDBACK"],
        )
    }

    required_fields = [str(field) for field in contract.get("required_fields", [])]
    missing_fields = [field for field in required_fields if field not in payload]
    if missing_fields:
        return {
            "payload": payload,
            "status": status,
            "error": (
                "Codex output contract violation: missing required field(s) "
                + ", ".join(f"`{field}`" for field in missing_fields)
            ),
        }

    if status in success_statuses:
        return {"payload": payload, "status": status, "error": None}

    if status in failure_statuses:
        error = payload.get("error") or payload.get("summary") or f"Codex reported {status_value}"
        return {"payload": payload, "status": status, "error": str(error)}

    return {
        "payload": payload,
        "status": status,
        "error": f"Codex output contract violation: unsupported status `{status_value}`",
    }


def _extract_json_object(value: str) -> dict[str, Any]:
    stripped = value.strip()
    if not stripped:
        raise ValueError("empty response; expected a JSON object")

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    for match in re.finditer(r"```(?:json)?\s*(.*?)```", value, re.IGNORECASE | re.DOTALL):
        candidate = match.group(1).strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", value):
        try:
            parsed, _ = decoder.raw_decode(value[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("expected a JSON object with a status field")
