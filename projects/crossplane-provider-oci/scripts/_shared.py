from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any


VERSION_RE = re.compile(
    r"^(?:export\s+)?TERRAFORM_PROVIDER_VERSION\s*(?::=|\?=|\+=|=)\s*(?P<version>\S+)",
    re.MULTILINE,
)
RESULT_JSON = "result.json"


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
        "feedback_retry_count": int(os.environ.get("AI_FACTORY_FEEDBACK_RETRY_COUNT", "0")),
        "retry_feedback": json.loads(os.environ.get("AI_FACTORY_RETRY_FEEDBACK_JSON", "{}")),
    }


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_artifact(artifact_dir: Path, name: str, payload: dict[str, Any]) -> str:
    path = artifact_dir / name
    write_json_file(path, payload)
    return str(path)


def write_result(artifact_dir: Path, payload: dict[str, Any]) -> None:
    write_json_file(artifact_dir / RESULT_JSON, payload)
    print(json.dumps(payload, sort_keys=True))


def target_version(inputs: dict[str, Any]) -> str | None:
    value = inputs.get("target_version") or inputs.get("terraform_provider_version")
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def execution_mode(inputs: dict[str, Any]) -> str:
    value = str(inputs.get("execution_mode") or "plan_only").strip().lower()
    if value in {"execute", "run", "real"}:
        return "execute"
    return "plan_only"


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


def repository_anchors(workspace_path: Path) -> dict[str, bool]:
    return existing_paths(
        workspace_path,
        [
            "Makefile",
            "config",
            "config/schema.json",
            "apis",
            "internal/apis",
            "internal/controller",
            "package/crds",
            "examples-generated",
            "docs",
        ],
    )


def existing_paths(workspace_path: Path, paths: list[str]) -> dict[str, bool]:
    return {path: (workspace_path / path).exists() for path in paths}


def git_output(
    workspace_path: Path,
    args: list[str],
    *,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    if not (workspace_path / ".git").exists():
        return {
            "command": ["git", *args],
            "returncode": None,
            "stdout": "",
            "stderr": "workspace is not a Git worktree",
        }
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=workspace_path,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "command": ["git", *args],
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
        }
    return {
        "command": ["git", *args],
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def write_git_file(
    artifact_dir: Path,
    workspace_path: Path,
    name: str,
    args: list[str],
) -> dict[str, Any]:
    result = git_output(workspace_path, args)
    path = artifact_dir / name
    path.write_text(result["stdout"], encoding="utf-8")
    if result["stderr"]:
        (artifact_dir / f"{name}.stderr.log").write_text(result["stderr"], encoding="utf-8")
    return {
        "path": str(path),
        "command": result["command"],
        "returncode": result["returncode"],
    }


def git_changed_files(workspace_path: Path) -> list[str]:
    result = git_output(workspace_path, ["status", "--porcelain=v1"])
    if result["returncode"] != 0:
        return []
    changed: list[str] = []
    for line in result["stdout"].splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if line.startswith("?? ") and path.endswith("/"):
            changed.extend(_untracked_directory_files(workspace_path, path))
        else:
            changed.append(path)
    return sorted(set(changed))


def _untracked_directory_files(workspace_path: Path, relative_dir: str) -> list[str]:
    directory = workspace_path / relative_dir
    if not directory.is_dir():
        return [relative_dir]
    files: list[str] = []
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            files.append(path.relative_to(workspace_path).as_posix())
    return files or [relative_dir]


def normalize_commands(value: Any, default: list[list[str]]) -> list[list[str]]:
    if value is None:
        return default
    if isinstance(value, str):
        return [shlex.split(value)]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [value]
    if isinstance(value, list) and all(
        isinstance(item, list) and all(isinstance(part, str) for part in item)
        for item in value
    ):
        return value
    raise ValueError("command input must be a string, argv list, or list of argv lists")


def run_commands(
    *,
    workspace_path: Path,
    artifact_dir: Path,
    commands: list[list[str]],
    timeout_seconds: int,
    log_prefix: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, command in enumerate(commands, start=1):
        results.append(
            run_command(
                workspace_path=workspace_path,
                artifact_dir=artifact_dir,
                command=command,
                timeout_seconds=timeout_seconds,
                log_prefix=f"{log_prefix}-{index}",
            )
        )
        if results[-1]["returncode"] != 0:
            break
    return results


def run_command(
    *,
    workspace_path: Path,
    artifact_dir: Path,
    command: list[str],
    timeout_seconds: int,
    log_prefix: str,
) -> dict[str, Any]:
    started = time.monotonic()
    stdout_path = artifact_dir / f"{log_prefix}.stdout.log"
    stderr_path = artifact_dir / f"{log_prefix}.stderr.log"
    try:
        completed = subprocess.run(
            command,
            cwd=workspace_path,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        return {
            "command": command,
            "returncode": completed.returncode,
            "timed_out": False,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        }
    except subprocess.TimeoutExpired as exc:
        stdout_path.write_text(_stream(exc.stdout), encoding="utf-8")
        stderr_path.write_text(_stream(exc.stderr), encoding="utf-8")
        return {
            "command": command,
            "returncode": None,
            "timed_out": True,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        }


def redaction_note() -> str:
    return "Artifacts exclude raw credentials, kubeconfigs, tokens, and tenancy-sensitive values."


def _stream(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
