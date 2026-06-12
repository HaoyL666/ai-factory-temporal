from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


VERSION_RE = re.compile(r"^TERRAFORM_PROVIDER_VERSION\s*:?=\s*(?P<version>\S+)", re.MULTILINE)


def context() -> dict[str, Any]:
    artifact_dir = Path(os.environ["AI_FACTORY_ARTIFACT_DIR"])
    workspace_path = Path(os.environ["AI_FACTORY_WORKSPACE_PATH"])
    inputs = json.loads(os.environ["AI_FACTORY_INPUTS_JSON"])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return {
        "artifact_dir": artifact_dir,
        "workspace_path": workspace_path,
        "inputs": inputs,
        "workflow_id": os.environ["AI_FACTORY_WORKFLOW_ID"],
        "step_id": os.environ["AI_FACTORY_STEP_ID"],
        "retry_count": int(os.environ.get("AI_FACTORY_RETRY_COUNT", "0")),
        "retry_feedback": json.loads(os.environ.get("AI_FACTORY_RETRY_FEEDBACK_JSON", "{}")),
    }


def write_json(artifact_dir: Path, name: str, payload: dict[str, Any]) -> None:
    (artifact_dir / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, sort_keys=True))


def target_version(inputs: dict[str, Any]) -> str | None:
    value = inputs.get("target_version") or inputs.get("terraform_provider_version")
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def validation_level(inputs: dict[str, Any]) -> str:
    return str(inputs.get("validation_level") or "L1_BUILD_GENERATE")


def read_makefile_version(workspace_path: Path) -> dict[str, Any]:
    makefile = workspace_path / "Makefile"
    if not makefile.exists():
        return {
            "path": str(makefile),
            "exists": False,
            "version": None,
        }
    content = makefile.read_text(encoding="utf-8", errors="replace")
    match = VERSION_RE.search(content)
    return {
        "path": str(makefile),
        "exists": True,
        "version": match.group("version") if match else None,
    }


def git_lines(workspace_path: Path, args: list[str]) -> list[str]:
    if not (workspace_path / ".git").exists():
        return []
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=workspace_path,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    return [line for line in completed.stdout.splitlines() if line.strip()]


def existing_paths(workspace_path: Path, paths: list[str]) -> dict[str, bool]:
    return {path: (workspace_path / path).exists() for path in paths}


def redaction_note() -> str:
    return "Artifacts intentionally exclude raw credentials, kubeconfigs, tokens, and tenancy-sensitive values."
