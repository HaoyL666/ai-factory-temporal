#!/usr/bin/env python3
from __future__ import annotations

import sys

from _shared import (
    context,
    execution_mode,
    normalize_commands,
    read_makefile_version,
    redaction_note,
    run_commands,
    target_version,
    write_artifact,
    write_result,
)


ctx = context()
artifact_dir = ctx["artifact_dir"]
workspace_path = ctx["workspace_path"]
inputs = ctx["inputs"]
mode = execution_mode(inputs)
commands = normalize_commands(inputs.get("publish_commands"), [])
command_results = []
if mode == "execute" and commands:
    command_results = run_commands(
        workspace_path=workspace_path,
        artifact_dir=artifact_dir,
        commands=commands,
        timeout_seconds=int(inputs.get("publish_timeout_seconds") or 1800),
        log_prefix="publish",
    )

checks = {
    "approval_recorded": True,
    "publish_command_configured_if_execute": mode != "execute" or bool(commands),
    "publish_commands_succeeded": (
        mode != "execute"
        or bool(command_results)
        and all(result["returncode"] == 0 for result in command_results)
    ),
}
payload = {
    "workflow_id": ctx["workflow_id"],
    "step_id": ctx["step_id"],
    "summary": (
        "Publish approval recorded."
        if not commands
        else "Publish approval recorded and configured commands evaluated."
    ),
    "passed": all(checks.values()),
    "gate": "publish",
    "execution_mode": mode,
    "target_version": target_version(inputs),
    "registry_destination": inputs.get("registry_destination"),
    "makefile": read_makefile_version(workspace_path),
    "checks": checks,
    "commands": commands,
    "command_results": command_results,
    "redaction": redaction_note(),
}
write_artifact(artifact_dir, "publish_gate.json", payload)
write_result(artifact_dir, payload)
if not payload["passed"]:
    sys.exit(40)
