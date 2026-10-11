import sys
import json
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.master_agent import decompose_requirement

sys.path.insert(0, str(Path(__file__).parent))

app = FastAPI(title="Distributed AI Platform - Master Agent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

REGISTRY_PATH = Path(__file__).parent / "agents.json"

# --- Models ---
class RequirementPayload(BaseModel):
    requirement_text: str

class CapabilityScores(BaseModel):
    coding: Optional[int] = Field(None, ge=0, le=100, description="Score 0-100")
    reasoning: Optional[int] = Field(None, ge=0, le=100, description="Score 0-100")
    context: Optional[int] = Field(None, ge=0, le=100, description="Score 0-100")

class AgentModel(BaseModel):
    name: str = Field(..., min_length=1, description="Unique agent identifier/name")
    model: str = Field(..., min_length=1, description="LLM model identifier")
    capabilities: Optional[CapabilityScores] = Field(default_factory=CapabilityScores)

# --- Helper Functions ---
def _load_agents() -> list:
    if not REGISTRY_PATH.exists():
        return []
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_agents(agents: list) -> None:
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(agents, f, indent=2, ensure_ascii=False)

# --- Endpoints ---
@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "Master Agent Hub"}

@app.get("/api/agents")
def get_registered_agents():
    """List all registered agents."""
    return _load_agents()

@app.post("/api/agents", status_code=status.HTTP_201_CREATED)
def add_agent(agent: AgentModel):
    """Add a new agent. Rejects duplicate names and invalid scores."""
    agents = _load_agents()
    
    if any(a["name"].strip().lower() == agent.name.strip().lower() for a in agents):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent with name '{agent.name}' already exists."
        )

    agents.append(agent.model_dump())
    _save_agents(agents)
    return {"message": "Agent registered successfully", "agent": agent}

@app.delete("/api/agents/{agent_name}")
def remove_agent(agent_name: str):
    """Remove an agent by name."""
    agents = _load_agents()
    updated_agents = [a for a in agents if a["name"].strip().lower() != agent_name.strip().lower()]

    if len(updated_agents) == len(agents):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_name}' not found."
        )

    _save_agents(updated_agents)
    return {"message": f"Agent '{agent_name}' removed successfully."}

@app.post("/api/decompose")
def decompose_tasks(payload: RequirementPayload):
    if not payload.requirement_text.strip():
        raise HTTPException(status_code=400, detail="Requirement text cannot be empty.")
    result = decompose_requirement(payload.requirement_text)
    return result