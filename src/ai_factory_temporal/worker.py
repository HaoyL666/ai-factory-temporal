from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from ai_factory_temporal import config
from ai_factory_temporal.activities import (
    get_workspace_checkpoint,
    record_approval_decision,
    record_approval_wait,
    record_feedback,
    record_step_failed,
    record_step_skipped,
    record_step_succeeded,
    record_step_timeline_event,
    record_step_waiting_for_feedback,
    reset_workspace_to_checkpoint,
    run_codex_step,
    run_script_step,
    set_workflow_status,
)
from ai_factory_temporal.workflows import AIFactoryWorkflow


async def run_worker() -> None:
    client = await Client.connect(config.TEMPORAL_ADDRESS)
    with ThreadPoolExecutor(max_workers=config.TEMPORAL_ACTIVITY_MAX_WORKERS) as executor:
        worker = Worker(
            client,
            task_queue=config.TEMPORAL_TASK_QUEUE,
            workflows=[AIFactoryWorkflow],
            activities=[
                set_workflow_status,
                get_workspace_checkpoint,
                reset_workspace_to_checkpoint,
                record_approval_wait,
                record_approval_decision,
                record_step_skipped,
                record_step_succeeded,
                record_step_timeline_event,
                record_step_waiting_for_feedback,
                record_step_failed,
                record_feedback,
                run_codex_step,
                run_script_step,
            ],
            activity_executor=executor,
        )
        await worker.run()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
