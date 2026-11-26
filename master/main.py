"""Master agent API: register agents and send tasks to a chosen agent.

Binds to this machine's Tailscale IP (via `tailscale ip -4`, or the
TAILSCALE_IP env var) so the API is only reachable over the tailnet.

Run:  python -m master.main
"""

import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field, ValidationError

from master.decompose import DecomposeResponse, decompose_requirement
from master.dependency import CyclicDependencyError, DependencyManager, MappedTask
from master.selection import AgentStatus, fetch_statuses, qualifying_agents
from shared.network import get_tailscale_peers
from master.classify import required_capabilities
from master.orchestrator import DispatchResult, RunReport, RunSettings, _wait_for_result, run_plan
from shared.schemas import (
    AckStatus,
    AgentRegistration,
    DispatchRecord,
    DispatchStatus,
    ReviewDecision,
    ReviewOutput,
    TaskAcknowledgement,
    TaskMessage,
    TaskSpec,
    TaskType,
    infer_capabilities,
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
    activity_logs: deque[dict] = deque(maxlen=100)
    _QUIET_GET_PATHS = {
        "/",
        "/dashboard",
        "/api/v1/system/logs",
        "/api/v1/system/info",
        "/api/v1/agents/health",
        "/api/v1/plan/tasks",
        "/api/v1/plan/info",
        "/api/v1/tasks",
    }

    @app.middleware("http")
    async def track_api_activity(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path != "/api/v1/system/logs" and not (
            request.method == "GET" and request.url.path in _QUIET_GET_PATHS
        ):
            client_ip = request.client.host if request.client else "unknown"
            activity_logs.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "client_ip": client_ip,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.perf_counter() - started) * 1000, 1),
            })
        return response 
    ollama_client = httpx.AsyncClient(
        base_url=OLLAMA_BASE_URL, transport=ollama_transport, timeout=OLLAMA_TIMEOUT_SECONDS
    )

    class RequirementPayload(BaseModel):
        requirement_text: str = Field(min_length=1)
        model: str | None = None

    async def list_ollama_models() -> list[str]:
        try:
            resp = await ollama_client.get("/api/tags", timeout=3.0)
            resp.raise_for_status()
            return [m["name"] for m in resp.json().get("models", []) if m.get("name")]
        except Exception:
            return []

    async def resolve_ollama_model(requested: str | None = None) -> str:
        if requested and requested.strip():
            return requested.strip()
        installed = await list_ollama_models()
        if not installed or OLLAMA_MODEL in installed:
            return OLLAMA_MODEL
        return next((m for m in installed if m.startswith(OLLAMA_MODEL)), installed[0])

    @app.get("/api/v1/ollama/models")
    async def ollama_models():
        installed = await list_ollama_models()
        default_model = (
            OLLAMA_MODEL
            if (not installed or OLLAMA_MODEL in installed)
            else next((m for m in installed if m.startswith(OLLAMA_MODEL)), installed[0])
        )
        return {"available": bool(installed), "models": installed, "default_model": default_model}

    @app.post("/api/v1/requirements/decompose", response_model=DecomposeResponse)
    async def decompose(payload: RequirementPayload) -> DecomposeResponse:
        text = payload.requirement_text.strip()
        if not text:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "requirement_text cannot be empty")
        model = await resolve_ollama_model(payload.model)
        return await decompose_requirement(text, ollama_client, model)

    @app.post("/api/v1/requirements/decompose_and_map", response_model=list[MappedTask])
    async def decompose_and_map(payload: RequirementPayload) -> list[MappedTask]:
        """Decomposes requirements and automatically maps text labels (T1, T2) to runtime UUIDs."""
        text = payload.requirement_text.strip()
        if not text:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "requirement_text cannot be empty")
        model = await resolve_ollama_model(payload.model)
        decomp = await decompose_requirement(text, ollama_client, model)
        try:
            dep_manager.reset()
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

    async def review_output(spec: TaskSpec, output: str) -> tuple[str, str | None]:
        """Send a completed task output to an available Review Agent when one is registered."""
        if spec.type == TaskType.REVIEW:
            return "approved", None

        review_req = required_capabilities(TaskType.REVIEW)
        review_spec = TaskSpec(
            type=TaskType.REVIEW,
            title=f"Review: {spec.title}",
            description=spec.description,
            context={"artifact": output, "reviewed_task_id": str(spec.task_id)},
            required_capabilities=review_req,
        )

        # Only trigger review when a dedicated review-capable agent is registered and healthy
        review_candidates = qualifying_agents(list(agents.values()), review_spec)
        if not review_candidates:
            return "approved", None

        statuses = await fetch_statuses(app.state.http, review_candidates, AGENT_HEALTH_TIMEOUT_SECONDS)
        chosen: AgentRegistration | None = None
        for cand in review_candidates:
            if statuses.get(cand.agent_id) != AgentStatus.IDLE:
                continue
            try:
                h_resp = await app.state.http.get(
                    f"{str(cand.base_url).rstrip('/')}/agent/v1/health",
                    timeout=AGENT_HEALTH_TIMEOUT_SECONDS,
                )
                if h_resp.status_code == 200:
                    stypes = h_resp.json().get("supported_types", [])
                    if stypes == [TaskType.REVIEW.value]:
                        chosen = cand
                        break
            except Exception:
                continue

        if chosen is None:
            return "approved", None

        try:
            rec = await dispatch_to_agent(chosen, review_spec)
        except HTTPException:
            return "approved", None

        if rec.status != DispatchStatus.ACKNOWLEDGED:
            return "approved", None

        outcome, raw_review = await _wait_for_result(chosen, rec.task.task_id, fetch_result, settings)
        if outcome != "completed" or not raw_review:
            return "rejected", f"review failed ({outcome}): {raw_review or 'empty review response'}"

        try:
            parsed = ReviewOutput.model_validate_json(raw_review)
        except ValidationError as exc:
            return "rejected", f"malformed review output: {exc}"

        if parsed.decision == ReviewDecision.REJECTED:
            return "rejected", parsed.reason
        return "approved", parsed.reason

    async def run_and_store() -> RunReport:
        try:
            report = await run_plan(dep_manager, send, fetch_result, settings, review_output=review_output)
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

    @app.get("/", response_class=HTMLResponse)
    @app.get("/dashboard", response_class=HTMLResponse)
    def serve_dashboard():
        """Serves the central multi-agent management dashboard."""
        if INDEX_HTML_PATH.exists():
            return HTMLResponse(content=INDEX_HTML_PATH.read_text(encoding="utf-8"))
        return HTMLResponse("<h2>Dashboard frontend/index.html not found.</h2>", status_code=404)

    @app.get("/api/v1/system/info")
    def system_info():
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

    @app.get("/api/v1/system/logs")
    def get_system_logs() -> list[dict]:
        """Recent incoming API requests from all clients/peers on the network."""
        return list(activity_logs)

    @app.delete("/api/v1/system/logs", status_code=status.HTTP_200_OK)
    def clear_system_logs() -> dict[str, str]:
        activity_logs.clear()
        return {"status": "ok"}

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
            base = {**agent.model_dump(mode="json"), "status": "offline", "queue_size": 0}
            if not agent.base_url:
                return {**base, "error": "No base_url configured"}
            try:
                resp = await app.state.http.get(
                    f"{str(agent.base_url).rstrip('/')}/agent/v1/health",
                    timeout=AGENT_HEALTH_TIMEOUT_SECONDS,
                )
                if resp.status_code != 200:
                    return {**base, "error": f"HTTP {resp.status_code}"}
                h = resp.json()
                return {
                    **base,
                    "status": h.get("status", "idle"),
                    "queue_size": h.get("queue_size", 0),
                    "max_queue": h.get("max_queue", 5),
                    "supported_types": h.get("supported_types", []),
                    "model_reported": h.get("model", agent.model),
                }
            except Exception as exc:
                return {**base, "error": str(exc)}

        return await asyncio.gather(*(check_one(a) for a in agents.values()))

    class AutoConnectPayload(BaseModel):
        client_ip: str | None = None
        ports: list[int] = Field(default_factory=lambda: [8001, 8002, 8003])
        models: list[str] = Field(default_factory=list)
        scan_tailnet: bool = False

    @app.post("/api/v1/agents/connect")
    async def auto_connect_agents(request: Request, payload: AutoConnectPayload | None = None):
        """One-click Tailscale connect: discovers running worker agents and local Ollama models
        on the caller's Tailscale IP (and optionally across online tailnet peers), registering
        every reachable worker automatically."""
        req = payload or AutoConnectPayload()
        raw_ip = req.client_ip or (request.client.host if request.client else "127.0.0.1")
        client_ip = "127.0.0.1" if raw_ip == "testclient" else raw_ip

        target_ips: list[str] = [client_ip]
        peers = get_tailscale_peers() if req.scan_tailnet else []
        for peer in peers:
            if peer["ip"] not in target_ips:
                target_ips.append(peer["ip"])

        async def probe_worker(ip: str, port: int) -> AgentRegistration | None:
            base = f"http://{ip}:{port}"
            try:
                resp = await app.state.http.get(f"{base}/agent/v1/health", timeout=AGENT_HEALTH_TIMEOUT_SECONDS)
                if resp.status_code != 200:
                    return None
                h = resp.json()
                raw_id = str(h.get("agent_id") or f"agent-{ip.replace('.', '-')}-{port}")
                agent_id = re.sub(r"[^A-Za-z0-9_-]", "-", raw_id)[:64]
                model = str(h.get("model") or "llama3.2")
                reg = AgentRegistration(
                    agent_id=agent_id,
                    base_url=base,
                    model=model,
                    capabilities=infer_capabilities(model),
                )
                agents[reg.agent_id] = reg
                return reg
            except Exception:
                return None

        async def probe_ollama_models(ip: str) -> list[str]:
            try:
                resp = await app.state.http.get(
                    f"http://{ip}:11434/api/tags", timeout=AGENT_HEALTH_TIMEOUT_SECONDS
                )
                if resp.status_code == 200:
                    return [m["name"] for m in resp.json().get("models", []) if m.get("name")]
            except Exception:
                pass
            return []

        worker_checks = [probe_worker(ip, port) for ip in target_ips for port in req.ports]
        found_workers, remote_models = await asyncio.gather(
            asyncio.gather(*worker_checks),
            probe_ollama_models(client_ip),
        )
        connected = [w.model_dump(mode="json") for w in found_workers if w is not None]
        detected_models = list(dict.fromkeys([*req.models, *remote_models]))

        master_base = str(request.base_url).rstrip("/")
        model_q = f"?model={detected_models[0]}" if detected_models else ""
        return {
            "client_ip": client_ip,
            "connected": connected,
            "detected_models": detected_models,
            "peers_scanned": len(target_ips),
            "join_command": f"curl -sSL '{master_base}/api/v1/agents/join.sh{model_q}' | bash",
        }

    @app.get("/api/v1/agents/join.sh", response_class=PlainTextResponse)
    def agent_join_script(request: Request, model: str = "", agent_id: str = "", port: int = 8001) -> str:
        """One-line shell script that detects the user's Tailscale IP and installed Ollama model(s),
        starts a worker agent, and auto-registers it with this Master."""
        safe_model = re.sub(r"[^A-Za-z0-9_.:/-]", "", model)
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", agent_id)[:64]
        safe_port = port if 1024 <= port <= 65535 else 8001
        master_url = str(request.base_url).rstrip("/")
        return (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'MASTER_URL="{master_url}"\n'
            f'PORT="{safe_port}"\n'
            f'MODEL="{safe_model}"\n'
            f'AGENT_ID="{safe_id}"\n'
            'if [ -z "$AGENT_ID" ]; then AGENT_ID="$(hostname | tr -cs "A-Za-z0-9_-" "-" | sed "s/^-//;s/-$//")-agent"; fi\n'
            'if [ -z "$MODEL" ]; then\n'
            '  MODEL="$(curl -sf http://127.0.0.1:11434/api/tags | python3 -c "import sys,json; m=json.load(sys.stdin).get(\'models\',[]); print(m[0][\'name\'] if m else \'\')" 2>/dev/null || true)"\n'
            'fi\n'
            'if [ -z "$MODEL" ]; then MODEL="auto"; fi\n'
            'echo "[Team7] Connecting $AGENT_ID (model: $MODEL) -> $MASTER_URL via Tailscale..."\n'
            'export MASTER_URL AGENT_ID AGENT_PORT="$PORT" AGENT_MODEL="$MODEL"\n'
            'PY="./.venv/bin/python3"\n'
            '[ -x "$PY" ] || PY="python3"\n'
            'exec "$PY" -m agent.main\n'
        )

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
            if resp.status_code == 404:
                return {"task_id": str(task_id), "status": "queued_or_not_started", "agent_id": agent.agent_id}
            if resp.status_code != 200:
                return {"task_id": str(task_id), "status": "error", "error": f"HTTP {resp.status_code}"}
            data = resp.json()
            st = data.get("status") or "running"
            if st == "completed":
                dep_manager.mark_completed(task_id, data.get("output", ""))
            elif st == "failed":
                dep_manager.mark_failed(task_id)
            return {
                "task_id": str(task_id),
                "status": st,
                "output": data.get("output"),
                "error": data.get("error"),
                "agent_id": agent.agent_id,
            }
        except Exception as exc:
            return {"task_id": str(task_id), "status": "error", "error": str(exc)}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    from shared.network import get_tailscale_ip

    uvicorn.run(app, host=get_tailscale_ip(), port=int(os.getenv("MASTER_PORT", "8000")))
