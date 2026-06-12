from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_factory_temporal import config
from ai_factory_temporal.catalog import CatalogError, ProjectCatalog
from ai_factory_temporal.models import StepStatus, WorkflowStatus
from ai_factory_temporal.store import SQLiteStore, new_workflow_id


class CatalogStoreTest(unittest.TestCase):
    def test_load_project_pack_and_workflow(self) -> None:
        catalog = ProjectCatalog(config.PROJECTS_DIR)
        pack = catalog.load_project_pack("generic-project")
        workflow = catalog.load_workflow("generic-project", "upgrade")

        self.assertEqual(pack["project_id"], "generic-project")
        self.assertEqual(workflow["id"], "upgrade")
        self.assertEqual([step["id"] for step in workflow["steps"]][0], "plan_upgrade")

    def test_load_crossplane_provider_oci_upgrade_workflow(self) -> None:
        catalog = ProjectCatalog(config.PROJECTS_DIR)
        pack = catalog.load_project_pack("crossplane-provider-oci")
        workflow = catalog.load_workflow("crossplane-provider-oci", "terraform_provider_upgrade")

        self.assertEqual(pack["project_id"], "crossplane-provider-oci")
        self.assertEqual(workflow["id"], "terraform_provider_upgrade")
        self.assertEqual(len(workflow["steps"]), 10)
        self.assertEqual(
            [step["id"] for step in workflow["steps"]],
            [
                "confirm_target_and_baseline",
                "apply_version_bump",
                "generate_diff_evidence",
                "classify_breaking_changes",
                "custom_config_controller_impact",
                "build_test_package",
                "publish_approval",
                "install_validation_approval",
                "live_oci_validation_approval",
                "assemble_final_review_packet",
            ],
        )
        self.assertIn("terraform_provider_upgrade", pack["supported_tasks"])

    def test_store_creates_workflow_and_steps(self) -> None:
        catalog = ProjectCatalog(config.PROJECTS_DIR)
        workflow = catalog.load_workflow("generic-project", "upgrade")

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "test.db")
            workflow_id = new_workflow_id()
            store.create_workflow(
                workflow_id=workflow_id,
                project_id="generic-project",
                task_type="upgrade",
                inputs={"target_version": "1.2.3"},
                workspace_path=str(Path(tmp)),
                steps=workflow["steps"],
            )

            response = store.workflow_response(workflow_id)
            self.assertEqual(response["workflow"]["status"], WorkflowStatus.PENDING.value)
            self.assertEqual(len(response["steps"]), 4)

            store.mark_step_running(workflow_id, "plan_upgrade", feedback_retry_count=0)
            store.finish_step(
                workflow_id=workflow_id,
                step_id="plan_upgrade",
                status=StepStatus.SUCCEEDED.value,
                output={"summary": "ok"},
                artifact_uri="local://artifact",
            )

            steps = store.list_steps(workflow_id)
            self.assertEqual(steps[0]["status"], StepStatus.SUCCEEDED.value)
            self.assertEqual(steps[0]["output"]["summary"], "ok")
            self.assertEqual(steps[0]["feedback_retry_count"], 0)

    def test_rejects_script_step_without_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects_dir = Path(tmp) / "projects"
            self._write_project(
                projects_dir,
                """
id: broken
steps:
  - id: missing_command
    kind: deterministic
    runner: script
""",
            )

            catalog = ProjectCatalog(projects_dir)
            with self.assertRaisesRegex(CatalogError, "command"):
                catalog.load_workflow("test-project", "broken")

    def test_rejects_invalid_codex_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects_dir = Path(tmp) / "projects"
            self._write_project(
                projects_dir,
                """
id: broken
steps:
  - id: bad_contract
    kind: codex
    prompt: prompts/task.md
    output_contract: false
""",
                prompt_text="# Task\n",
            )

            catalog = ProjectCatalog(projects_dir)
            with self.assertRaisesRegex(CatalogError, "output_contract"):
                catalog.load_workflow("test-project", "broken")

    def test_rejects_missing_prompt_include(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects_dir = Path(tmp) / "projects"
            self._write_project(
                projects_dir,
                """
id: broken
steps:
  - id: missing_include
    kind: codex
    prompt: prompts/task.md
""",
                prompt_text="{{include:prompts/_shared/missing.md}}\n",
            )

            catalog = ProjectCatalog(projects_dir)
            with self.assertRaisesRegex(CatalogError, "invalid prompt"):
                catalog.load_workflow("test-project", "broken")

    @staticmethod
    def _write_project(
        projects_dir: Path,
        workflow_text: str,
        prompt_text: str | None = None,
    ) -> None:
        project_dir = projects_dir / "test-project"
        (project_dir / "workflows").mkdir(parents=True)
        (project_dir / "prompts").mkdir()
        (project_dir / "project_pack.yaml").write_text(
            "project_id: test-project\nsupported_tasks:\n  - broken\n",
            encoding="utf-8",
        )
        (project_dir / "workflows" / "broken.yaml").write_text(
            workflow_text.strip() + "\n",
            encoding="utf-8",
        )
        if prompt_text is not None:
            (project_dir / "prompts" / "task.md").write_text(prompt_text, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
