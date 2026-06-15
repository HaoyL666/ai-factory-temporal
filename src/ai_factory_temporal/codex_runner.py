from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any

from ai_factory_temporal import config


def run_codex_turn(
    *,
    prompt: str,
    prompt_path: Path,
    output_path: Path,
    metadata_path: Path,
    payload: dict[str, Any],
    step: dict[str, Any],
    step_id: str,
    feedback_retry_count: int,
    role: str,
    review_round: int,
    sandbox_value: str,
    approval_mode_value: str,
    model: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    prompt_path.write_text(prompt, encoding="utf-8")

    if config.CODEX_MODE == "stub":
        final_response = stub_codex_response(
            step,
            step_id,
            feedback_retry_count,
            role=role,
            review_round=review_round,
        )
        output_path.write_text(final_response, encoding="utf-8")
        metadata = {
            "runner": "codex",
            "mode": "stub",
            "role": role,
            "review_round": review_round,
            "model": model,
            "items_count": 0,
            "usage": None,
        }
        _write_metadata(metadata_path, metadata)
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
        "review_round": review_round,
        "model": model,
        "thread_id": getattr(thread, "id", None),
        "turn_id": getattr(result, "id", None),
        "duration_ms": getattr(result, "duration_ms", None),
        "items_count": len(getattr(result, "items", []) or []),
        "usage": _json_safe(getattr(result, "usage", None)),
    }
    _write_metadata(metadata_path, metadata)
    return {"final_response": final_response, "metadata": metadata}


def codex_mode_name() -> str:
    return "stub" if config.CODEX_MODE == "stub" else "sdk"


def codex_review_config(step: dict[str, Any]) -> dict[str, Any]:
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

    max_review_rounds = int(review.get("max_review_rounds", 2))
    if max_review_rounds < 1:
        raise ValueError("Codex review max_review_rounds must be positive")

    return {
        "enabled": True,
        "max_review_rounds": max_review_rounds,
        "reviewer_sandbox": review.get("reviewer_sandbox", "read-only"),
        "reviewer_approval_mode": review.get("reviewer_approval_mode", "deny_all"),
        "reviewer_timeout_seconds": int(review.get("reviewer_timeout_seconds", 900)),
        "reviewer_model": review.get("reviewer_model"),
        "stub_verdicts": review.get("stub_verdicts"),
    }


def stub_codex_response(
    step: dict[str, Any],
    step_id: str,
    feedback_retry_count: int,
    *,
    role: str = "coder",
    review_round: int = 1,
) -> str:
    if role == "reviewer":
        review_config = codex_review_config(step)
        verdicts = review_config.get("stub_verdicts") or ["APPROVED"]
        verdict = str(verdicts[min(review_round - 1, len(verdicts) - 1)])
        verdict = verdict.upper().replace("-", "_")
        findings = []
        required_fixes = []
        if verdict == "CHANGES_REQUESTED":
            findings = [
                {
                    "severity": "medium",
                    "issue": "Stub reviewer requested one repair round.",
                    "required_fix": "Run another coder repair round.",
                }
            ]
            required_fixes = ["Run another coder repair round."]
        return json.dumps(
            {
                "status": "SUCCEEDED",
                "verdict": verdict,
                "summary": f"CODEX_STUB_REVIEW_{verdict} for {step_id}",
                "findings": findings,
                "required_fixes": required_fixes,
                "error": None,
                "review_round": review_round,
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
            "step_run_number": feedback_retry_count + 1,
            "review_round": review_round,
        },
        indent=2,
        sort_keys=True,
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


def _write_metadata(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
