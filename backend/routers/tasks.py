from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.services.execution_service import execute_agent_task

router = APIRouter(prefix="/api/v1/tasks", tags=["Agent Execution"])

class TaskExecutionRequest(BaseModel):
    model_name: str
    task_type: str
    title: str
    description: str

@router.post("/execute")
async def run_agent_task_endpoint(payload: TaskExecutionRequest):
    """
    API endpoint to dispatch and execute tasks using local LLM models via Ollama.
    """
    result_output = await execute_agent_task(
        model_name=payload.model_name,
        task_type=payload.task_type,
        task_title=payload.title,
        task_description=payload.description
    )
    return {
        "status": "success",
        "model_used": payload.model_name,
        "task_type": payload.task_type,
        "output": result_output
    }