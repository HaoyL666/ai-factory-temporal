#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path


artifact_dir = Path(os.environ["AI_FACTORY_ARTIFACT_DIR"])
payload = {
    "published": True,
    "workflow_id": os.environ["AI_FACTORY_WORKFLOW_ID"],
    "step_id": os.environ["AI_FACTORY_STEP_ID"],
}
artifact_dir.mkdir(parents=True, exist_ok=True)
(artifact_dir / "publish.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(payload, sort_keys=True))
