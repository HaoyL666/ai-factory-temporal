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
            self.assertEqual(
                [step["runner"] for step in response["steps"]],
                ["codex", "script", None, "codex"],
            )

            store.mark_step_running(workflow_id, "plan_upgrade", feedback_retry_count=0)
            store.finish_step(
                workflow_id=workflow_id,
                step_id="plan_upgrade",
                status=StepStatus.SUCCEEDED.value,
                output={"runner": "codex", "summary": "ok"},
                artifact_uri="local://artifact",
            )

            steps = store.list_steps(workflow_id)
            self.assertEqual(steps[0]["status"], StepStatus.SUCCEEDED.value)
            self.assertEqual(steps[0]["runner"], "codex")
            self.assertEqual(steps[0]["output"]["summary"], "ok")
            self.assertEqual(steps[0]["feedback_retry_count"], 0)

    def test_store_backfills_missing_runner_for_existing_steps(self) -> None:
        catalog = ProjectCatalog(config.PROJECTS_DIR)
        workflow = catalog.load_workflow("generic-project", "upgrade")

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            store = SQLiteStore(db_path)
            workflow_id = new_workflow_id()
            store.create_workflow(
                workflow_id=workflow_id,
                project_id="generic-project",
                task_type="upgrade",
                inputs={"target_version": "1.2.3"},
                workspace_path=str(Path(tmp)),
                steps=workflow["steps"],
            )

            with store.connect() as conn:
                conn.execute(
                    "UPDATE workflow_steps SET runner = NULL WHERE workflow_id = ?",
                    (workflow_id,),
                )

            reopened = SQLiteStore(db_path)
            steps = reopened.list_steps(workflow_id)
            self.assertEqual(
                [step["runner"] for step in steps],
                ["codex", "script", None, "codex"],
            )

    def test_store_records_codex_usage_and_workflow_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "test.db")
            workflow_id = new_workflow_id()
            store.create_workflow(
                workflow_id=workflow_id,
                project_id="generic-project",
                task_type="codex",
                inputs={},
                workspace_path=str(Path(tmp)),
                steps=[{"id": "codex_step", "name": "Codex Step", "kind": "codex"}],
            )

            record = store.record_codex_usage(
                workflow_id=workflow_id,
                step_id="codex_step",
                step_run_number=1,
                role="coder",
                review_round=1,
                mode="sdk",
                model="gpt-test",
                thread_id="thread-1",
                turn_id="turn-1",
                usage={
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "cache_read_input_tokens": 10,
                    "cache_creation_input_tokens": 0,
                    "billable_input_tokens": 90,
                    "total_tokens": 125,
                },
                raw_usage={"input_tokens": 100},
                metadata={"mode": "sdk", "turn_id": "turn-1"},
                estimated_cost=0.0014,
                cost_currency="USD",
                artifact_uri="/tmp/artifacts/codex_step/step-run-1",
            )

            self.assertEqual(record["role"], "coder")
            self.assertEqual(record["usage"]["input_tokens"], 100)
            self.assertEqual(record["estimated_cost"], 0.0014)

            response = store.workflow_response(workflow_id)
            self.assertEqual(response["usage"]["summary"]["turn_count"], 1)
            self.assertEqual(response["usage"]["summary"]["priced_turn_count"], 1)
            self.assertEqual(response["usage"]["summary"]["usage"]["total_tokens"], 125)
            self.assertEqual(response["usage"]["summary"]["estimated_cost"], 0.0014)
            self.assertEqual(response["usage"]["records"][0]["turn_id"], "turn-1")

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
