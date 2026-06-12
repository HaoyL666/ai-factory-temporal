#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path


artifact_dir = Path(os.environ["AI_FACTORY_ARTIFACT_DIR"])
retry_count = int(os.environ.get("AI_FACTORY_RETRY_COUNT", "0"))
retry_feedback = json.loads(os.environ.get("AI_FACTORY_RETRY_FEEDBACK_JSON", "{}"))

payload = {
    "passed": retry_count > 0,
    "retry_count": retry_count,
    "retry_feedback": retry_feedback,
}
artifact_dir.mkdir(parents=True, exist_ok=True)
(artifact_dir / "fail_once.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(payload, sort_keys=True))
if not payload["passed"]:
    raise SystemExit(40)
