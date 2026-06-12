from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException

from ai_factory_temporal.api import CreateWorkflowRequest, create_app


class ApiValidationTest(unittest.TestCase):
    def test_rejects_invalid_workflow_before_creating_db_row_or_temporal_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            projects_dir = tmp_path / "projects"
            project_dir = projects_dir / "test-project"
            (project_dir / "workflows").mkdir(parents=True)
            (project_dir / "project_pack.yaml").write_text(
                "project_id: test-project\nsupported_tasks:\n  - broken\n",
                encoding="utf-8",
            )
            (project_dir / "workflows" / "broken.yaml").write_text(
                """
id: broken
steps:
  - id: missing_command
    kind: deterministic
    runner: script
""".strip()
                + "\n",
                encoding="utf-8",
            )

            db_path = tmp_path / "api.db"
            with patch("ai_factory_temporal.api.config.PROJECTS_DIR", projects_dir), patch(
                "ai_factory_temporal.api.config.DB_PATH", db_path
            ):
                app = create_app()
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(
                        _create_workflow(app)(
                            CreateWorkflowRequest(
                                project_id="test-project",
                                task_type="broken",
                                inputs={},
                                workspace_path=str(tmp_path),
                            )
                        )
                    )

            self.assertEqual(raised.exception.status_code, 400)
            self.assertIn("command", raised.exception.detail)

            with sqlite3.connect(db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]
            self.assertEqual(count, 0)

    def test_rejects_unsupported_task_before_loading_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            projects_dir = tmp_path / "projects"
            project_dir = projects_dir / "test-project"
            (project_dir / "workflows").mkdir(parents=True)
            (project_dir / "project_pack.yaml").write_text(
                "project_id: test-project\nsupported_tasks:\n  - supported\n",
                encoding="utf-8",
            )
            (project_dir / "workflows" / "unsupported.yaml").write_text(
                """
id: unsupported
steps:
  - id: missing_command
    kind: deterministic
    runner: script
""".strip()
                + "\n",
                encoding="utf-8",
            )

            db_path = tmp_path / "api.db"
            with patch("ai_factory_temporal.api.config.PROJECTS_DIR", projects_dir), patch(
                "ai_factory_temporal.api.config.DB_PATH", db_path
            ):
                app = create_app()
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(
                        _create_workflow(app)(
                            CreateWorkflowRequest(
                                project_id="test-project",
                                task_type="unsupported",
                                inputs={},
                                workspace_path=str(tmp_path),
                            )
                        )
                    )

            self.assertEqual(raised.exception.status_code, 400)
            self.assertIn("not supported", raised.exception.detail)


def _create_workflow(app: FastAPI):
    for route in app.routes:
        if getattr(route, "path", None) == "/workflows" and "POST" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("POST /workflows route not found")


if __name__ == "__main__":
    unittest.main()
