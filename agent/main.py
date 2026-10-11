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

import os
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from pydantic import BaseModel

from shared.schemas import (
    AckStatus,
    TaskAcknowledgement,
    TaskMessage,
    TaskType,
)


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


def _build_prompt(task: TaskMessage) -> str:
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
            results[task.task_id] = TaskResult(task_id=task.task_id, status=ExecutionStatus.COMPLETED, output=output)
        except httpx.HTTPError as exc:
            results[task.task_id] = TaskResult(
                task_id=task.task_id, status=ExecutionStatus.FAILED, error=f"ollama call failed: {exc!r}"
            )

    @app.post("/agent/v1/tasks", response_model=TaskAcknowledgement)
    def receive_task(task: TaskMessage, background_tasks: BackgroundTasks) -> TaskAcknowledgement:
        if task.task_id in acks:
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
