"""Worker agent: receives tasks from the master, acknowledges them, and runs
them against a local Ollama model in the background.

The ack contract (Sprint 2) is unaffected by execution: the agent answers
immediately and runs the task afterwards. Execution status is a preview of
Sprint 3, fetched separately via GET /agent/v1/tasks/{task_id}/result.

Binds to this machine's Tailscale IP (via `tailscale ip -4`, or the
TAILSCALE_IP env var) so the API is only reachable over the tailnet.

Run:
  ollama serve &
  AGENT_ID=agent-a AGENT_MODEL=llama3 python -m agent.main
"""
import json
import os
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from pydantic import BaseModel, ValidationError

from shared.schemas import (
    AckStatus,
    CapabilityScores,
    ReviewOutput,
    TaskAcknowledgement,
    TaskMessage,
    TaskType,
)

REVIEW_AGENT_CAPABILITIES = CapabilityScores(coding=75, reasoning=90, context=80)

@dataclass
class AgentSettings:
    agent_id: str = "agent-local"
    model: str = "unknown"
    supported_types: set[TaskType] = field(default_factory=lambda: set(TaskType))
    max_queue: int = 5
    ollama_base_url: str = "http://localhost:11434"
    ollama_timeout_seconds: float = 120.0

    @classmethod
    def from_env(cls) -> "AgentSettings":
        types = os.getenv("AGENT_SUPPORTED_TYPES")
        return cls(
            agent_id=os.getenv("AGENT_ID", cls.agent_id),
            model=os.getenv("AGENT_MODEL", cls.model),
            supported_types={TaskType(t.strip()) for t in types.split(",")} if types else set(TaskType),
            max_queue=int(os.getenv("AGENT_MAX_QUEUE", cls.max_queue)),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", cls.ollama_base_url),
            ollama_timeout_seconds=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", cls.ollama_timeout_seconds)),
        )


class ExecutionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskResult(BaseModel):
    task_id: UUID
    status: ExecutionStatus
    output: str | None = None
    error: str | None = None


def _build_review_prompt(task: TaskMessage) -> str:
    """Prompt specifically engineered for review tasks to enforce strict JSON output."""
    artifact = task.context.get("artifact") or task.context.get("output") or ""
    return (
        "You are a strict technical Review Agent. Evaluate the following task deliverable.\n"
        f"Task Title: {task.title}\n"
        f"Requirements / Description: {task.description}\n"
        f"Submitted Output to Review:\n{artifact}\n\n"
        "Respond ONLY with a valid JSON object using this exact schema (no markdown, no extra text):\n"
        '{"decision": "approved" or "rejected", "reason": "clear explanation or fix instructions"}'
    )


def _parse_and_validate_review(raw_output: str) -> str:
    """Validate review output; raise ValueError if empty, malformed JSON, or invalid schema."""
    cleaned = raw_output.strip()
    if not cleaned:
        raise ValueError("Review model returned empty output")

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3:
            cleaned = "\n".join(lines[1:-1]).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Review model returned malformed JSON: {exc}") from exc

    try:
        validated = ReviewOutput.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Review output failed schema validation: {exc}") from exc

    return validated.model_dump_json()


def _build_prompt(task: TaskMessage) -> str:
    if task.type == TaskType.REVIEW:
        return _build_review_prompt(task)
    prompt = f"Task type: {task.type}\nTitle: {task.title}\nDescription: {task.description}"
    if task.context:
        prompt += f"\nContext: {task.context}"
    return prompt


def create_app(settings: AgentSettings | None = None, ollama_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """`ollama_transport` lets tests fake the Ollama server instead of calling a real one."""

    settings = settings or AgentSettings.from_env()
    ollama_client = httpx.AsyncClient(
        base_url=settings.ollama_base_url,
        transport=ollama_transport,
        timeout=settings.ollama_timeout_seconds,
    )

    app = FastAPI(title=f"Agent {settings.agent_id}", version="0.3.0")
    queue: list[TaskMessage] = []
    acks: dict = {}  # task_id -> TaskAcknowledgement
    results: dict[UUID, TaskResult] = {}

    async def run_task(task: TaskMessage) -> None:
        results[task.task_id] = TaskResult(task_id=task.task_id, status=ExecutionStatus.RUNNING)
        try:
            response = await ollama_client.post(
                "/api/generate",
                json={"model": settings.model, "prompt": _build_prompt(task), "stream": False},
            )
            response.raise_for_status()
            output = response.json().get("response", "")

            if not output or not output.strip():
                results[task.task_id] = TaskResult(
                    task_id=task.task_id,
                    status=ExecutionStatus.FAILED,
                    error="model returned empty output",
                )
                return

            if task.type == TaskType.REVIEW:
                output = _parse_and_validate_review(output)

            results[task.task_id] = TaskResult(task_id=task.task_id, status=ExecutionStatus.COMPLETED, output=output)
        except ValueError as exc:
            results[task.task_id] = TaskResult(
                task_id=task.task_id,
                status=ExecutionStatus.FAILED,
                error=str(exc),
            )
        except httpx.HTTPError as exc:
            results[task.task_id] = TaskResult(
                task_id=task.task_id, status=ExecutionStatus.FAILED, error=f"ollama call failed: {exc!r}"
            )
        finally:
            queue.remove(task)

    @app.post("/agent/v1/tasks", response_model=TaskAcknowledgement)
    def receive_task(task: TaskMessage, background_tasks: BackgroundTasks) -> TaskAcknowledgement:
        previous = results.get(task.task_id)
        # Same task_id again = same task, answered from the first ack. A task that failed (or was
        # rejected earlier) may be sent again, so the master can retry it after a fix.
        if task.task_id in acks and previous is not None and previous.status != ExecutionStatus.FAILED:
            return acks[task.task_id].model_copy(update={"duplicate": True})

        reason = None
        if task.agent_id != settings.agent_id:
            reason = f"task addressed to '{task.agent_id}', this agent is '{settings.agent_id}'"
        elif task.type not in settings.supported_types:
            reason = f"task type '{task.type}' is not supported by this agent"
        elif len(queue) >= settings.max_queue:
            reason = f"queue is full ({settings.max_queue} tasks)"

        if reason:
            ack = TaskAcknowledgement(
                task_id=task.task_id,
                agent_id=settings.agent_id,
                status=AckStatus.REJECTED,
                reason=reason,
            )
        else:
            queue.append(task)
            results[task.task_id] = TaskResult(task_id=task.task_id, status=ExecutionStatus.QUEUED)
            background_tasks.add_task(run_task, task)
            ack = TaskAcknowledgement(
                task_id=task.task_id,
                agent_id=settings.agent_id,
                status=AckStatus.ACCEPTED,
                queue_position=len(queue),
            )
        acks[task.task_id] = ack
        return ack

    @app.get("/agent/v1/tasks/{task_id}/result", response_model=TaskResult)
    def get_result(task_id: UUID) -> TaskResult:
        result = results.get(task_id)
        if result is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no result for this task_id")
        return result

    @app.get("/agent/v1/health")
    def health() -> dict:
        return {
            "agent_id": settings.agent_id,
            "model": settings.model,
            "status": "busy" if queue else "idle",
            "queue_size": len(queue),
            "max_queue": settings.max_queue,
            "supported_types": sorted(settings.supported_types),
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    from shared.network import get_tailscale_ip

    uvicorn.run(app, host=get_tailscale_ip(), port=int(os.getenv("AGENT_PORT", "8001")))
