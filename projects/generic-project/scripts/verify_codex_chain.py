#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path


artifact_dir = Path(os.environ["AI_FACTORY_ARTIFACT_DIR"])
workspace_path = Path(os.environ["AI_FACTORY_WORKSPACE_PATH"])
inputs = json.loads(os.environ["AI_FACTORY_INPUTS_JSON"])

target = workspace_path / inputs["chain_file_name"]
content = target.read_text(encoding="utf-8") if target.exists() else ""
first_marker = inputs["codex1_marker"]
second_marker = inputs["codex2_marker"]

checks = {
    "target_exists": target.exists(),
    "first_marker_present": first_marker in content,
    "second_marker_present": second_marker in content,
    "first_marker_before_second": (
        first_marker in content
        and second_marker in content
        and content.index(first_marker) < content.index(second_marker)
    ),
}
payload = {
    "checks": checks,
    "passed": all(checks.values()),
    "target": str(target),
    "content": content,
}
artifact_dir.mkdir(parents=True, exist_ok=True)
(artifact_dir / "verify_codex_chain.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(payload, sort_keys=True))
if not payload["passed"]:
    raise SystemExit(50)
