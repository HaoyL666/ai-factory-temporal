from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from temporalio.client import Client

from ai_factory_temporal import config
from ai_factory_temporal.catalog import CatalogError, ProjectCatalog
from ai_factory_temporal.store import SQLiteStore, new_workflow_id, usage_summary
from ai_factory_temporal.workflows import AIFactoryWorkflow


class CreateWorkflowRequest(BaseModel):
    project_id: str
    task_type: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    workspace_path: str


class ApprovalRequest(BaseModel):
    step_id: str
    approved: bool = True
    comment: str | None = None


class FeedbackRequest(BaseModel):
    step_id: str
    action: str = "retry"
    message: str = ""


def create_app() -> FastAPI:
    app = FastAPI(title="Local AI Factory Temporal Harness")
    catalog = ProjectCatalog(config.PROJECTS_DIR)
    store = SQLiteStore(config.DB_PATH)

    async def temporal_client() -> Client:
        return await Client.connect(config.TEMPORAL_ADDRESS)

    @app.post("/workflows")
    async def create_workflow(request: CreateWorkflowRequest):
        try:
            project_pack = catalog.load_project_pack(request.project_id)
            catalog.validate_task_supported(project_pack, request.project_id, request.task_type)
            workflow_def = catalog.load_workflow(request.project_id, request.task_type)
        except CatalogError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        workflow_id = new_workflow_id()
        project_dir = catalog.project_dir(request.project_id)
        workspace_path = str(Path(request.workspace_path).resolve())
        steps = workflow_def["steps"]
        store.create_workflow(
            workflow_id=workflow_id,
            project_id=request.project_id,
            task_type=request.task_type,
            inputs=request.inputs,
            workspace_path=workspace_path,
            steps=steps,
        )
        spec = {
            "workflow_id": workflow_id,
            "project_id": request.project_id,
            "task_type": request.task_type,
            "inputs": request.inputs,
            "workspace_path": workspace_path,
            "project_pack": project_pack,
            "project_dir": str(project_dir),
            "db_path": str(config.DB_PATH),
            "artifacts_path": str(config.ARTIFACTS_DIR),
            "steps": steps,
        }
        try:
            client = await temporal_client()
            await client.start_workflow(
                AIFactoryWorkflow.run,
                spec,
                id=workflow_id,
                task_queue=config.TEMPORAL_TASK_QUEUE,
            )
        except Exception as exc:
            store.set_workflow_status(workflow_id, "FAILED")
            raise HTTPException(
                status_code=503,
                detail=f"Failed to start Temporal workflow: {exc}",
            ) from exc
        return {
            "workflow_id": workflow_id,
            "status": "PENDING",
            "temporal_workflow_id": workflow_id,
        }

    @app.get("/workflows/{workflow_id}")
    async def get_workflow(workflow_id: str):
        try:
            return store.workflow_response(workflow_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/workflows/{workflow_id}/usage")
    async def get_workflow_usage(workflow_id: str):
        try:
            store.get_workflow(workflow_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        records = store.list_usage_records(workflow_id)
        return {
            "workflow_id": workflow_id,
            "summary": usage_summary(records),
            "records": records,
        }

    @app.get("/workflows/{workflow_id}/runtime")
    async def get_runtime_state(workflow_id: str):
        client = await temporal_client()
        handle = client.get_workflow_handle(workflow_id)
        return await handle.query("state")

    @app.post("/workflows/{workflow_id}/approve")
    async def approve(workflow_id: str, request: ApprovalRequest):
        client = await temporal_client()
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(
            "approve",
            {
                "step_id": request.step_id,
                "approved": request.approved,
                "comment": request.comment,
            },
        )
        return {"workflow_id": workflow_id, "signal": "approve", "step_id": request.step_id}

    @app.post("/workflows/{workflow_id}/feedback")
    async def feedback(workflow_id: str, request: FeedbackRequest):
        client = await temporal_client()
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(
            "submit_feedback",
            {
                "step_id": request.step_id,
                "action": request.action,
                "message": request.message,
            },
        )
        return {"workflow_id": workflow_id, "signal": "feedback", "step_id": request.step_id}

    @app.post("/workflows/{workflow_id}/cancel")
    async def cancel(workflow_id: str, reason: str = "cancel requested"):
        client = await temporal_client()
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal("cancel", reason)
        return {"workflow_id": workflow_id, "signal": "cancel"}

    return app


def main() -> None:
    import uvicorn

    uvicorn.run("ai_factory_temporal.api:create_app", factory=True, reload=True)


if __name__ == "__main__":
    main()
