from __future__ import annotations

import unittest

from ai_factory_temporal.workflows import AIFactoryWorkflow


class WorkflowFeedbackTest(unittest.TestCase):
    def test_feedback_is_matched_by_step_id(self) -> None:
        workflow = AIFactoryWorkflow()
        workflow.feedback_events = [
            {"step_id": "other_step", "action": "retry", "message": "wrong step"},
            {"step_id": "failed_step", "action": "skip", "message": "skip failed step"},
        ]

        self.assertTrue(workflow._has_feedback_for_step("failed_step"))
        feedback = workflow._pop_feedback_for_step("failed_step")

        self.assertEqual(feedback["action"], "skip")
        self.assertEqual([event["step_id"] for event in workflow.feedback_events], ["other_step"])


if __name__ == "__main__":
    unittest.main()
