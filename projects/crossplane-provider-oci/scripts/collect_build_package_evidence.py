#!/usr/bin/env python3
from __future__ import annotations

import sys

from _shared import (
    context,
    existing_paths,
    git_lines,
    read_makefile_version,
    redaction_note,
    target_version,
    validation_level,
    write_json,
)


ctx = context()
artifact_dir = ctx["artifact_dir"]
workspace_path = ctx["workspace_path"]
inputs = ctx["inputs"]
target = target_version(inputs)
makefile = read_makefile_version(workspace_path)
changed_files = git_lines(workspace_path, ["diff", "--name-only"])
anchors = existing_paths(
    workspace_path,
    [
        "Makefile",
        "package",
        "package/crds",
        "examples-generated",
        ".github/workflows",
    ],
)
execution_mode = str(inputs.get("execution_mode") or "plan_only")
checks = {
    "target_version_provided": bool(target),
    "makefile_exists": bool(makefile["exists"]),
    "makefile_version_found": bool(makefile["version"]),
    "makefile_matches_target": bool(target and makefile["version"] == target),
    "plan_only_mode": execution_mode == "plan_only",
}
payload = {
    "workflow_id": ctx["workflow_id"],
    "step_id": ctx["step_id"],
    "passed": all(checks.values()),
    "checks": checks,
    "target_version": target,
    "validation_level": validation_level(inputs),
    "execution_mode": execution_mode,
    "makefile": makefile,
    "repository_anchors": anchors,
    "changed_files": changed_files,
    "planned_build_test_package_commands": [
        "make build",
        "make test",
        "make package",
    ],
    "artifact_expectations": [
        "build logs",
        "test logs",
        "package paths",
        "provider package or image references",
        "checksums or provenance when available",
    ],
    "redaction": redaction_note(),
}
write_json(artifact_dir, "build_package_evidence.json", payload)
if not payload["passed"]:
    sys.exit(31)
