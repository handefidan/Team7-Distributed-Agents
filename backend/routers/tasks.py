import json
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.services.execution_service import execute_agent_task, AgentExecutionError

from master.classify import required_capabilities
from master.selection import select_agent
from shared.schemas import AgentRegistration, TaskSpec, TaskType

router = APIRouter(prefix="/api/v1/tasks", tags=["Agent Execution"])

AGENTS_FILE = Path(__file__).resolve().parent.parent / "agents.json"

def load_registered_agents() -> list[AgentRegistration]:
    if not AGENTS_FILE.exists():
        print(f"ERROR: agents.json not found at {AGENTS_FILE}")
        return []
    try:
        with open(AGENTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            
            agents = []
            for item in data:
                if "base_url" not in item or not item["base_url"]:
                    item["base_url"] = "http://localhost:11434"
                
                if "agent_id" in item and isinstance(item["agent_id"], str):
                    item["agent_id"] = item["agent_id"].replace(".", "-")
                    
                agents.append(AgentRegistration(**item))
            return agents
            
    except Exception as e:
        print(f"ERROR DETAILS: {e}")
        raise e

class TaskExecutionRequest(BaseModel):
    agent_id: Optional[str] = None
    task_type: TaskType
    title: str
    description: str

@router.post("/execute")
async def run_agent_task_endpoint(payload: TaskExecutionRequest):
    agents = load_registered_agents()
    if not agents:
        raise HTTPException(status_code=500, detail="No agents found in agents.json.")

    target_agent = None

    if payload.agent_id:
        normalized_requested_id = payload.agent_id.replace(".", "-")
        target_agent = next((a for a in agents if a.agent_id == normalized_requested_id), None)
        if not target_agent:
            raise HTTPException(
                status_code=404,
                detail=f"Agent ID '{payload.agent_id}' not found in agents.json."
            )
    else:
        req_caps = required_capabilities(payload.task_type)
        task_spec = TaskSpec(
            task_type=payload.task_type,
            title=payload.title,
            description=payload.description,
            required_capabilities=req_caps
        )
        target_agent = select_agent(agents, task_spec)
        if not target_agent:
            raise HTTPException(
                status_code=400,
                detail=f"No suitable agent found meeting the required capabilities for task type '{payload.task_type}'."
            )

    model_name = target_agent.model

    try:
        output = await execute_agent_task(
            model_name=model_name,
            task_type=payload.task_type.value,
            task_title=payload.title,
            task_description=payload.description,
        )
    except AgentExecutionError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    agent_name = getattr(target_agent, "name", target_agent.agent_id)

    return {
        "status": "success",
        "agent_id": target_agent.agent_id,
        "agent_name": agent_name,
        "model_used": model_name,
        "task_type": payload.task_type,
        "output": output,
    }
