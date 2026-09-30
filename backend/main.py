import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMResponseMiddleware, CORSMiddleware
from pydantic import BaseModel
from master_agent import decompose_requirement

app = FastAPI(title="Distributed AI Platform - Master Agent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

REGISTRY_PATH = Path(__file__).parent / "agents.json"

class RequirementPayload(BaseModel):
    requirement_text: str

@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "Master Agent Hub"}

@app.get("/api/agents")
def get_registered_agents():
    if not REGISTRY_PATH.exists():
        raise HTTPException(status_code=404, detail="Agent registry file not found.")
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        agents = json.load(f)
    return agents

@app.post("/api/decompose")
def decompose_tasks(payload: RequirementPayload):
    if not payload.requirement_text.strip():
        raise HTTPException(status_code=400, detail="Requirement text cannot be empty.")
    result = decompose_requirement(payload.requirement_text)
    return result
