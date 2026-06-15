from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException

from ai_factory_temporal.api import CreateWorkflowRequest, create_app


class ApiValidationTest(unittest.TestCase):
    def test_create_workflow_can_prepare_managed_git_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            projects_dir = tmp_path / "projects"
            target_repo = tmp_path / "target"
            worktrees_dir = tmp_path / "worktrees"
            self._write_valid_project(projects_dir)
            base_commit = _init_repo(target_repo)
            db_path = tmp_path / "api.db"
            fake_client = _FakeTemporalClient()

            with patch("ai_factory_temporal.api.config.PROJECTS_DIR", projects_dir), patch(
                "ai_factory_temporal.api.config.DB_PATH", db_path
            ), patch(
                "ai_factory_temporal.api.config.ARTIFACTS_DIR", tmp_path / "artifacts"
            ), patch(
                "ai_factory_temporal.api.Client.connect",
                new=AsyncMock(return_value=fake_client),
            ):
                app = create_app()
                response = asyncio.run(
                    _create_workflow(app)(
                        CreateWorkflowRequest(
                            project_id="test-project",
                            task_type="managed",
                            inputs={"target": "1.2.3"},
                            target_repo_path=str(target_repo),
                            base_ref="HEAD",
                            worktrees_dir=str(worktrees_dir),
                        )
                    )
                )

            workspace = response["workspace"]
            workspace_path = Path(workspace["workspace_path"])
            self.assertEqual(workspace["mode"], "managed_worktree")
            self.assertEqual(workspace["base_commit"], base_commit)
            self.assertTrue(workspace_path.exists())
            self.assertEqual(workspace_path.parent, worktrees_dir.resolve())
            self.assertEqual(
                _git(workspace_path, ["rev-parse", "HEAD"]).stdout.strip(),
                base_commit,
            )
            self.assertEqual(
                _git(workspace_path, ["branch", "--show-current"]).stdout.strip(),
                workspace["branch"],
            )

            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT workspace_path, status FROM workflows WHERE id = ?",
                    (response["workflow_id"],),
                ).fetchone()
            self.assertEqual(row[0], str(workspace_path))
            self.assertEqual(row[1], "PENDING")

            started_spec = fake_client.started[0]["args"][1]
            self.assertEqual(started_spec["workspace_path"], str(workspace_path))
            self.assertEqual(started_spec["workspace"], workspace)

    def test_create_workflow_removes_managed_worktree_when_temporal_start_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            projects_dir = tmp_path / "projects"
            target_repo = tmp_path / "target"
            worktrees_dir = tmp_path / "worktrees"
            self._write_valid_project(projects_dir)
            _init_repo(target_repo)
            db_path = tmp_path / "api.db"
            fake_client = _FakeTemporalClient(start_error=RuntimeError("temporal down"))

            with patch("ai_factory_temporal.api.config.PROJECTS_DIR", projects_dir), patch(
                "ai_factory_temporal.api.config.DB_PATH", db_path
            ), patch(
                "ai_factory_temporal.api.config.ARTIFACTS_DIR", tmp_path / "artifacts"
            ), patch(
                "ai_factory_temporal.api.Client.connect",
                new=AsyncMock(return_value=fake_client),
            ):
                app = create_app()
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(
                        _create_workflow(app)(
                            CreateWorkflowRequest(
                                project_id="test-project",
                                task_type="managed",
                                inputs={},
                                target_repo_path=str(target_repo),
                                worktrees_dir=str(worktrees_dir),
                            )
                        )
                    )

            self.assertEqual(raised.exception.status_code, 503)
            with sqlite3.connect(db_path) as conn:
                row = conn.execute("SELECT workspace_path, status FROM workflows").fetchone()
            self.assertEqual(row[1], "FAILED")
            self.assertFalse(Path(row[0]).exists())
            self.assertEqual(
                _git(target_repo, ["branch", "--list", "ai-factory/*"]).stdout.strip(),
                "",
            )

    def test_create_workflow_removes_managed_worktree_when_db_create_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            projects_dir = tmp_path / "projects"
            target_repo = tmp_path / "target"
            worktrees_dir = tmp_path / "worktrees"
            self._write_valid_project(projects_dir)
            _init_repo(target_repo)

            with patch("ai_factory_temporal.api.config.PROJECTS_DIR", projects_dir), patch(
                "ai_factory_temporal.api.config.DB_PATH", tmp_path / "api.db"
            ), patch(
                "ai_factory_temporal.api.SQLiteStore.create_workflow",
                side_effect=RuntimeError("db down"),
            ):
                app = create_app()
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(
                        _create_workflow(app)(
                            CreateWorkflowRequest(
                                project_id="test-project",
                                task_type="managed",
                                inputs={},
                                target_repo_path=str(target_repo),
                                worktrees_dir=str(worktrees_dir),
                            )
                        )
                    )

            self.assertEqual(raised.exception.status_code, 500)
            self.assertFalse(any(worktrees_dir.iterdir()))
            self.assertEqual(
                _git(target_repo, ["branch", "--list", "ai-factory/*"]).stdout.strip(),
                "",
            )

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

    def _write_valid_project(self, projects_dir: Path) -> None:
        project_dir = projects_dir / "test-project"
        (project_dir / "workflows").mkdir(parents=True)
        (project_dir / "project_pack.yaml").write_text(
            "project_id: test-project\nsupported_tasks:\n  - managed\n",
            encoding="utf-8",
        )
        (project_dir / "workflows" / "managed.yaml").write_text(
            """
id: managed
steps:
  - id: echo
    kind: deterministic
    runner: script
    command:
      - python3
      - -c
      - print('ok')
""".strip()
            + "\n",
            encoding="utf-8",
        )


def _create_workflow(app: FastAPI):
    for route in app.routes:
        if (
            getattr(route, "path", None) == "/workflows"
            and "POST" in getattr(route, "methods", set())
        ):
            return route.endpoint
    raise AssertionError("POST /workflows route not found")


class _FakeTemporalClient:
    def __init__(self, start_error: Exception | None = None):
        self.start_error = start_error
        self.started: list[dict[str, object]] = []

    async def start_workflow(self, *args, **kwargs):
        if self.start_error:
            raise self.start_error
        self.started.append({"args": args, "kwargs": kwargs})


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True)
    _git(path, ["init"])
    _git(path, ["config", "user.name", "Test User"])
    _git(path, ["config", "user.email", "test@example.invalid"])
    (path / "README.md").write_text("# Target\n", encoding="utf-8")
    _git(path, ["add", "README.md"])
    _git(path, ["commit", "-m", "initial"])
    return _git(path, ["rev-parse", "HEAD"]).stdout.strip()


def _git(path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=path,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed


if __name__ == "__main__":
    unittest.main()
