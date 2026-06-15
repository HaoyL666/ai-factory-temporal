from __future__ import annotations

import json
import re
from typing import Any


SUCCESS_STATUSES = {"SUCCEEDED", "SUCCESS", "OK"}
FAILURE_STATUSES = {"FAILED", "FAILURE", "ERROR", "NEEDS_FEEDBACK"}
APPROVED_VERDICTS = {"APPROVED", "CHANGES_REQUESTED", "BLOCKED"}
VERDICT_ALIASES = {
    "PASS": "APPROVED",
    "PASSED": "APPROVED",
    "ACCEPTED": "APPROVED",
    "APPROVE": "APPROVED",
    "REJECTED": "CHANGES_REQUESTED",
    "NEEDS_CHANGES": "CHANGES_REQUESTED",
    "NEEDS_FEEDBACK": "CHANGES_REQUESTED",
}


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def codex_output_contract(step: dict[str, Any]) -> dict[str, Any]:
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


def evaluate_codex_output_contract(
    step: dict[str, Any],
    final_response: str,
) -> dict[str, Any]:
    contract = codex_output_contract(step)

    contract_type = str(contract.get("type", "status_json"))
    if contract_type not in {"status_json", "json_status"}:
        raise ValueError(f"Unsupported Codex output contract type: {contract_type}")

    try:
        payload = extract_json_object(final_response)
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

    status = normalize_status(status_value)
    success_statuses = {
        normalize_status(value)
        for value in contract.get("success_statuses", sorted(SUCCESS_STATUSES))
    }
    failure_statuses = {
        normalize_status(value)
        for value in contract.get("failure_statuses", sorted(FAILURE_STATUSES))
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


def evaluate_codex_review_contract(final_response: str) -> dict[str, Any]:
    try:
        payload = extract_json_object(final_response)
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
    status = normalize_status(status_value)
    if status not in SUCCESS_STATUSES:
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
    verdict = VERDICT_ALIASES.get(normalize_status(verdict_value), normalize_status(verdict_value))
    if verdict not in APPROVED_VERDICTS:
        return {
            "payload": payload,
            "status": status,
            "verdict": verdict,
            "error": f"Codex review contract violation: unsupported verdict `{verdict_value}`",
        }
    return {"payload": payload, "status": status, "verdict": verdict, "error": None}


def extract_json_object(value: str) -> dict[str, Any]:
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


def normalize_status(value: Any) -> str:
    return str(value).strip().upper().replace("-", "_")
