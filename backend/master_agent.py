import os
import json
from pydantic import BaseModel, Field
from typing import List, Dict
from google import genai
from google.genai import types

# Alt görev modeli
class SubTask(BaseModel):
    task_id: str = Field(description="Unique task identifier, e.g., T1, T2")
    title: str = Field(description="Brief title of the technical subtask")
    category: str = Field(description="Domain: frontend, backend, database, testing, documentation")
    description: str = Field(description="Detailed explanation of what needs to be implemented")
    required_capabilities: Dict[str, int] = Field(description="Required minimum scores (0-100) for coding, reasoning, and context")
    dependencies: List[str] = Field(description="List of task_ids that must be completed before this task can start")

# Genel yanıt şeması
class DecomposeResponse(BaseModel):
    project_title: str
    summary: str
    tasks: List[SubTask]

SYSTEM_PROMPT = """
You are an expert Chief Software Architect and Master Orchestrator Agent.
Analyze the user's software requirement document thoroughly.
Decompose it into clear, modular, non-overlapping technical subtasks (Frontend, Backend, Database, Testing, Integration, Documentation).
For each task:
1. Specify clear dependencies (e.g., Backend APIs might depend on Database Schema).
2. Estimate the required capability scores (coding, reasoning, context) on a 0-100 scale.
Output strictly according to the provided JSON schema.
"""

def decompose_requirement(requirement_text: str) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        # API anahtarı olmadan test edebilmek için güvenli mock fallback
        return {
            "project_title": "Automated Software Project",
            "summary": "Mock task decomposition fallback (GEMINI_API_KEY not configured).",
            "tasks": [
                {
                    "task_id": "T1",
                    "title": "Database Schema Design",
                    "category": "database",
                    "description": "Design relational models and migrations.",
                    "required_capabilities": {"coding": 70, "reasoning": 85, "context": 70},
                    "dependencies": []
                },
                {
                    "task_id": "T2",
                    "title": "Backend REST APIs",
                    "category": "backend",
                    "description": "Implement authentication and core entity CRUD endpoints.",
                    "required_capabilities": {"coding": 90, "reasoning": 80, "context": 85},
                    "dependencies": ["T1"]
                },
                {
                    "task_id": "T3",
                    "title": "Frontend UI Components",
                    "category": "frontend",
                    "description": "Build responsive user dashboard and forms.",
                    "required_capabilities": {"coding": 85, "reasoning": 70, "context": 75},
                    "dependencies": ["T2"]
                },
                {
                    "task_id": "T4",
                    "title": "Integration and Automated Tests",
                    "category": "testing",
                    "description": "Write end-to-end and unit test suites.",
                    "required_capabilities": {"coding": 80, "reasoning": 90, "context": 80},
                    "dependencies": ["T3"]
                }
            ]
        }

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"Software Requirement Document:\n{requirement_text}",
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=DecomposeResponse,
            temperature=0.2,
        ),
    )
    return json.loads(response.text)
