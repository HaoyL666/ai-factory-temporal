#!/usr/bin/env python3
from __future__ import annotations

import sys

from _shared import (
    context,
    execution_mode,
    normalize_commands,
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
commands = normalize_commands(inputs.get("install_validation_commands"), [])
command_results = []
if mode == "execute" and commands:
    command_results = run_commands(
        workspace_path=workspace_path,
        artifact_dir=artifact_dir,
        commands=commands,
        timeout_seconds=int(inputs.get("install_validation_timeout_seconds") or 3600),
        log_prefix="install-validation",
    )

checks = {
    "approval_recorded": True,
    "install_command_configured_if_execute": mode != "execute" or bool(commands),
    "install_commands_succeeded": (
        mode != "execute"
        or bool(command_results)
        and all(result["returncode"] == 0 for result in command_results)
    ),
}
payload = {
    "workflow_id": ctx["workflow_id"],
    "step_id": ctx["step_id"],
    "summary": (
        "Install validation approval recorded."
        if not commands
        else "Install validation approval recorded and configured commands evaluated."
    ),
    "passed": all(checks.values()),
    "gate": "install_validation",
    "execution_mode": mode,
    "target_version": target_version(inputs),
    "cluster_context": inputs.get("cluster_context"),
    "package_reference": inputs.get("package_reference"),
    "checks": checks,
    "commands": commands,
    "command_results": command_results,
    "redaction": redaction_note(),
}
write_artifact(artifact_dir, "install_validation_gate.json", payload)
write_result(artifact_dir, payload)
if not payload["passed"]:
    sys.exit(41)
