from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(os.environ.get("AI_FACTORY_ROOT", Path(__file__).resolve().parents[2]))
PROJECTS_DIR = Path(os.environ.get("AI_FACTORY_PROJECTS_DIR", PROJECT_ROOT / "projects"))
DB_PATH = Path(os.environ.get("AI_FACTORY_DB", PROJECT_ROOT / "var" / "ai_factory.db"))
ARTIFACTS_DIR = Path(os.environ.get("AI_FACTORY_ARTIFACTS", PROJECT_ROOT / "var" / "artifacts"))

TEMPORAL_ADDRESS = os.environ.get("AI_FACTORY_TEMPORAL_ADDRESS", "127.0.0.1:7233")
TEMPORAL_TASK_QUEUE = os.environ.get("AI_FACTORY_TASK_QUEUE", "ai-factory-local")
TEMPORAL_ACTIVITY_MAX_WORKERS = int(os.environ.get("AI_FACTORY_ACTIVITY_MAX_WORKERS", "8"))

CODEX_MODEL = os.environ.get("AI_FACTORY_CODEX_MODEL")
CODEX_SANDBOX = os.environ.get("AI_FACTORY_CODEX_SANDBOX", "workspace-write")
CODEX_APPROVAL_MODE = os.environ.get("AI_FACTORY_CODEX_APPROVAL_MODE", "auto_review")
CODEX_MODE = os.environ.get("AI_FACTORY_CODEX_MODE", "real")
