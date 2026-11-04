from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.services.execution_service import execute_agent_task, AgentExecutionError

router = APIRouter(prefix="/api/v1/tasks", tags=["Agent Execution"])

class TaskExecutionRequest(BaseModel):
    model_name: str
    task_type: Literal["backend", "frontend", "database", "testing", "docs"]
    title: str
    description: str

@router.post("/execute")
async def run_agent_task_endpoint(payload: TaskExecutionRequest):
    try:
        output = await execute_agent_task(
            model_name=payload.model_name,
            task_type=payload.task_type,
            task_title=payload.title,
            task_description=payload.description,
        )
    except AgentExecutionError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    return {
        "status": "success",
        "model_used": payload.model_name,
        "task_type": payload.task_type,
        "output": output,
    }
