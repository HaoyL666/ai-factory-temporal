from __future__ import annotations

import unittest

from ai_factory_temporal import config
from ai_factory_temporal.catalog import ProjectCatalog
from ai_factory_temporal.prompting import render_prompt


class PromptingTest(unittest.TestCase):
    def test_render_prompt_expands_shared_includes(self) -> None:
        catalog = ProjectCatalog(config.PROJECTS_DIR)
        pack = catalog.load_project_pack("generic-project")
        workflow = catalog.load_workflow("generic-project", "upgrade")
        step = workflow["steps"][0]

        rendered = render_prompt(
            project_dir=catalog.project_dir("generic-project"),
            workflow_id="wf-test",
            project_id="generic-project",
            task_type="upgrade",
            workspace_path="/tmp/workspace",
            step=step,
            inputs={"target_version": "1.2.3"},
            project_pack=pack,
            previous_outputs=[],
            retry_feedback=None,
            attempt=1,
        )

        self.assertIn("## Runtime Context", rendered)
        self.assertIn("Workflow: `wf-test`", rendered)
        self.assertIn("Project: `generic-project`", rendered)
        self.assertIn("`1.2.3`", rendered)
        self.assertIn("## Output Contract", rendered)
        self.assertNotIn("{{include:", rendered)


if __name__ == "__main__":
    unittest.main()
