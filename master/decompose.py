"""Splits a free-text software requirement into categorized subtasks.

Sprint 1: the master agent's first capability. Independent of the
task-dispatch contract (Sprint 2+) — this only produces the subtask list;
sending those subtasks to agents is a separate step.

Uses a local Ollama model (same as the worker agents) rather than a cloud
LLM API — no external API key required.
"""

import json
import logging

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from master.classify import required_capabilities
from shared.schemas import CapabilityScores, TaskSpec, TaskType

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
    required_capabilities: CapabilityScores = Field(
        default_factory=CapabilityScores,
        description="Set from the task type (master/classify.py); any value the model sends is replaced.",
    )

    @model_validator(mode="after")
    def _classify(self) -> "SubTask":
        self.required_capabilities = required_capabilities(self.category)
        return self

    def to_task_spec(self) -> TaskSpec:
        """The message the master can dispatch to an agent for this subtask."""
        return TaskSpec(
            type=self.category,
            title=self.title,
            description=self.description,
            required_capabilities=self.required_capabilities,
        )


class DecomposeResponse(BaseModel):
    project_title: str
    summary: str
    tasks: list[SubTask] = Field(min_length=1)


_MOCK_SPECS: list[tuple[str, str, TaskType, str, list[str]]] = [
    ("T1", "Database Schema Design", TaskType.DATABASE, "Design the relational models and migrations.", []),
    ("T2", "Backend REST APIs", TaskType.BACKEND, "Implement authentication and core entity CRUD endpoints.", ["T1"]),
    ("T3", "Frontend UI", TaskType.FRONTEND, "Build the user-facing views and forms.", ["T2"]),
    ("T4", "Automated Tests", TaskType.TESTING, "Write unit and integration tests for the above.", ["T2", "T3"]),
    ("T5", "Documentation", TaskType.DOCUMENTATION, "Document the API and setup instructions.", ["T2"]),
]


def mock_decomposition(requirement_text: str) -> DecomposeResponse:
    """Deterministic fallback used when Ollama is unreachable or returns an
    unparseable response, so the endpoint degrades gracefully instead of
    failing the caller outright."""
    return DecomposeResponse(
        project_title="Automated Software Project",
        summary=f"Mock decomposition (local model unavailable) for: {requirement_text[:120]}",
        tasks=[
            SubTask(task_id=tid, title=title, category=cat, description=desc, dependencies=deps)
            for tid, title, cat, desc, deps in _MOCK_SPECS
        ],
    )


logger = logging.getLogger("master.decompose")


async def decompose_requirement(
    requirement_text: str, client: httpx.AsyncClient, model: str
) -> DecomposeResponse:
    """Ask the local Ollama model to decompose `requirement_text`, falling
    back to a fixed mock if it's unreachable or answers with something
    that doesn't fit the schema."""
    logger.info("Decomposing requirement using Ollama model '%s'...", model)
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
        data = json.loads(raw)
        result = DecomposeResponse.model_validate(data)
        logger.info(
            "Ollama model '%s' successfully generated project '%s' with %d subtasks!",
            model,
            result.project_title,
            len(result.tasks),
        )
        return result
    except Exception as exc:
        logger.warning(
            "Ollama call failed (%s: %s). Falling back to mock decomposition.",
            type(exc).__name__,
            exc,
        )
        return mock_decomposition(requirement_text)
