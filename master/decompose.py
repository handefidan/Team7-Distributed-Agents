"""Splits a free-text software requirement into categorized subtasks.

Sprint 1: the master agent's first capability. Independent of the
task-dispatch contract (Sprint 2+) — this only produces the subtask list;
sending those subtasks to agents is a separate step.

Uses a local Ollama model (same as the worker agents) rather than a cloud
LLM API — no external API key required.
"""

import json

import httpx
from pydantic import BaseModel, Field, ValidationError

from shared.schemas import TaskType

SYSTEM_PROMPT = """You are an expert Chief Software Architect and Master Orchestrator Agent.
Analyze the user's software requirement document thoroughly.
Decompose it into clear, modular, non-overlapping technical subtasks.
Each task's "category" must be exactly one of: frontend, backend, database, testing, documentation, integration, requirements.
Specify clear dependencies via "dependencies" (e.g. backend APIs depend on the database schema task's "task_id").
Respond with ONLY a JSON object matching this shape, no other text:
{"project_title": str, "summary": str, "tasks": [{"task_id": str, "title": str, "category": str, "description": str, "dependencies": [str]}]}"""


class SubTask(BaseModel):
    task_id: str = Field(description="Unique id within this response, e.g. T1, T2")
    title: str
    category: TaskType
    description: str
    dependencies: list[str] = Field(default_factory=list, description="task_ids that must finish first")


class DecomposeResponse(BaseModel):
    project_title: str
    summary: str
    tasks: list[SubTask] = Field(min_length=1)


def mock_decomposition(requirement_text: str) -> DecomposeResponse:
    """Deterministic fallback used when Ollama is unreachable or returns an
    unparseable response, so the endpoint degrades gracefully instead of
    failing the caller outright."""
    return DecomposeResponse(
        project_title="Automated Software Project",
        summary=f"Mock decomposition (local model unavailable) for: {requirement_text[:120]}",
        tasks=[
            SubTask(
                task_id="T1",
                title="Database Schema Design",
                category=TaskType.DATABASE,
                description="Design the relational models and migrations.",
            ),
            SubTask(
                task_id="T2",
                title="Backend REST APIs",
                category=TaskType.BACKEND,
                description="Implement authentication and core entity CRUD endpoints.",
                dependencies=["T1"],
            ),
            SubTask(
                task_id="T3",
                title="Frontend UI",
                category=TaskType.FRONTEND,
                description="Build the user-facing views and forms.",
                dependencies=["T2"],
            ),
            SubTask(
                task_id="T4",
                title="Automated Tests",
                category=TaskType.TESTING,
                description="Write unit and integration tests for the above.",
                dependencies=["T2", "T3"],
            ),
            SubTask(
                task_id="T5",
                title="Documentation",
                category=TaskType.DOCUMENTATION,
                description="Document the API and setup instructions.",
                dependencies=["T2"],
            ),
        ],
    )


async def decompose_requirement(
    requirement_text: str, client: httpx.AsyncClient, model: str
) -> DecomposeResponse:
    """Ask the local Ollama model to decompose `requirement_text`, falling
    back to a fixed mock if it's unreachable or answers with something
    that doesn't fit the schema."""
    try:
        response = await client.post(
            "/api/generate",
            json={
                "model": model,
                "system": SYSTEM_PROMPT,
                "prompt": f"Software Requirement Document:\n{requirement_text}",
                "format": "json",
                "stream": False,
            },
        )
        response.raise_for_status()
        raw = response.json().get("response", "")
        return DecomposeResponse.model_validate(json.loads(raw))
    except (httpx.HTTPError, json.JSONDecodeError, ValidationError):
        return mock_decomposition(requirement_text)
