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
from pydantic import BaseModel, Field, ValidationError

from master.decompose import DecomposeResponse, decompose_requirement
from master.dependency import CyclicDependencyError, DependencyManager, MappedTask
from master.orchestrator import DispatchResult, RunReport, RunSettings, run_plan
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


def run_settings_from_env() -> RunSettings:
    """Plan-run limits (see docs/error-recovery.md); every value can be set with an env var."""
    d = RunSettings()
    return RunSettings(
        max_attempts=int(os.getenv("MAX_TASK_ATTEMPTS", d.max_attempts)),
        busy_wait_seconds=float(os.getenv("BUSY_WAIT_SECONDS", d.busy_wait_seconds)),
        max_busy_wait_seconds=float(os.getenv("MAX_BUSY_WAIT_SECONDS", d.max_busy_wait_seconds)),
        poll_interval_seconds=float(os.getenv("RESULT_POLL_INTERVAL_SECONDS", d.poll_interval_seconds)),
        result_timeout_seconds=float(os.getenv("TASK_RESULT_TIMEOUT_SECONDS", d.result_timeout_seconds)),
    )

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
    run_settings: RunSettings | None = None,
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
            dep_manager.mark_dispatched(message.task_id)
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

    async def select_and_dispatch(spec: TaskSpec, exclude: frozenset[str] | set[str] = frozenset()) -> DispatchRecord:
        """Send the task to the best-scoring agent that meets its required_capabilities
        and is currently idle (master/selection.py). Agents in `exclude` are skipped."""
        if not agents:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no agents are registered")

        candidates = qualifying_agents(list(agents.values()), spec)
        if not candidates:
            required = spec.required_capabilities.model_dump()
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"no registered agent meets the required capabilities {required}",
            )
        candidates = [a for a in candidates if a.agent_id not in exclude]
        if not candidates:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"no other agent left to try (already tried: {', '.join(sorted(exclude))})",
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

    @app.post("/api/v1/tasks/dispatch", response_model=DispatchRecord)
    async def dispatch_task(spec: TaskSpec) -> DispatchRecord:
        """Send the task to the best-scoring agent that meets its
        required_capabilities and is currently idle (master/selection.py)."""
        if spec.task_id in dep_manager.tasks and not dep_manager.is_ready_for_dispatch(spec.task_id):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Task '{spec.task_id}' cannot be dispatched yet because its prerequisite dependencies are not completed.",
            )
        return await select_and_dispatch(spec)

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
    
    # ---- running a whole plan (Sprint 4 Week 7) ----
    settings = run_settings or run_settings_from_env()
    run_state: dict = {"task": None, "report": RunReport()}

    async def send(spec: TaskSpec, exclude: set[str]) -> DispatchResult:
        """Adapter for master/orchestrator.py: turns the dispatch outcome into a DispatchResult."""
        try:
            record = await select_and_dispatch(spec, exclude)
        except HTTPException as exc:
            if isinstance(exc.detail, dict):  # the chosen agent failed (unreachable, timeout, bad answer)
                failed = dispatches.get(spec.task_id)
                agent = agents.get(failed.task.agent_id) if failed else None
                return DispatchResult("unreachable", agent, exc.detail.get("error"))
            if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
                return DispatchResult("all_busy", detail=str(exc.detail))
            return DispatchResult("no_agent", detail=str(exc.detail))
        agent = agents[record.task.agent_id]
        if record.status == DispatchStatus.REJECTED:
            return DispatchResult("rejected", agent, record.acknowledgement.reason if record.acknowledgement else None)
        return DispatchResult("accepted", agent)

    async def fetch_result(agent: AgentRegistration, task_id: UUID) -> dict | None:
        try:
            response = await app.state.http.get(
                f"{str(agent.base_url).rstrip('/')}/agent/v1/tasks/{task_id}/result",
                timeout=AGENT_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            return None

    async def run_and_store() -> RunReport:
        try:
            report = await run_plan(dep_manager, send, fetch_result, settings)
        finally:
            run_state["task"] = None
        run_state["report"] = report
        return report

    @app.post("/api/v1/plan/run", response_model=RunReport)
    async def run_plan_endpoint(wait: bool = False) -> RunReport:
        """Run the decomposed plan: send every ready task, collect results, release the next tasks,
        and recover from failures (docs/error-recovery.md). With `wait=true` the call returns when
        the whole plan is done; otherwise it starts the run and returns at once (watch /plan/tasks)."""
        if run_state["task"] is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "the plan is already running")
        if not dep_manager.tasks:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "no plan yet: call decompose_and_map first")
        run_state["report"] = RunReport()
        if wait:
            run_state["task"] = asyncio.current_task()
            return await run_and_store()
        run_state["task"] = asyncio.create_task(run_and_store())
        return run_state["report"]

    @app.get("/api/v1/plan/run", response_model=RunReport)
    def plan_run_status() -> RunReport:
        """Result of the last plan run (`finished` is false while a run is in progress or none started)."""
        return run_state["report"]

    @app.get("/api/v1/plan/tasks", response_model=list[MappedTask])
    def plan_tasks() -> list[MappedTask]:
        """Every task of the plan with its status, output and attempt history."""
        return list(dep_manager.tasks.values())

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
