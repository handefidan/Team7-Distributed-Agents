"""Master agent API: register agents and send tasks to a chosen agent.

Run:  uvicorn master.main:app --port 8000
"""

import os
import time
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, status
from pydantic import ValidationError

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


def create_app(transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """`transport` lets tests route agent calls in-process instead of over the network."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(transport=transport, timeout=AGENT_TIMEOUT_SECONDS) as client:
            app.state.http = client
            yield

    app = FastAPI(title="Master Agent", version="0.2.0", lifespan=lifespan)
    # In-memory for Sprint 2; replace with the Sprint 1 agent registry / a database later.
    agents: dict[str, AgentRegistration] = {}
    dispatches: dict[UUID, DispatchRecord] = {}

    @app.post("/api/v1/agents", response_model=AgentRegistration, status_code=status.HTTP_201_CREATED)
    def register_agent(agent: AgentRegistration) -> AgentRegistration:
        agents[agent.agent_id] = agent
        return agent

    @app.get("/api/v1/agents", response_model=list[AgentRegistration])
    def list_agents() -> list[AgentRegistration]:
        return list(agents.values())

    @app.post("/api/v1/agents/{agent_id}/tasks", response_model=DispatchRecord)
    async def send_task(agent_id: str, spec: TaskSpec) -> DispatchRecord:
        agent = agents.get(agent_id)
        if agent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"agent '{agent_id}' is not registered")

        message = TaskMessage(**spec.model_dump(), agent_id=agent_id)
        url = f"{str(agent.base_url).rstrip('/')}/agent/v1/tasks"
        started = time.perf_counter()

        def fail(code: int, error: str) -> HTTPException:
            dispatches[message.task_id] = DispatchRecord(
                task=message, status=DispatchStatus.FAILED, error=error
            )
            return HTTPException(code, {"task_id": str(message.task_id), "error": error})

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
