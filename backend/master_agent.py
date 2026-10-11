import json
import ollama
from typing import List, Dict
from pydantic import BaseModel, Field

# 1. Subtask schema
class SubTask(BaseModel):
    task_id: str = Field(description="Unique task identifier, e.g., T1, T2")
    title: str = Field(description="Brief title of the technical subtask")
    category: str = Field(description="Domain strictly one of: frontend, backend, database, test, docs")
    description: str = Field(description="Detailed explanation of what needs to be implemented")
    required_capabilities: Dict[str, int] = Field(description="Required minimum scores (0-100) for coding, reasoning, and context")
    dependencies: List[str] = Field(description="List of task_ids that must be completed before this task can start")

# 2. Complete response schema
class DecomposeResponse(BaseModel):
    project_title: str
    summary: str
    tasks: List[SubTask]

SYSTEM_PROMPT = """
You are an expert Chief Software Architect acting as a Master Agent for agile task decomposition.
Analyze the user's software requirement and decompose it into granular subtasks covering ALL FIVE required domains:
1. frontend
2. backend
3. database
4. test
5. docs

Requirements:
- Ensure at least one task exists for each of the 5 categories (frontend, backend, database, test, docs).
- Specify task dependencies (e.g., Backend APIs might depend on Database Schema).
- Score required capabilities (coding, reasoning, context) on a 0-100 scale.
- Return ONLY valid JSON adhering strictly to the schema.
"""

def decompose_requirement(requirement_text: str) -> dict:
    try:
        # Call the local Ollama server (running at http://127.0.0.1:11434)
        response = ollama.chat(
            model="llama3.2",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Software Requirement:\n{requirement_text}"}
            ],
            format=DecomposeResponse.model_json_schema(),
            options={"temperature": 0.2}
        )
        return json.loads(response["message"]["content"])

    except Exception as e:
        print(f"[Warning] Local Ollama call failed: {e}. Using fallback.")
        return {
            "project_title": "Automated Software Project",
            "summary": "Local Ollama fallback mode.",
            "tasks": [
                {
                    "task_id": "T1",
                    "title": "Database Schema Design",
                    "category": "database",
                    "description": "Design relational tables, schemas, and migrations.",
                    "required_capabilities": {"coding": 70, "reasoning": 85, "context": 70},
                    "dependencies": []
                },
                {
                    "task_id": "T2",
                    "title": "Backend API Services",
                    "category": "backend",
                    "description": "Implement authentication and core business REST endpoints.",
                    "required_capabilities": {"coding": 90, "reasoning": 80, "context": 85},
                    "dependencies": ["T1"]
                },
                {
                    "task_id": "T3",
                    "title": "Frontend UI Components",
                    "category": "frontend",
                    "description": "Build responsive user views, search forms, and interaction modals.",
                    "required_capabilities": {"coding": 85, "reasoning": 70, "context": 75},
                    "dependencies": ["T2"]
                },
                {
                    "task_id": "T4",
                    "title": "Unit and Integration Testing",
                    "category": "test",
                    "description": "Create automated test suites and validation mocks.",
                    "required_capabilities": {"coding": 80, "reasoning": 90, "context": 80},
                    "dependencies": ["T3"]
                },
                {
                    "task_id": "T5",
                    "title": "API & System Documentation",
                    "category": "docs",
                    "description": "Generate OpenAPI specifications and developer setup guides.",
                    "required_capabilities": {"coding": 60, "reasoning": 75, "context": 80},
                    "dependencies": ["T2"]
                }
            ]
        }