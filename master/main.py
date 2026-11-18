"""Master agent API: register agents and send tasks to a chosen agent.

Binds to this machine's Tailscale IP (via `tailscale ip -4`, or the
TAILSCALE_IP env var) so the API is only reachable over the tailnet.

Run:  python -m master.main
"""

import asyncio
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
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError

from master.decompose import DecomposeResponse, decompose_requirement
from master.dependency import CyclicDependencyError, DependencyManager, MappedTask
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
INDEX_HTML_PATH = Path(__file__).resolve().parent.parent / "frontend" / "index.html"

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
        if "agent_id" in entry:
            # Same normalization as backend/routers/tasks.py: dots aren't allowed in agent ids.
            entry["agent_id"] = entry["agent_id"].replace(".", "-")
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
    dep_manager = DependencyManager() 
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

    @app.post("/api/v1/requirements/decompose_and_map", response_model=list[MappedTask])
    async def decompose_and_map(payload: RequirementPayload) -> list[MappedTask]:
        """Decomposes requirements and automatically maps text labels (T1, T2) to runtime UUIDs."""
        text = payload.requirement_text.strip()
        if not text:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "requirement_text cannot be empty")
        decomp = await decompose_requirement(text, ollama_client, OLLAMA_MODEL)
        try:
            return dep_manager.register_decomposition(decomp)
        except CyclicDependencyError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    
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
        if record.status == DispatchStatus.ACKNOWLEDGED:
            dep_manager.mark_dispatched(message.task_id, message.agent_id)
        return record

    @app.post("/api/v1/agents/{agent_id}/tasks", response_model=DispatchRecord)
    async def send_task(agent_id: str, spec: TaskSpec) -> DispatchRecord:
        agent = agents.get(agent_id)
        if agent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"agent '{agent_id}' is not registered")
        if spec.task_id in dep_manager.tasks and not dep_manager.is_ready_for_dispatch(spec.task_id):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Task '{spec.task_id}' cannot be dispatched yet because its prerequisite dependencies are not completed.",
            )
        return await dispatch_to_agent(agent, spec)

    @app.post("/api/v1/tasks/dispatch", response_model=DispatchRecord)
    async def dispatch_task(spec: TaskSpec) -> DispatchRecord:
        """Send the task to the best-scoring agent that meets its
        required_capabilities and is currently idle (master/selection.py)."""
        if spec.task_id in dep_manager.tasks and not dep_manager.is_ready_for_dispatch(spec.task_id):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Task '{spec.task_id}' cannot be dispatched yet because its prerequisite dependencies are not completed.",
            )
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

    class CompletePayload(BaseModel):
        output: str | None = None

    @app.get("/api/v1/plan/ready", response_model=list[MappedTask])
    def ready_tasks() -> list[MappedTask]:
        """Mapped tasks whose prerequisites are all completed and that can be dispatched now."""
        return dep_manager.get_ready_tasks()

    @app.get("/api/v1/plan/tasks/{task_id}/spec", response_model=TaskSpec)
    def task_spec(task_id: UUID) -> TaskSpec:
        """Ready-to-dispatch TaskSpec of a mapped task (real UUIDs, dependency outputs in context)."""
        if task_id not in dep_manager.tasks:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "task is not part of a decomposed plan")
        return dep_manager.to_task_spec(task_id)

    @app.post("/api/v1/tasks/{task_id}/complete", status_code=status.HTTP_200_OK)
    def complete_task(task_id: UUID, payload: CompletePayload | None = None) -> dict[str, str]:
        """Marks a task as completed (optionally storing its output) and releases dependent tasks."""
        dep_manager.mark_completed(task_id, payload.output if payload else None)
        return {"task_id": str(task_id), "status": "completed"}

    @app.post("/api/v1/tasks/{task_id}/fail", status_code=status.HTTP_200_OK)
    def fail_task(task_id: UUID) -> dict[str, str]:
        """Marks a task as failed; tasks that depend on it stay blocked."""
        dep_manager.mark_failed(task_id)
        return {"task_id": str(task_id), "status": "failed"}
    
    @app.get("/api/v1/tasks", response_model=list[DispatchRecord])
    def list_tasks() -> list[DispatchRecord]:
        return list(dispatches.values())

    @app.get("/api/v1/tasks/{task_id}", response_model=DispatchRecord)
    def get_task(task_id: UUID) -> DispatchRecord:
        record = dispatches.get(task_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
        return record

    @app.get("/", response_class=HTMLResponse)
    @app.get("/dashboard", response_class=HTMLResponse)
    def serve_dashboard():
        """Serves the central multi-agent management dashboard."""
        if INDEX_HTML_PATH.exists():
            return HTMLResponse(content=INDEX_HTML_PATH.read_text(encoding="utf-8"))
        return HTMLResponse("<h2>Dashboard frontend/index.html not found.</h2>", status_code=404)

    @app.get("/api/v1/system/info")
    def system_info():
        current_ip = "127.0.0.1"
        try:
            from shared.network import get_tailscale_ip
            current_ip = get_tailscale_ip()
        except Exception:
            current_ip = os.getenv("TAILSCALE_IP", "127.0.0.1 (Tailscale offline)")
        port = int(os.getenv("MASTER_PORT", "8000"))
        return {
            "tailscale_ip": current_ip,
            "master_port": port,
            "master_base_url": f"http://{current_ip}:{port}",
            "ollama_model": OLLAMA_MODEL,
            "ollama_base_url": OLLAMA_BASE_URL,
            "registered_agents_count": len(agents),
            "total_tasks_count": len(dep_manager.tasks),
            "ready_tasks_count": len(dep_manager.get_ready_tasks()),
        }

    @app.get("/api/v1/plan/tasks", response_model=list[MappedTask])
    def list_plan_tasks() -> list[MappedTask]:
        """All tasks in the active decomposed plan with their dependency status."""
        return list(dep_manager.tasks.values())

    @app.get("/api/v1/plan/info")
    def plan_info():
        return {
            "project_title": dep_manager.project_title,
            "summary": dep_manager.summary,
            "tasks_count": len(dep_manager.tasks),
            "is_mock": dep_manager.summary.startswith("Mock decomposition"),
        }

    @app.post("/api/v1/plan/reset", status_code=status.HTTP_200_OK)
    def reset_plan() -> dict[str, str]:
        """Clears the active decomposition plan."""
        dep_manager.reset()
        return {"status": "ok", "message": "Plan cleared"}

    @app.get("/api/v1/agents/health")
    async def check_agents_health():
        """Live health status and telemetry for every registered agent."""
        async def check_one(agent: AgentRegistration):
            agent_data = agent.model_dump(mode="json")
            if not agent.base_url:
                return {**agent_data, "status": "offline", "error": "No base_url configured", "queue_size": 0}
            url = f"{str(agent.base_url).rstrip('/')}/agent/v1/health"
            try:
                resp = await app.state.http.get(url, timeout=AGENT_HEALTH_TIMEOUT_SECONDS)
                if resp.status_code == 200:
                    h = resp.json()
                    return {
                        **agent_data,
                        "status": h.get("status", "idle"),
                        "queue_size": h.get("queue_size", 0),
                        "max_queue": h.get("max_queue", 5),
                        "supported_types": h.get("supported_types", []),
                        "model_reported": h.get("model", agent.model),
                    }
                return {**agent_data, "status": "offline", "error": f"HTTP {resp.status_code}", "queue_size": 0}
            except Exception as exc:
                return {**agent_data, "status": "offline", "error": str(exc), "queue_size": 0}

        tasks = [check_one(a) for a in agents.values()]
        return await asyncio.gather(*tasks) if tasks else []

    @app.delete("/api/v1/agents/{agent_id}", status_code=status.HTTP_200_OK)
    def delete_agent(agent_id: str):
        if agent_id not in agents:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"agent '{agent_id}' not found")
        del agents[agent_id]
        return {"message": f"Agent '{agent_id}' removed successfully"}

    @app.get("/api/v1/tasks/{task_id}/poll_worker")
    async def poll_worker_result(task_id: UUID):
        """Poll the assigned worker agent for the background execution result."""
        record = dispatches.get(task_id)
        if not record:
            task = dep_manager.tasks.get(task_id)
            if task:
                return {"task_id": str(task_id), "status": task.status.value, "output": task.output}
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Task dispatch record not found")

        agent = agents.get(record.task.agent_id)
        if not agent or not agent.base_url:
            return {"task_id": str(task_id), "status": "unknown", "error": "Assigned agent not available"}

        url = f"{str(agent.base_url).rstrip('/')}/agent/v1/tasks/{task_id}/result"
        try:
            resp = await app.state.http.get(url, timeout=AGENT_HEALTH_TIMEOUT_SECONDS)
            if resp.status_code == 200:
                result_data = resp.json()
                exec_status = result_data.get("status")
                if exec_status == "completed":
                    output = result_data.get("output", "")
                    dep_manager.mark_completed(task_id, output)
                    return {
                        "task_id": str(task_id),
                        "status": "completed",
                        "output": output,
                        "agent_id": agent.agent_id,
                    }
                elif exec_status == "failed":
                    dep_manager.mark_failed(task_id)
                    return {
                        "task_id": str(task_id),
                        "status": "failed",
                        "error": result_data.get("error"),
                        "agent_id": agent.agent_id,
                    }
                else:
                    return {
                        "task_id": str(task_id),
                        "status": exec_status or "running",
                        "agent_id": agent.agent_id,
                    }
            elif resp.status_code == 404:
                return {"task_id": str(task_id), "status": "queued_or_not_started", "agent_id": agent.agent_id}
            else:
                return {"task_id": str(task_id), "status": "error", "error": f"HTTP {resp.status_code}"}
        except Exception as exc:
            return {"task_id": str(task_id), "status": "error", "error": str(exc)}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    from shared.network import get_tailscale_ip

    uvicorn.run(app, host=get_tailscale_ip(), port=int(os.getenv("MASTER_PORT", "8000")))
