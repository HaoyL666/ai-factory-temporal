from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_factory_temporal import config
from ai_factory_temporal.catalog import ProjectCatalog
from ai_factory_temporal.activities import run_script_step
from ai_factory_temporal.models import StepStatus
from ai_factory_temporal.store import SQLiteStore, new_workflow_id


class ScriptActivityTest(unittest.TestCase):
    def test_run_script_step_updates_db_and_artifacts(self) -> None:
        catalog = ProjectCatalog(config.PROJECTS_DIR)
        pack = catalog.load_project_pack("generic-project")
        workflow = catalog.load_workflow("generic-project", "upgrade")
        step = workflow["steps"][1]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = SQLiteStore(tmp_path / "test.db")
            workflow_id = new_workflow_id()
            store.create_workflow(
                workflow_id=workflow_id,
                project_id="generic-project",
                task_type="upgrade",
                inputs={"target_version": "1.2.3"},
                workspace_path=str(tmp_path),
                steps=workflow["steps"],
            )

            result = run_script_step(
                {
                    "workflow_id": workflow_id,
                    "project_id": "generic-project",
                    "task_type": "upgrade",
                    "inputs": {"target_version": "1.2.3"},
                    "workspace_path": str(tmp_path),
                    "project_pack": pack,
                    "project_dir": str(config.PROJECTS_DIR / "generic-project"),
                    "db_path": str(tmp_path / "test.db"),
                    "artifacts_path": str(tmp_path / "artifacts"),
                    "step": step,
                    "retry_count": 0,
                }
            )

            self.assertEqual(result["status"], StepStatus.SUCCEEDED.value)
            self.assertTrue((Path(result["artifact_uri"]) / "validation.json").exists())
            stored = store.list_steps(workflow_id)[1]
            self.assertEqual(stored["status"], StepStatus.SUCCEEDED.value)

    def test_crossplane_generation_evidence_script_updates_db_and_artifacts(self) -> None:
        catalog = ProjectCatalog(config.PROJECTS_DIR)
        pack = catalog.load_project_pack("crossplane-provider-oci")
        workflow = catalog.load_workflow("crossplane-provider-oci", "terraform_provider_upgrade")
        step = workflow["steps"][2]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "Makefile").write_text(
                "TERRAFORM_PROVIDER_VERSION := 8.99.0\n",
                encoding="utf-8",
            )
            for relative in [
                "config",
                "apis",
                "internal/controller",
                "package/crds",
                "examples-generated",
                "docs",
                ".github/workflows",
            ]:
                (tmp_path / relative).mkdir(parents=True, exist_ok=True)
            (tmp_path / "config" / "schema.json").write_text("{}\n", encoding="utf-8")

            store = SQLiteStore(tmp_path / "test.db")
            workflow_id = new_workflow_id()
            store.create_workflow(
                workflow_id=workflow_id,
                project_id="crossplane-provider-oci",
                task_type="terraform_provider_upgrade",
                inputs={"target_version": "8.99.0"},
                workspace_path=str(tmp_path),
                steps=workflow["steps"],
            )

            result = run_script_step(
                {
                    "workflow_id": workflow_id,
                    "project_id": "crossplane-provider-oci",
                    "task_type": "terraform_provider_upgrade",
                    "inputs": {"target_version": "8.99.0"},
                    "workspace_path": str(tmp_path),
                    "project_pack": pack,
                    "project_dir": str(config.PROJECTS_DIR / "crossplane-provider-oci"),
                    "db_path": str(tmp_path / "test.db"),
                    "artifacts_path": str(tmp_path / "artifacts"),
                    "step": step,
                    "retry_count": 0,
                }
            )

            self.assertEqual(result["status"], StepStatus.SUCCEEDED.value)
            artifact_uri = Path(result["artifact_uri"])
            self.assertTrue((artifact_uri / "generation_evidence.json").exists())
            stored = store.list_steps(workflow_id)[2]
            self.assertEqual(stored["status"], StepStatus.SUCCEEDED.value)

    def test_missing_script_command_is_system_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = SQLiteStore(tmp_path / "test.db")
            workflow_id = new_workflow_id()
            step = {
                "id": "broken_script",
                "name": "Broken Script",
                "kind": "deterministic",
                "runner": "script",
            }
            store.create_workflow(
                workflow_id=workflow_id,
                project_id="generic-project",
                task_type="broken",
                inputs={},
                workspace_path=str(tmp_path),
                steps=[step],
            )

            with self.assertRaisesRegex(ValueError, "script step is missing command"):
                run_script_step(
                    {
                        "workflow_id": workflow_id,
                        "project_id": "generic-project",
                        "task_type": "broken",
                        "inputs": {},
                        "workspace_path": str(tmp_path),
                        "project_pack": {},
                        "project_dir": str(config.PROJECTS_DIR / "generic-project"),
                        "db_path": str(tmp_path / "test.db"),
                        "artifacts_path": str(tmp_path / "artifacts"),
                        "step": step,
                        "retry_count": 0,
                    }
                )


if __name__ == "__main__":
    unittest.main()
