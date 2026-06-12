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
status = git_lines(workspace_path, ["status", "--short"])
anchors = existing_paths(
    workspace_path,
    [
        "Makefile",
        "config/schema.json",
        "config",
        "apis",
        "internal/controller",
        "package/crds",
        "examples-generated",
        "docs",
        ".github/workflows",
    ],
)

checks = {
    "target_version_provided": bool(target),
    "makefile_exists": bool(makefile["exists"]),
    "makefile_version_found": bool(makefile["version"]),
    "makefile_matches_target": bool(target and makefile["version"] == target),
}
payload = {
    "workflow_id": ctx["workflow_id"],
    "step_id": ctx["step_id"],
    "passed": all(checks.values()),
    "checks": checks,
    "target_version": target,
    "validation_level": validation_level(inputs),
    "makefile": makefile,
    "repository_anchors": anchors,
    "changed_files": changed_files,
    "git_status": status,
    "planned_generation_commands": [
        "make provider.schema",
        "make provider.docs",
        "make provider.generate",
        "make provider.resolve",
    ],
    "evidence": [
        "source-of-truth version check",
        "repository anchor inventory",
        "git diff file list",
    ],
    "redaction": redaction_note(),
}
write_json(artifact_dir, "generation_evidence.json", payload)
if not payload["passed"]:
    sys.exit(30)
