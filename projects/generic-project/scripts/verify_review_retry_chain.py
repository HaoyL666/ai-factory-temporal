#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path


artifact_dir = Path(os.environ["AI_FACTORY_ARTIFACT_DIR"])
workspace_path = Path(os.environ["AI_FACTORY_WORKSPACE_PATH"])
inputs = json.loads(os.environ["AI_FACTORY_INPUTS_JSON"])

review_file = workspace_path / inputs["review_file_name"]
retry_review_file = workspace_path / inputs["retry_review_file_name"]
review_content = review_file.read_text(encoding="utf-8") if review_file.exists() else ""
retry_review_content = (
    retry_review_file.read_text(encoding="utf-8") if retry_review_file.exists() else ""
)

checks = {
    "review_file_exists": review_file.exists(),
    "review_initial_marker_present": inputs["review_initial_marker"] in review_content,
    "review_repair_marker_present": inputs["review_repair_marker"] in review_content,
    "retry_review_file_exists": retry_review_file.exists(),
    "retry_review_marker_present": inputs["retry_review_marker"] in retry_review_content,
}
payload = {
    "checks": checks,
    "passed": all(checks.values()),
    "review_file": str(review_file),
    "retry_review_file": str(retry_review_file),
    "review_content": review_content,
    "retry_review_content": retry_review_content,
}
artifact_dir.mkdir(parents=True, exist_ok=True)
(artifact_dir / "verify_review_retry_chain.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(payload, sort_keys=True))
if not payload["passed"]:
    raise SystemExit(70)
