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
    prompt_path = artifact_dir / "codex-prompt.md"
    output_path = artifact_dir / "codex-final-response.md"
    metadata_path = artifact_dir / "codex-sdk-result.json"
    prompt_path.write_text(prompt, encoding="utf-8")

    if config.CODEX_MODE == "stub":
        final_response = _stub_codex_response(step, step_id, retry_count)
        output_path.write_text(final_response, encoding="utf-8")
        metadata = {"runner": "codex", "mode": "stub", "items_count": 0}
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        output = {
            "runner": "codex",
            "mode": "stub",
            "summary": final_response,
            "prompt": str(prompt_path),
            "final_response": str(output_path),
            "metadata": str(metadata_path),
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

    try:
        from openai_codex import ApprovalMode, Codex, Sandbox
    except ImportError as exc:
        raise RuntimeError("openai-codex is not installed") from exc

    model = step.get("model") or config.CODEX_MODEL
    sandbox = _sandbox(Sandbox, str(step.get("sandbox", config.CODEX_SANDBOX)))
    approval_mode = _approval_mode(
        ApprovalMode,
        str(step.get("codex_approval_mode", config.CODEX_APPROVAL_MODE)),
    )
    timeout_seconds = int(step.get("timeout_seconds", 1800))

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
        return _finish_failed(
            store,
            workflow_id,
            step_id,
            retry_count,
            artifact_dir,
            {"runner": "codex", "mode": "sdk", "timeout_seconds": timeout_seconds},
            f"Codex timed out after {timeout_seconds} seconds",
        )

    final_response = getattr(result, "final_response", None) or ""
    output_path.write_text(final_response, encoding="utf-8")
    metadata = {
        "runner": "codex",
        "mode": "sdk",
        "thread_id": getattr(thread, "id", None),
        "turn_id": getattr(result, "id", None),
        "duration_ms": getattr(result, "duration_ms", None),
        "items_count": len(getattr(result, "items", []) or []),
        "usage": _json_safe(getattr(result, "usage", None)),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output = {
        "runner": "codex",
        "mode": "sdk",
        "summary": final_response[:2000],
        "prompt": str(prompt_path),
        "final_response": str(output_path),
        "metadata": str(metadata_path),
        "thread_id": metadata["thread_id"],
        "turn_id": metadata["turn_id"],
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


def _stub_codex_response(step: dict[str, Any], step_id: str, retry_count: int) -> str:
    return json.dumps(
        {
            "status": "SUCCEEDED",
            "summary": f"CODEX_STUB_DONE for {step_id}",
            "changed_files": [],
            "checks": ["stub mode returned a contract-compliant response"],
            "error": None,
            "attempt": retry_count + 1,
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
