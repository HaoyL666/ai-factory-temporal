#!/usr/bin/env python3
from __future__ import annotations

import sys

from _shared import (
    context,
    execution_mode,
    git_changed_files,
    normalize_commands,
    read_makefile_version,
    redaction_note,
    repository_anchors,
    run_commands,
    target_version,
    write_artifact,
    write_git_file,
    write_result,
)


DEFAULT_BUILD_PACKAGE_COMMANDS = [
    ["make", "reviewable"],
    ["make", "build.artifacts"],
]


ctx = context()
artifact_dir = ctx["artifact_dir"]
workspace_path = ctx["workspace_path"]
inputs = ctx["inputs"]
target = target_version(inputs)
mode = execution_mode(inputs)
makefile = read_makefile_version(workspace_path)

write_git_file(artifact_dir, workspace_path, "git-status-before.txt", ["status", "--short"])
commands = normalize_commands(
    inputs.get("build_package_commands"),
    DEFAULT_BUILD_PACKAGE_COMMANDS,
)
command_results = []
if mode == "execute":
    command_results = run_commands(
        workspace_path=workspace_path,
        artifact_dir=artifact_dir,
        commands=commands,
        timeout_seconds=int(inputs.get("build_package_timeout_seconds") or 7200),
        log_prefix="build-package",
    )

write_git_file(artifact_dir, workspace_path, "git-status-after.txt", ["status", "--short"])
write_git_file(
    artifact_dir,
    workspace_path,
    "git-diff-after.diff",
    ["diff", "--no-ext-diff", "--binary", "HEAD", "--", "."],
)

checks = {
    "target_version_provided": bool(target),
    "makefile_exists": bool(makefile["exists"]),
    "makefile_version_found": bool(makefile["version"]),
    "makefile_matches_target": bool(target and makefile["version"] == target),
    "build_package_commands_succeeded": (
        mode != "execute"
        or bool(command_results)
        and all(result["returncode"] == 0 for result in command_results)
    ),
}
payload = {
    "workflow_id": ctx["workflow_id"],
    "step_id": ctx["step_id"],
    "summary": (
        "Build/package evidence planned."
        if mode == "plan_only"
        else "Build/package commands executed and evidence collected."
    ),
    "passed": all(checks.values()),
    "execution_mode": mode,
    "target_version": target,
    "makefile": makefile,
    "repository_anchors": repository_anchors(workspace_path),
    "checks": checks,
    "commands": commands,
    "command_results": command_results,
    "changed_files": git_changed_files(workspace_path),
    "evidence": [
        "git-status-before.txt",
        "git-status-after.txt",
        "git-diff-after.diff",
    ],
    "redaction": redaction_note(),
}
write_artifact(artifact_dir, "build_package_evidence.json", payload)
write_result(artifact_dir, payload)
if not payload["passed"]:
    sys.exit(31)
