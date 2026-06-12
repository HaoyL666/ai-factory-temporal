#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path


artifact_dir = Path(os.environ["AI_FACTORY_ARTIFACT_DIR"])
workspace_path = Path(os.environ["AI_FACTORY_WORKSPACE_PATH"])
inputs = json.loads(os.environ["AI_FACTORY_INPUTS_JSON"])

target = workspace_path / inputs["file_name"]
content = target.read_text(encoding="utf-8") if target.exists() else ""
old_text = inputs["old_text"]
new_text = inputs["new_text"]
checks = {
    "target_exists": target.exists(),
    "old_text_removed": old_text not in content,
    "new_text_present": new_text in content,
}
payload = {
    "checks": checks,
    "passed": all(checks.values()),
    "target": str(target),
    "content": content,
}
artifact_dir.mkdir(parents=True, exist_ok=True)
(artifact_dir / "verify_codex_edit.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(payload, sort_keys=True))
if not payload["passed"]:
    raise SystemExit(30)
