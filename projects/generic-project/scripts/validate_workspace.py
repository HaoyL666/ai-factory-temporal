#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path


artifact_dir = Path(os.environ["AI_FACTORY_ARTIFACT_DIR"])
workspace_path = Path(os.environ["AI_FACTORY_WORKSPACE_PATH"])
inputs = json.loads(os.environ["AI_FACTORY_INPUTS_JSON"])

checks = {
    "workspace_exists": workspace_path.exists(),
    "target_version_provided": bool(inputs.get("target_version")),
}
payload = {
    "checks": checks,
    "passed": all(checks.values()),
    "workspace": str(workspace_path),
    "target_version": inputs.get("target_version"),
}
artifact_dir.mkdir(parents=True, exist_ok=True)
(artifact_dir / "validation.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(payload, sort_keys=True))
if not payload["passed"]:
    raise SystemExit(20)
