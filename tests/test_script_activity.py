from __future__ import annotations

import json
import sys
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
                    "feedback_retry_count": 0,
                }
            )

            self.assertEqual(result["status"], StepStatus.SUCCEEDED.value)
            artifact_uri = Path(result["artifact_uri"])
            self.assertTrue((artifact_uri / "validation.json").exists())
            self.assertTrue((artifact_uri / "artifact-manifest.json").exists())
            self.assertTrue((artifact_uri / "step-result.json").exists())
            self.assertEqual(result["output"]["status"], StepStatus.SUCCEEDED.value)
            self.assertEqual(result["output"]["artifact_uri"], str(artifact_uri))
            self.assertEqual(
                result["output"]["artifact_manifest"],
                str(artifact_uri / "artifact-manifest.json"),
            )
            self.assertIn(
                "validation.json",
                {artifact["path"] for artifact in result["output"]["artifacts"]},
            )
            stored = store.list_steps(workflow_id)[1]
            self.assertEqual(stored["status"], StepStatus.SUCCEEDED.value)
            self.assertEqual(stored["output"]["status"], StepStatus.SUCCEEDED.value)

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
                    "feedback_retry_count": 0,
                }
            )

            self.assertEqual(result["status"], StepStatus.SUCCEEDED.value)
            artifact_uri = Path(result["artifact_uri"])
            self.assertTrue((artifact_uri / "generation_evidence.json").exists())
            self.assertTrue((artifact_uri / "artifact-manifest.json").exists())
            self.assertIn(
                "generation_evidence.json",
                {artifact["path"] for artifact in result["output"]["artifacts"]},
            )
            stored = store.list_steps(workflow_id)[2]
            self.assertEqual(stored["status"], StepStatus.SUCCEEDED.value)

    def test_script_result_json_is_loaded_into_normalized_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            script_path = tmp_path / "write_result.py"
            script_path.write_text(
                "\n".join(
                    [
                        "import json",
                        "import os",
                        "from pathlib import Path",
                        "artifact_dir = Path(os.environ['AI_FACTORY_ARTIFACT_DIR'])",
                        "artifact_dir.mkdir(parents=True, exist_ok=True)",
                        "(artifact_dir / 'provider_schema.diff').write_text('diff --git a b\\n')",
                        "(artifact_dir / 'result.json').write_text(json.dumps({",
                        "  'summary': 'Generated Terraform provider diff evidence.',",
                        "  'checks': ['diff generated'],",
                        "  'artifacts': [{'path': 'provider_schema.diff', 'kind': 'diff'}],",
                        "}, sort_keys=True))",
                        "print('done')",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            step = {
                "id": "generate_diff_evidence",
                "name": "Generate Diff Evidence",
                "kind": "deterministic",
                "runner": "script",
                "command": [sys.executable, str(script_path)],
                "cwd": "{{workspace_path}}",
            }
            store = SQLiteStore(tmp_path / "test.db")
            workflow_id = new_workflow_id()
            store.create_workflow(
                workflow_id=workflow_id,
                project_id="generic-project",
                task_type="test",
                inputs={},
                workspace_path=str(tmp_path),
                steps=[step],
            )

            result = run_script_step(
                {
                    "workflow_id": workflow_id,
                    "project_id": "generic-project",
                    "task_type": "test",
                    "inputs": {},
                    "workspace_path": str(tmp_path),
                    "project_pack": {},
                    "project_dir": str(tmp_path),
                    "db_path": str(tmp_path / "test.db"),
                    "artifacts_path": str(tmp_path / "artifacts"),
                    "step": step,
                    "feedback_retry_count": 0,
                }
            )

            artifact_uri = Path(result["artifact_uri"])
            step_result = json.loads((artifact_uri / "step-result.json").read_text())
            manifest = json.loads((artifact_uri / "artifact-manifest.json").read_text())
            self.assertEqual(result["status"], StepStatus.SUCCEEDED.value)
            self.assertEqual(
                result["output"]["summary"],
                "Generated Terraform provider diff evidence.",
            )
            self.assertEqual(result["output"]["script_result"]["checks"], ["diff generated"])
            self.assertIn(
                "provider_schema.diff",
                {artifact["path"] for artifact in manifest["files"]},
            )
            self.assertEqual(step_result["script_result"]["summary"], result["output"]["summary"])

    def test_failed_script_step_writes_normalized_artifacts(self) -> None:
        catalog = ProjectCatalog(config.PROJECTS_DIR)
        pack = catalog.load_project_pack("generic-project")
        step = {
            "id": "fail_once",
            "name": "Fail Once",
            "kind": "deterministic",
            "runner": "script",
            "command": [
                "python3.12",
                "{{project_dir}}/scripts/fail_once.py",
            ],
            "cwd": "{{workspace_path}}",
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = SQLiteStore(tmp_path / "test.db")
            workflow_id = new_workflow_id()
            store.create_workflow(
                workflow_id=workflow_id,
                project_id="generic-project",
                task_type="fail_once",
                inputs={},
                workspace_path=str(tmp_path),
                steps=[step],
            )

            result = run_script_step(
                {
                    "workflow_id": workflow_id,
                    "project_id": "generic-project",
                    "task_type": "fail_once",
                    "inputs": {},
                    "workspace_path": str(tmp_path),
                    "project_pack": pack,
                    "project_dir": str(config.PROJECTS_DIR / "generic-project"),
                    "db_path": str(tmp_path / "test.db"),
                    "artifacts_path": str(tmp_path / "artifacts"),
                    "step": step,
                    "feedback_retry_count": 0,
                }
            )

            artifact_uri = Path(result["artifact_uri"])
            self.assertEqual(result["status"], StepStatus.FAILED.value)
            self.assertTrue((artifact_uri / "stdout.log").exists())
            self.assertTrue((artifact_uri / "stderr.log").exists())
            self.assertTrue((artifact_uri / "artifact-manifest.json").exists())
            self.assertTrue((artifact_uri / "step-result.json").exists())
            self.assertEqual(result["output"]["status"], StepStatus.FAILED.value)
            self.assertEqual(result["output"]["returncode"], 40)
            self.assertIn(
                "fail_once.json",
                {item["path"] for item in result["output"]["artifacts"]},
            )
            stored = store.list_steps(workflow_id)[0]
            self.assertEqual(stored["status"], StepStatus.FAILED.value)
            self.assertEqual(stored["output"]["returncode"], 40)

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
                        "feedback_retry_count": 0,
                    }
                )


if __name__ == "__main__":
    unittest.main()
