"""Master agent API: register agents and send tasks to a chosen agent.

Binds to this machine's Tailscale IP (via `tailscale ip -4`, or the
TAILSCALE_IP env var) so the API is only reachable over the tailnet.

Run:  python -m master.main
"""

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

from master.decompose import DecomposeResponse, decompose_requirement
from master.selection import AgentStatus, fetch_statuses, qualifying_agents
from shared.schemas import (
    AckStatus,
    AgentRegistration,
    DispatchRecord,
    DispatchStatus,
    TaskAcknowledgement,
    TaskMessage,
    TaskSpec,
)

AGENT_TIMEOUT_SECONDS = float(os.getenv("AGENT_TIMEOUT_SECONDS", "5"))
AGENT_HEALTH_TIMEOUT_SECONDS = float(os.getenv("AGENT_HEALTH_TIMEOUT_SECONDS", "2"))
AGENTS_FILE = os.getenv(
    "MASTER_AGENTS_FILE", str(Path(__file__).resolve().parent.parent / "backend" / "agents.json")
)
OLLAMA_BASE_URL = os.getenv("MASTER_OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("MASTER_OLLAMA_MODEL", "llama3.2")
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("MASTER_OLLAMA_TIMEOUT_SECONDS", "120"))

logger = logging.getLogger("master")


def load_agents(path: str | Path) -> dict[str, AgentRegistration]:
    """Read the agent registry file. The file is shared with backend/main.py,
    which may write entries without an agent_id, so bad entries are skipped
    with a warning instead of stopping the master from starting."""
    file = Path(path)
    if not file.exists():
        logger.warning("agents file %s not found; starting with no registered agents", file)
        return {}
    agents: dict[str, AgentRegistration] = {}
    for entry in json.loads(file.read_text(encoding="utf-8")):
        try:
            agent = AgentRegistration.model_validate(entry)
        except ValidationError as exc:
            logger.warning("skipping invalid entry in %s: %s (%s)", file, entry, exc.errors()[0]["msg"])
            continue
        agents[agent.agent_id] = agent
    return agents


def create_app(
    transport: httpx.AsyncBaseTransport | None = None,
    ollama_transport: httpx.AsyncBaseTransport | None = None,
    agents_file: str | Path | None = AGENTS_FILE,
) -> FastAPI:
    """`transport` lets tests route agent calls in-process; `ollama_transport`
    does the same for the local Ollama call used to decompose requirements.
    `agents_file` pre-registers agents at startup; None starts empty."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(transport=transport, timeout=AGENT_TIMEOUT_SECONDS) as client:
            app.state.http = client
            yield

    app = FastAPI(title="Master Agent", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Seeded from agents_file; later registrations live in memory only.
    agents: dict[str, AgentRegistration] = load_agents(agents_file) if agents_file else {}
    dispatches: dict[UUID, DispatchRecord] = {}
    ollama_client = httpx.AsyncClient(
        base_url=OLLAMA_BASE_URL, transport=ollama_transport, timeout=OLLAMA_TIMEOUT_SECONDS
    )

    class RequirementPayload(BaseModel):
        requirement_text: str = Field(min_length=1)

    @app.post("/api/v1/requirements/decompose", response_model=DecomposeResponse)
    async def decompose(payload: RequirementPayload) -> DecomposeResponse:
        text = payload.requirement_text.strip()
        if not text:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "requirement_text cannot be empty")
        return await decompose_requirement(text, ollama_client, OLLAMA_MODEL)

    @app.post("/api/v1/agents", response_model=AgentRegistration, status_code=status.HTTP_201_CREATED)
    def register_agent(agent: AgentRegistration) -> AgentRegistration:
        agents[agent.agent_id] = agent
        return agent

    @app.get("/api/v1/agents", response_model=list[AgentRegistration])
    def list_agents() -> list[AgentRegistration]:
        return list(agents.values())

    async def dispatch_to_agent(agent: AgentRegistration, spec: TaskSpec) -> DispatchRecord:
        message = TaskMessage(**spec.model_dump(), agent_id=agent.agent_id)
        url = f"{str(agent.base_url).rstrip('/')}/agent/v1/tasks"
        started = time.perf_counter()

        def fail(code: int, error: str) -> HTTPException:
            dispatches[message.task_id] = DispatchRecord(
                task=message, status=DispatchStatus.FAILED, error=error
            )
            return HTTPException(code, {"task_id": str(message.task_id), "error": error})

        if agent.base_url is None:
            raise fail(status.HTTP_503_SERVICE_UNAVAILABLE, f"agent '{agent.agent_id}' has no base_url configured")

        try:
            response = await app.state.http.post(url, json=message.model_dump(mode="json"))
            response.raise_for_status()
            ack = TaskAcknowledgement.model_validate(response.json())
        except httpx.TimeoutException:
            raise fail(status.HTTP_504_GATEWAY_TIMEOUT, "agent did not answer in time")
        except httpx.HTTPStatusError as exc:
            raise fail(status.HTTP_502_BAD_GATEWAY, f"agent returned HTTP {exc.response.status_code}")
        except httpx.RequestError as exc:
            raise fail(status.HTTP_503_SERVICE_UNAVAILABLE, f"agent unreachable: {exc!r}")
        except (ValueError, ValidationError):
            raise fail(status.HTTP_502_BAD_GATEWAY, "agent sent an invalid acknowledgement")

        if ack.task_id != message.task_id:
            raise fail(status.HTTP_502_BAD_GATEWAY, "acknowledgement is for a different task")

        record = DispatchRecord(
            task=message,
            status=DispatchStatus.ACKNOWLEDGED if ack.status == AckStatus.ACCEPTED else DispatchStatus.REJECTED,
            acknowledgement=ack,
            round_trip_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        dispatches[message.task_id] = record
        return record

    @app.post("/api/v1/agents/{agent_id}/tasks", response_model=DispatchRecord)
    async def send_task(agent_id: str, spec: TaskSpec) -> DispatchRecord:
        agent = agents.get(agent_id)
        if agent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"agent '{agent_id}' is not registered")
        return await dispatch_to_agent(agent, spec)

    @app.post("/api/v1/tasks/dispatch", response_model=DispatchRecord)
    async def dispatch_task(spec: TaskSpec) -> DispatchRecord:
        """Send the task to the best-scoring agent that meets its
        required_capabilities and is currently idle (master/selection.py)."""
        if not agents:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no agents are registered")

        candidates = qualifying_agents(list(agents.values()), spec)
        if not candidates:
            required = spec.required_capabilities.model_dump()
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"no registered agent meets the required capabilities {required}",
            )

        statuses = await fetch_statuses(app.state.http, candidates, AGENT_HEALTH_TIMEOUT_SECONDS)
        chosen = next((a for a in candidates if statuses[a.agent_id] == AgentStatus.IDLE), None)
        if chosen is None:
            summary = ", ".join(f"{agent_id} is {state}" for agent_id, state in statuses.items())
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"every agent that meets the required capabilities is busy or offline: {summary}",
            )
        return await dispatch_to_agent(chosen, spec)

    @app.get("/api/v1/tasks", response_model=list[DispatchRecord])
    def list_tasks() -> list[DispatchRecord]:
        return list(dispatches.values())

    @app.get("/api/v1/tasks/{task_id}", response_model=DispatchRecord)
    def get_task(task_id: UUID) -> DispatchRecord:
        record = dispatches.get(task_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
        return record

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    from shared.network import get_tailscale_ip

    uvicorn.run(app, host=get_tailscale_ip(), port=int(os.getenv("MASTER_PORT", "8000")))
