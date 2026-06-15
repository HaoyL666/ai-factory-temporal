from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from temporalio import activity

from ai_factory_temporal import config
from ai_factory_temporal.artifacts import (
    ARTIFACT_MANIFEST_FILENAME,
    SCRIPT_RESULT_FILENAME,
    STEP_RESULT_FILENAME,
    build_artifact_manifest,
    load_optional_json_object,
    write_json as write_artifact_json,
)
from ai_factory_temporal.codex_contracts import (
    as_list as _as_list,
    codex_output_contract as _codex_output_contract,
    evaluate_codex_output_contract as _evaluate_codex_output_contract,
    evaluate_codex_review_contract as _evaluate_codex_review_contract,
)
from ai_factory_temporal.codex_runner import (
    codex_mode_name as _codex_mode_name,
    codex_review_config as _codex_review_config,
    run_codex_turn as _run_codex_turn,
)
from ai_factory_temporal.models import StepStatus
from ai_factory_temporal.prompting import render_prompt
from ai_factory_temporal.review_prompts import (
    build_repair_prompt as _build_repair_prompt,
    build_reviewer_prompt as _build_reviewer_prompt,
    repair_context as _repair_context,
)
from ai_factory_temporal.store import SQLiteStore
from ai_factory_temporal.usage import estimate_usage_cost, normalize_token_usage


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
        feedback_retry_count=_feedback_retry_count(payload),
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
        feedback_retry_count=_feedback_retry_count(payload),
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
    feedback_retry_count = _feedback_retry_count(payload)
    workspace_path = Path(payload["workspace_path"])
    artifact_dir = _step_run_dir(
        payload["artifacts_path"],
        workflow_id,
        step_id,
        feedback_retry_count,
    )

    store.mark_step_running(workflow_id, step_id, feedback_retry_count)
    command = step.get("command")
    if not command:
        raise ValueError("script step is missing command")

    resolved_command = [
        _replace_placeholders(str(part), payload, artifact_dir)
        for part in command
    ]
    cwd = Path(
        _replace_placeholders(str(step.get("cwd", "{{workspace_path}}")), payload, artifact_dir)
    )
    env = os.environ.copy()
    env.update(
        {
            "AI_FACTORY_WORKFLOW_ID": workflow_id,
            "AI_FACTORY_STEP_ID": step_id,
            "AI_FACTORY_ARTIFACT_DIR": str(artifact_dir),
            "AI_FACTORY_INPUTS_JSON": json.dumps(payload["inputs"], sort_keys=True),
            "AI_FACTORY_WORKSPACE_PATH": str(workspace_path),
            "AI_FACTORY_FEEDBACK_RETRY_COUNT": str(feedback_retry_count),
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
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_path.write_text(_timeout_stream(exc.stdout), encoding="utf-8")
        stderr_path.write_text(_timeout_stream(exc.stderr), encoding="utf-8")
        error = f"script timed out after {timeout_seconds} seconds"
        output = _script_step_output(
            step=step,
            status=StepStatus.FAILED.value,
            artifact_dir=artifact_dir,
            command=resolved_command,
            returncode=None,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            timeout_seconds=timeout_seconds,
            error=error,
        )
        return _finish_failed(
            store,
            workflow_id,
            step_id,
            feedback_retry_count,
            artifact_dir,
            output,
            error,
        )

    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        error = f"script failed with exit code {completed.returncode}"
        output = _script_step_output(
            step=step,
            status=StepStatus.FAILED.value,
            artifact_dir=artifact_dir,
            command=resolved_command,
            returncode=completed.returncode,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            timeout_seconds=timeout_seconds,
            error=error,
        )
        return _finish_failed(
            store,
            workflow_id,
            step_id,
            feedback_retry_count,
            artifact_dir,
            output,
            error,
        )

    output = _script_step_output(
        step=step,
        status=StepStatus.SUCCEEDED.value,
        artifact_dir=artifact_dir,
        command=resolved_command,
        returncode=completed.returncode,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        timeout_seconds=timeout_seconds,
        error=None,
    )
    store.finish_step(
        workflow_id=workflow_id,
        step_id=step_id,
        status=StepStatus.SUCCEEDED.value,
        output=output,
        artifact_uri=str(artifact_dir),
        feedback_retry_count=feedback_retry_count,
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
    feedback_retry_count = _feedback_retry_count(payload)
    step_run_number = feedback_retry_count + 1
    artifact_dir = _step_run_dir(
        payload["artifacts_path"],
        workflow_id,
        step_id,
        feedback_retry_count,
    )

    store.mark_step_running(workflow_id, step_id, feedback_retry_count)
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
        step_run_number=step_run_number,
    )

    if review_config["enabled"]:
        return _run_reviewed_codex_step(
            store=store,
            payload=payload,
            step=step,
            workflow_id=workflow_id,
            step_id=step_id,
            feedback_retry_count=feedback_retry_count,
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
        feedback_retry_count=feedback_retry_count,
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
    feedback_retry_count: int,
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
            feedback_retry_count=feedback_retry_count,
            role="coder",
            review_round=1,
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
            feedback_retry_count,
            artifact_dir,
            {
                "runner": "codex",
                "mode": _codex_mode_name(),
                "timeout_seconds": int(step.get("timeout_seconds", 1800)),
            },
            f"Codex timed out after {int(step.get('timeout_seconds', 1800))} seconds",
        )

    usage_record = _record_codex_usage(
        store=store,
        payload=payload,
        step=step,
        metadata=turn_output["metadata"],
        role="coder",
        review_round=1,
        feedback_retry_count=feedback_retry_count,
        artifact_dir=artifact_dir,
    )
    final_response = turn_output["final_response"]
    output = {
        "runner": "codex",
        "kind": "codex",
        "status": StepStatus.SUCCEEDED.value,
        "mode": turn_output["metadata"]["mode"],
        "summary": final_response[:2000],
        "prompt": str(prompt_path),
        "final_response": str(output_path),
        "metadata": str(metadata_path),
        "thread_id": turn_output["metadata"].get("thread_id"),
        "turn_id": turn_output["metadata"].get("turn_id"),
        "usage": usage_record["usage"],
        "estimated_cost": usage_record["estimated_cost"],
        "cost_currency": usage_record["cost_currency"],
    }
    contract_result = _evaluate_codex_output_contract(step, final_response)
    output["contract"] = contract_result["payload"]
    output["contract_status"] = contract_result.get("status")
    if isinstance(contract_result["payload"].get("summary"), str):
        output["summary"] = contract_result["payload"]["summary"]
    if contract_result.get("error"):
        return _finish_failed(
            store,
            workflow_id,
            step_id,
            feedback_retry_count,
            artifact_dir,
            output,
            contract_result["error"],
        )
    output["artifact_uri"] = str(artifact_dir)
    output["step_result"] = str(artifact_dir / STEP_RESULT_FILENAME)
    _write_json(artifact_dir / STEP_RESULT_FILENAME, output)
    store.finish_step(
        workflow_id=workflow_id,
        step_id=step_id,
        status=StepStatus.SUCCEEDED.value,
        output=output,
        artifact_uri=str(artifact_dir),
        feedback_retry_count=feedback_retry_count,
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
    feedback_retry_count: int,
    artifact_dir: Path,
    initial_prompt: str,
    review_config: dict[str, Any],
) -> dict[str, Any]:
    review_history: list[dict[str, Any]] = []
    coder_prompt = initial_prompt
    repair_context: dict[str, Any] | None = None
    max_review_rounds = int(review_config["max_review_rounds"])

    for review_round in range(1, max_review_rounds + 1):
        review_round_dir = artifact_dir / f"review-round-{review_round}"
        review_round_dir.mkdir(parents=True, exist_ok=True)

        coder_prompt_path = review_round_dir / "coder-prompt.md"
        coder_output_path = review_round_dir / "coder-final-response.md"
        coder_metadata_path = review_round_dir / "coder-sdk-result.json"
        coder_contract_path = review_round_dir / "coder-contract.json"
        reviewer_prompt_path = review_round_dir / "reviewer-prompt.md"
        reviewer_output_path = review_round_dir / "reviewer-final-response.md"
        reviewer_metadata_path = review_round_dir / "reviewer-sdk-result.json"
        reviewer_contract_path = review_round_dir / "reviewer-contract.json"

        try:
            coder_turn = _run_codex_turn(
                prompt=coder_prompt,
                prompt_path=coder_prompt_path,
                output_path=coder_output_path,
                metadata_path=coder_metadata_path,
                payload=payload,
                step=step,
                step_id=step_id,
                feedback_retry_count=feedback_retry_count,
                role="coder",
                review_round=review_round,
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
                feedback_retry_count=feedback_retry_count,
                artifact_dir=artifact_dir,
                review_history=review_history,
                error=(
                    "Codex coder timed out after "
                    f"{int(step.get('timeout_seconds', 1800))} seconds"
                ),
            )

        coder_usage = _record_codex_usage(
            store=store,
            payload=payload,
            step=step,
            metadata=coder_turn["metadata"],
            role="coder",
            review_round=review_round,
            feedback_retry_count=feedback_retry_count,
            artifact_dir=artifact_dir,
        )
        coder_contract = _evaluate_codex_output_contract(step, coder_turn["final_response"])
        _write_json(coder_contract_path, coder_contract)
        if coder_contract.get("error"):
            return _finish_reviewed_codex_failure(
                store=store,
                workflow_id=workflow_id,
                step_id=step_id,
                feedback_retry_count=feedback_retry_count,
                artifact_dir=artifact_dir,
                review_history=review_history,
                error=str(coder_contract["error"]),
                coder_contract=coder_contract,
            )

        reviewer_prompt = _build_reviewer_prompt(
            original_prompt=initial_prompt,
            review_round=review_round,
            coder_prompt_path=coder_prompt_path,
            repair_context=repair_context,
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
                feedback_retry_count=feedback_retry_count,
                role="reviewer",
                review_round=review_round,
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
                feedback_retry_count=feedback_retry_count,
                artifact_dir=artifact_dir,
                review_history=review_history,
                error=(
                    "Codex reviewer timed out after "
                    f"{int(review_config['reviewer_timeout_seconds'])} seconds"
                ),
                coder_contract=coder_contract,
            )

        reviewer_usage = _record_codex_usage(
            store=store,
            payload=payload,
            step=step,
            metadata=reviewer_turn["metadata"],
            role="reviewer",
            review_round=review_round,
            feedback_retry_count=feedback_retry_count,
            artifact_dir=artifact_dir,
        )
        review_contract = _evaluate_codex_review_contract(reviewer_turn["final_response"])
        _write_json(reviewer_contract_path, review_contract)
        history_entry = {
            "review_round": review_round,
            "verdict": review_contract.get("verdict"),
            "coder_summary": coder_contract["payload"].get("summary"),
            "reviewer_summary": review_contract["payload"].get("summary"),
            "findings": _as_list(review_contract["payload"].get("findings")),
            "required_fixes": _as_list(review_contract["payload"].get("required_fixes")),
            "coder_contract": str(coder_contract_path),
            "reviewer_contract": str(reviewer_contract_path),
            "coder_prompt": str(coder_prompt_path),
            "coder_response": str(coder_output_path),
            "reviewer_prompt": str(reviewer_prompt_path),
            "reviewer_response": str(reviewer_output_path),
            "coder_usage": coder_usage["usage"],
            "coder_estimated_cost": coder_usage["estimated_cost"],
            "reviewer_usage": reviewer_usage["usage"],
            "reviewer_estimated_cost": reviewer_usage["estimated_cost"],
        }
        review_history.append(history_entry)

        if review_contract.get("error"):
            return _finish_reviewed_codex_failure(
                store=store,
                workflow_id=workflow_id,
                step_id=step_id,
                feedback_retry_count=feedback_retry_count,
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
                feedback_retry_count=feedback_retry_count,
                artifact_dir=artifact_dir,
                review_history=review_history,
                coder_contract=coder_contract,
                reviewer_contract=review_contract,
            )

        if (
            review_contract["verdict"] == "CHANGES_REQUESTED"
            and review_round < max_review_rounds
        ):
            repair_context = _repair_context(
                review_round=review_round + 1,
                previous_review_round=review_round,
                previous_coder_contract=coder_contract["payload"],
                reviewer_feedback=review_contract["payload"],
            )
            coder_prompt = _build_repair_prompt(
                original_prompt=initial_prompt,
                review_history=review_history,
                repair_context=repair_context,
            )
            continue

        return _finish_reviewed_codex_failure(
            store=store,
            workflow_id=workflow_id,
            step_id=step_id,
            feedback_retry_count=feedback_retry_count,
            artifact_dir=artifact_dir,
            review_history=review_history,
            error=(
                "Codex review rejected final round: "
                f"{review_contract['payload'].get('summary') or review_contract['verdict']}"
            ),
            coder_contract=coder_contract,
            reviewer_contract=review_contract,
        )

    return _finish_reviewed_codex_failure(
        store=store,
        workflow_id=workflow_id,
        step_id=step_id,
        feedback_retry_count=feedback_retry_count,
        artifact_dir=artifact_dir,
        review_history=review_history,
        error="Codex review loop ended without an approval verdict",
    )


def _finish_reviewed_codex_success(
    *,
    store: SQLiteStore,
    workflow_id: str,
    step_id: str,
    feedback_retry_count: int,
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
        feedback_retry_count=feedback_retry_count,
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
    feedback_retry_count: int,
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
        feedback_retry_count,
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
    review_rounds = len(review_history)

    if status == "SUCCEEDED":
        summary = (
            f"Codex step passed review after {review_rounds} review round(s). "
            f"{coder_payload.get('summary', '')}"
        ).strip()
    else:
        summary = f"Codex step failed review after {review_rounds} review round(s)."

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
        checks.append("reviewer approved final review round")

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
            "review_rounds": review_rounds,
            "repair_rounds": max(0, review_rounds - 1),
            "history": [
                {
                    "review_round": entry["review_round"],
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
        "kind": "codex",
        "status": final_contract["status"],
        "mode": _codex_mode_name(),
        "summary": final_contract["summary"],
        "contract": final_contract,
        "contract_status": final_contract["status"],
        "review": final_contract["review"],
        "review_history": review_history,
        "artifact_uri": str(artifact_dir),
        "step_result": str(artifact_dir / "step-result.json"),
    }


def _record_codex_usage(
    *,
    store: SQLiteStore,
    payload: dict[str, Any],
    step: dict[str, Any],
    metadata: dict[str, Any],
    role: str,
    review_round: int,
    feedback_retry_count: int,
    artifact_dir: Path,
) -> dict[str, Any]:
    raw_usage = metadata.get("usage") if isinstance(metadata, dict) else None
    usage = normalize_token_usage(raw_usage)
    estimated_cost = estimate_usage_cost(
        usage,
        input_price_per_1m=config.INPUT_TOKEN_PRICE_PER_1M,
        output_price_per_1m=config.OUTPUT_TOKEN_PRICE_PER_1M,
        cache_read_price_per_1m=config.CACHE_READ_TOKEN_PRICE_PER_1M,
        cache_creation_price_per_1m=config.CACHE_CREATION_TOKEN_PRICE_PER_1M,
    )
    return store.record_codex_usage(
        workflow_id=payload["workflow_id"],
        step_id=step["id"],
        step_run_number=feedback_retry_count + 1,
        role=role,
        review_round=review_round,
        mode=str(metadata.get("mode") or _codex_mode_name()),
        model=metadata.get("model") or step.get("model") or config.CODEX_MODEL,
        thread_id=metadata.get("thread_id"),
        turn_id=metadata.get("turn_id"),
        usage=usage,
        raw_usage=raw_usage,
        metadata=metadata,
        estimated_cost=estimated_cost,
        cost_currency=config.COST_CURRENCY,
        artifact_uri=str(artifact_dir),
    )


def _script_step_output(
    *,
    step: dict[str, Any],
    status: str,
    artifact_dir: Path,
    command: list[str],
    returncode: int | None,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: int,
    error: str | None,
) -> dict[str, Any]:
    script_result, script_result_error = load_optional_json_object(
        artifact_dir / SCRIPT_RESULT_FILENAME
    )
    manifest = build_artifact_manifest(
        artifact_dir,
        exclude={ARTIFACT_MANIFEST_FILENAME, STEP_RESULT_FILENAME},
    )
    manifest_path = artifact_dir / ARTIFACT_MANIFEST_FILENAME
    write_artifact_json(manifest_path, manifest)

    output: dict[str, Any] = {
        "runner": step.get("runner", "script"),
        "kind": step.get("kind", "deterministic"),
        "status": status,
        "summary": _script_step_summary(
            step=step,
            status=status,
            returncode=returncode,
            script_result=script_result,
            error=error,
        ),
        "command": command,
        "returncode": returncode,
        "timeout_seconds": timeout_seconds,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "artifact_uri": str(artifact_dir),
        "artifact_manifest": str(manifest_path),
        "artifacts": manifest["files"],
        "step_result": str(artifact_dir / STEP_RESULT_FILENAME),
    }
    if script_result is not None:
        output["script_result"] = script_result
    if script_result_error:
        output["script_result_error"] = script_result_error
    if error:
        output["error"] = error

    write_artifact_json(artifact_dir / STEP_RESULT_FILENAME, output)
    return output


def _script_step_summary(
    *,
    step: dict[str, Any],
    status: str,
    returncode: int | None,
    script_result: dict[str, Any] | None,
    error: str | None,
) -> str:
    if script_result is not None and isinstance(script_result.get("summary"), str):
        return str(script_result["summary"])
    if status == StepStatus.SUCCEEDED.value:
        return f"Script step `{step['id']}` succeeded with exit code {returncode}."
    return error or f"Script step `{step['id']}` failed."


def _finish_failed(
    store: SQLiteStore,
    workflow_id: str,
    step_id: str,
    feedback_retry_count: int,
    artifact_dir: Path,
    output: dict[str, Any],
    error: str,
) -> dict[str, Any]:
    output.setdefault("status", StepStatus.FAILED.value)
    output.setdefault("summary", error)
    output.setdefault("artifact_uri", str(artifact_dir))
    output.setdefault("step_result", str(artifact_dir / STEP_RESULT_FILENAME))
    _write_json(artifact_dir / STEP_RESULT_FILENAME, output)
    store.finish_step(
        workflow_id=workflow_id,
        step_id=step_id,
        status=StepStatus.FAILED.value,
        output=output,
        artifact_uri=str(artifact_dir),
        error=error,
        feedback_retry_count=feedback_retry_count,
    )
    return {
        "step_id": step_id,
        "status": StepStatus.FAILED.value,
        "output": output,
        "artifact_uri": str(artifact_dir),
        "error": error,
    }


def _step_run_dir(
    artifacts_path: str,
    workflow_id: str,
    step_id: str,
    feedback_retry_count: int,
) -> Path:
    artifact_dir = (
        Path(artifacts_path)
        / workflow_id
        / step_id
        / f"step-run-{feedback_retry_count + 1}"
    )
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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _feedback_retry_count(payload: dict[str, Any]) -> int:
    return int(payload.get("feedback_retry_count", 0) or 0)


def _timeout_stream(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
