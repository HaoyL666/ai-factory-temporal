from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ai_factory_temporal.activities import (
    get_workspace_checkpoint,
    reset_workspace_to_checkpoint,
    run_script_step,
)
from ai_factory_temporal.models import StepStatus
from ai_factory_temporal.store import SQLiteStore, new_workflow_id


class WorkspaceCheckpointTest(unittest.TestCase):
    def test_successful_script_step_commits_source_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "repo"
            initial_commit = _init_repo(workspace)
            script = tmp_path / "edit_source.py"
            script.write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "Path('target.txt').write_text('updated\\n', encoding='utf-8')",
                        "print('updated target')",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            store, workflow_id, step = _store_with_step(
                workspace=workspace,
                db_path=workspace / "state" / "test.db",
                command=[sys.executable, str(script)],
            )

            result = run_script_step(_payload(workspace, workflow_id, step))

            checkpoint = result["output"]["checkpoint"]
            self.assertEqual(result["status"], StepStatus.SUCCEEDED.value)
            self.assertEqual(checkpoint["status"], "committed")
            self.assertNotEqual(checkpoint["commit"], initial_commit)
            self.assertEqual((workspace / "target.txt").read_text(encoding="utf-8"), "updated\n")
            self.assertEqual(
                _git(workspace, ["show", "--pretty=", "--name-only", "HEAD"]).stdout.strip(),
                "target.txt",
            )

            stored = store.list_steps(workflow_id)[0]
            self.assertEqual(stored["output"]["checkpoint"]["commit"], checkpoint["commit"])

    def test_failed_script_step_does_not_commit_and_reset_restores_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "repo"
            initial_commit = _init_repo(workspace)
            script = tmp_path / "fail_after_edit.py"
            script.write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "Path('target.txt').write_text('failed edit\\n', encoding='utf-8')",
                        "Path('untracked.txt').write_text('temporary\\n', encoding='utf-8')",
                        "raise SystemExit(40)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            _store, workflow_id, step = _store_with_step(
                workspace=workspace,
                db_path=workspace / "state" / "test.db",
                command=[sys.executable, str(script)],
            )

            result = run_script_step(_payload(workspace, workflow_id, step))

            self.assertEqual(result["status"], StepStatus.FAILED.value)
            self.assertEqual(_head(workspace), initial_commit)
            self.assertEqual(
                (workspace / "target.txt").read_text(encoding="utf-8"),
                "failed edit\n",
            )
            self.assertTrue((workspace / "untracked.txt").exists())

            reset = reset_workspace_to_checkpoint(
                {
                    "workspace_path": str(workspace),
                    "checkpoint_commit": initial_commit,
                    "db_path": str(workspace / "state" / "test.db"),
                    "artifacts_path": str(workspace / "artifacts"),
                }
            )

            self.assertEqual(reset["status"], "reset")
            self.assertEqual(reset["commit"], initial_commit)
            self.assertEqual((workspace / "target.txt").read_text(encoding="utf-8"), "base\n")
            self.assertFalse((workspace / "untracked.txt").exists())
            self.assertTrue((workspace / "artifacts").exists())
            self.assertTrue((workspace / "state" / "test.db").exists())

    def test_non_git_workspace_disables_checkpointing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = get_workspace_checkpoint({"workspace_path": tmp})

            self.assertFalse(checkpoint["enabled"])
            self.assertEqual(checkpoint["status"], "not_git")


def _init_repo(workspace: Path) -> str:
    workspace.mkdir(parents=True)
    _git(workspace, ["init"])
    _git(workspace, ["config", "user.name", "Test User"])
    _git(workspace, ["config", "user.email", "test@example.invalid"])
    (workspace / "target.txt").write_text("base\n", encoding="utf-8")
    _git(workspace, ["add", "target.txt"])
    _git(workspace, ["commit", "-m", "initial"])
    return _head(workspace)


def _store_with_step(
    *,
    workspace: Path,
    db_path: Path,
    command: list[str],
) -> tuple[SQLiteStore, str, dict[str, object]]:
    step = {
        "id": "edit_source",
        "name": "Edit Source",
        "kind": "deterministic",
        "runner": "script",
        "command": command,
        "cwd": "{{workspace_path}}",
    }
    store = SQLiteStore(db_path)
    workflow_id = new_workflow_id()
    store.create_workflow(
        workflow_id=workflow_id,
        project_id="generic-project",
        task_type="checkpoint-test",
        inputs={},
        workspace_path=str(workspace),
        steps=[step],
    )
    return store, workflow_id, step


def _payload(workspace: Path, workflow_id: str, step: dict[str, object]) -> dict[str, object]:
    return {
        "workflow_id": workflow_id,
        "project_id": "generic-project",
        "task_type": "checkpoint-test",
        "inputs": {},
        "workspace_path": str(workspace),
        "project_pack": {},
        "project_dir": str(workspace),
        "db_path": str(workspace / "state" / "test.db"),
        "artifacts_path": str(workspace / "artifacts"),
        "step": step,
        "feedback_retry_count": 0,
    }


def _head(workspace: Path) -> str:
    return _git(workspace, ["rev-parse", "HEAD"]).stdout.strip()


def _git(workspace: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=workspace,
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
