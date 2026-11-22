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

_FALLBACK_SPECS = [
    ("T1", "Database Schema Design", "database", "Design relational tables, schemas, and migrations.", (70, 85, 70), []),
    ("T2", "Backend API Services", "backend", "Implement authentication and core business REST endpoints.", (90, 80, 85), ["T1"]),
    ("T3", "Frontend UI Components", "frontend", "Build responsive user views, search forms, and interaction modals.", (85, 70, 75), ["T2"]),
    ("T4", "Unit and Integration Testing", "test", "Create automated test suites and validation mocks.", (80, 90, 80), ["T3"]),
    ("T5", "API & System Documentation", "docs", "Generate OpenAPI specifications and developer setup guides.", (60, 75, 80), ["T2"]),
]


def decompose_requirement(requirement_text: str) -> dict:
    try:
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
                    "task_id": tid,
                    "title": title,
                    "category": cat,
                    "description": desc,
                    "required_capabilities": {"coding": c, "reasoning": r, "context": x},
                    "dependencies": deps,
                }
                for tid, title, cat, desc, (c, r, x), deps in _FALLBACK_SPECS
            ],
        }