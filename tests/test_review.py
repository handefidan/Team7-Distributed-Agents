import json
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from agent.main import (
    REVIEW_AGENT_CAPABILITIES,
    AgentSettings,
    ExecutionStatus,
    create_app,
)
from master.classify import required_capabilities
from shared.schemas import TaskMessage, TaskType


def _mock_ollama_client(response_text: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": response_text})

    return httpx.MockTransport(handler)


def _make_review_task(agent_id: str = "agent-reviewer") -> TaskMessage:
    return TaskMessage(
        task_id=uuid4(),
        agent_id=agent_id,
        type=TaskType.REVIEW,
        title="Review login endpoint implementation",
        description="Verify JWT token generation and error handling.",
        context={"artifact": "def login(): return {'token': 'abc'}"},
    )


def test_review_agent_profile_meets_required_capabilities():
    req = required_capabilities(TaskType.REVIEW)
    assert REVIEW_AGENT_CAPABILITIES.reasoning >= req.reasoning
    assert REVIEW_AGENT_CAPABILITIES.coding >= req.coding
    assert REVIEW_AGENT_CAPABILITIES.context >= req.context


def test_review_agent_approves_valid_output():
    mock_response = json.dumps({"decision": "approved", "reason": "Code meets all requirements cleanly."})
    settings = AgentSettings(agent_id="agent-reviewer", model="llama3.2:3b", supported_types={TaskType.REVIEW})
    app = create_app(settings=settings, ollama_transport=_mock_ollama_client(mock_response))

    task = _make_review_task()
    with TestClient(app) as client:
        ack = client.post("/agent/v1/tasks", json=task.model_dump(mode="json")).json()
        assert ack["status"] == "accepted"

        result = client.get(f"/agent/v1/tasks/{task.task_id}/result").json()
        assert result["status"] == ExecutionStatus.COMPLETED
        parsed = json.loads(result["output"])
        assert parsed["decision"] == "approved"
        assert "meets all requirements" in parsed["reason"]


def test_review_agent_rejects_with_reason():
    mock_response = json.dumps({
        "decision": "rejected",
        "reason": "Missing password hashing and HTTP 401 unauthorized handling.",
    })
    settings = AgentSettings(agent_id="agent-reviewer", model="llama3.2:3b", supported_types={TaskType.REVIEW})
    app = create_app(settings=settings, ollama_transport=_mock_ollama_client(mock_response))

    task = _make_review_task()
    with TestClient(app) as client:
        client.post("/agent/v1/tasks", json=task.model_dump(mode="json"))
        result = client.get(f"/agent/v1/tasks/{task.task_id}/result").json()
        assert result["status"] == ExecutionStatus.COMPLETED
        parsed = json.loads(result["output"])
        assert parsed["decision"] == "rejected"
        assert "password hashing" in parsed["reason"]


def test_review_agent_marks_empty_output_as_failed():
    settings = AgentSettings(agent_id="agent-reviewer", model="llama3.2:3b", supported_types={TaskType.REVIEW})
    app = create_app(settings=settings, ollama_transport=_mock_ollama_client("   "))

    task = _make_review_task()
    with TestClient(app) as client:
        client.post("/agent/v1/tasks", json=task.model_dump(mode="json"))
        result = client.get(f"/agent/v1/tasks/{task.task_id}/result").json()
        assert result["status"] == ExecutionStatus.FAILED
        assert "empty output" in result["error"]


def test_review_agent_marks_malformed_output_as_failed():
    settings = AgentSettings(agent_id="agent-reviewer", model="llama3.2:3b", supported_types={TaskType.REVIEW})
    app = create_app(settings=settings, ollama_transport=_mock_ollama_client("Looks okay I guess, not JSON"))

    task = _make_review_task()
    with TestClient(app) as client:
        client.post("/agent/v1/tasks", json=task.model_dump(mode="json"))
        result = client.get(f"/agent/v1/tasks/{task.task_id}/result").json()
        assert result["status"] == ExecutionStatus.FAILED
        assert "malformed JSON" in result["error"]

# ---------------------------------------------------------------------------
# Sprint 4 Week 8: End-to-End Review + Error Recovery & Health Failover Tests
# ---------------------------------------------------------------------------

class _EndToEndNetwork(httpx.AsyncBaseTransport):
    """In-process router connecting Master, Worker Agents, and Review Agent."""

    def __init__(self) -> None:
        self.apps: dict[str, object] = {}
        self.offline_hosts: set[str] = set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in self.offline_hosts:
            raise httpx.ConnectError(f"Agent {host} is offline", request=request)
        return await httpx.ASGITransport(app=self.apps[host]).handle_async_request(request)


def _worker_ollama(label: str, output_text: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": f"[{label}] {output_text}"})

    return httpx.MockTransport(handler)


def _dynamic_review_ollama(decisions_by_substring: list[tuple[str, dict | str]]) -> httpx.MockTransport:
    """Returns different review JSON (or malformed string) depending on submitted artifact content."""

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["prompt"]
        for needle, reply in decisions_by_substring:
            if needle in prompt:
                text = reply if isinstance(reply, str) else json.dumps(reply)
                return httpx.Response(200, json={"response": text})
        return httpx.Response(200, json={"response": json.dumps({"decision": "approved", "reason": "Looks good."})})

    return httpx.MockTransport(handler)


def test_review_rejection_triggers_error_recovery_and_reassigns_to_next_agent():
    """When Review Agent rejects worker-1's output with a reason, Master records
    'review_rejected', excludes worker-1, and reassigns the task to worker-2."""
    from master.main import create_app as create_master
    from master.orchestrator import RunSettings

    net = _EndToEndNetwork()
    fast_settings = RunSettings(
        max_attempts=3,
        busy_wait_seconds=0.01,
        max_busy_wait_seconds=0.05,
        poll_interval_seconds=0.01,
        result_timeout_seconds=0.5,
    )

    net.apps["worker1.test"] = create_app(
        settings=AgentSettings(agent_id="worker-1", model="m", supported_types={TaskType.BACKEND}),
        ollama_transport=_worker_ollama("worker-1", "buggy_no_auth_code"),
    )
    net.apps["worker2.test"] = create_app(
        settings=AgentSettings(agent_id="worker-2", model="m", supported_types={TaskType.BACKEND}),
        ollama_transport=_worker_ollama("worker-2", "fixed_jwt_auth_code"),
    )
    net.apps["reviewer.test"] = create_app(
        settings=AgentSettings(agent_id="agent-reviewer", model="llama3.2:3b", supported_types={TaskType.REVIEW}),
        ollama_transport=_dynamic_review_ollama([
            ("buggy_no_auth_code", {"decision": "rejected", "reason": "Missing JWT authentication check."}),
            ("fixed_jwt_auth_code", {"decision": "approved", "reason": "JWT authentication verified."}),
        ]),
    )

    def single_task_plan(request: httpx.Request) -> httpx.Response:
        plan = {
            "project_title": "Auth Service",
            "summary": "Single backend task",
            "tasks": [
                {
                    "task_id": "T1",
                    "title": "Build auth endpoint",
                    "category": "backend",
                    "description": "Implement JWT auth",
                    "dependencies": [],
                }
            ],
        }
        return httpx.Response(200, json={"response": json.dumps(plan)})

    master_app = create_master(
        transport=net,
        ollama_transport=httpx.MockTransport(single_task_plan),
        agents_file=None,
        run_settings=fast_settings,
    )

    with TestClient(master_app) as client:
        client.post("/api/v1/agents", json={
            "agent_id": "worker-1",
            "base_url": "http://worker1.test",
            "model": "m",
            "capabilities": {"coding": 95, "reasoning": 90, "context": 90},
        })
        client.post("/api/v1/agents", json={
            "agent_id": "worker-2",
            "base_url": "http://worker2.test",
            "model": "m",
            "capabilities": {"coding": 85, "reasoning": 85, "context": 85},
        })
        client.post("/api/v1/agents", json={
            "agent_id": "agent-reviewer",
            "base_url": "http://reviewer.test",
            "model": "llama3.2:3b",
            "capabilities": {"coding": 75, "reasoning": 90, "context": 80},
        })

        client.post("/api/v1/requirements/decompose_and_map", json={"requirement_text": "Auth API"})
        report = client.post("/api/v1/plan/run", params={"wait": "true"}).json()

        assert report["finished"] is True
        assert report["completed"] == ["T1"]
        assert report["failed"] == []

        tasks = client.get("/api/v1/plan/tasks").json()
        t1 = tasks[0]
        assert t1["status"] == "completed"
        assert "[worker-2] fixed_jwt_auth_code" in t1["output"]
        assert [(a["agent_id"], a["outcome"]) for a in t1["attempts"]] == [
            ("worker-1", "review_rejected"),
            ("worker-2", "completed"),
        ]
        assert "Missing JWT authentication check" in t1["attempts"][0]["detail"]


def test_malformed_review_output_does_not_approve_and_triggers_recovery():
    """If Review Agent returns malformed JSON (marking review task FAILED), Master
    must NOT silently approve the artifact and must trigger error recovery."""
    from master.main import create_app as create_master
    from master.orchestrator import RunSettings

    net = _EndToEndNetwork()
    fast_settings = RunSettings(
        max_attempts=2,
        busy_wait_seconds=0.01,
        max_busy_wait_seconds=0.05,
        poll_interval_seconds=0.01,
        result_timeout_seconds=0.5,
    )

    net.apps["worker1.test"] = create_app(
        settings=AgentSettings(agent_id="worker-1", model="m", supported_types={TaskType.BACKEND}),
        ollama_transport=_worker_ollama("worker-1", "first_attempt"),
    )
    net.apps["worker2.test"] = create_app(
        settings=AgentSettings(agent_id="worker-2", model="m", supported_types={TaskType.BACKEND}),
        ollama_transport=_worker_ollama("worker-2", "second_attempt"),
    )
    net.apps["reviewer.test"] = create_app(
        settings=AgentSettings(agent_id="agent-reviewer", model="llama3.2:3b", supported_types={TaskType.REVIEW}),
        ollama_transport=_dynamic_review_ollama([
            ("first_attempt", "NOT_VALID_JSON_AT_ALL"),
            ("second_attempt", {"decision": "approved", "reason": "Valid on second try."}),
        ]),
    )

    def single_task_plan(request: httpx.Request) -> httpx.Response:
        plan = {
            "project_title": "Test",
            "summary": "Single task",
            "tasks": [
                {
                    "task_id": "T1",
                    "title": "Build service",
                    "category": "backend",
                    "description": "Service logic",
                    "dependencies": [],
                }
            ],
        }
        return httpx.Response(200, json={"response": json.dumps(plan)})

    master_app = create_master(
        transport=net,
        ollama_transport=httpx.MockTransport(single_task_plan),
        agents_file=None,
        run_settings=fast_settings,
    )

    with TestClient(master_app) as client:
        client.post("/api/v1/agents", json={
            "agent_id": "worker-1",
            "base_url": "http://worker1.test",
            "model": "m",
            "capabilities": {"coding": 95, "reasoning": 90, "context": 90},
        })
        client.post("/api/v1/agents", json={
            "agent_id": "worker-2",
            "base_url": "http://worker2.test",
            "model": "m",
            "capabilities": {"coding": 85, "reasoning": 85, "context": 85},
        })
        client.post("/api/v1/agents", json={
            "agent_id": "agent-reviewer",
            "base_url": "http://reviewer.test",
            "model": "llama3.2:3b",
            "capabilities": {"coding": 75, "reasoning": 90, "context": 80},
        })

        client.post("/api/v1/requirements/decompose_and_map", json={"requirement_text": "Service"})
        report = client.post("/api/v1/plan/run", params={"wait": "true"}).json()

        assert report["completed"] == ["T1"]
        t1 = client.get("/api/v1/plan/tasks").json()[0]
        assert [(a["agent_id"], a["outcome"]) for a in t1["attempts"]] == [
            ("worker-1", "review_rejected"),
            ("worker-2", "completed"),
        ]
        assert "review failed" in t1["attempts"][0]["detail"]


def test_health_monitoring_detects_offline_agent_and_reassigns_task():
    """Verifies that when an agent is down/offline in health monitoring
    (/api/v1/agents/health), Master detects it and assigns the task to the online agent."""
    from master.main import create_app as create_master
    from master.orchestrator import RunSettings

    net = _EndToEndNetwork()
    fast_settings = RunSettings(
        max_attempts=3,
        busy_wait_seconds=0.01,
        max_busy_wait_seconds=0.05,
        poll_interval_seconds=0.01,
        result_timeout_seconds=0.5,
    )

    net.apps["primary.test"] = create_app(
        settings=AgentSettings(agent_id="agent-primary", model="m", supported_types={TaskType.BACKEND}),
        ollama_transport=_worker_ollama("primary", "primary_out"),
    )
    net.apps["backup.test"] = create_app(
        settings=AgentSettings(agent_id="agent-backup", model="m", supported_types={TaskType.BACKEND}),
        ollama_transport=_worker_ollama("backup", "backup_out"),
    )

    # Simulate primary agent going offline/down
    net.offline_hosts.add("primary.test")

    def single_task_plan(request: httpx.Request) -> httpx.Response:
        plan = {
            "project_title": "Failover Test",
            "summary": "Single task",
            "tasks": [
                {
                    "task_id": "T1",
                    "title": "Create API",
                    "category": "backend",
                    "description": "Backend API",
                    "dependencies": [],
                }
            ],
        }
        return httpx.Response(200, json={"response": json.dumps(plan)})

    master_app = create_master(
        transport=net,
        ollama_transport=httpx.MockTransport(single_task_plan),
        agents_file=None,
        run_settings=fast_settings,
    )

    with TestClient(master_app) as client:
        client.post("/api/v1/agents", json={
            "agent_id": "agent-primary",
            "base_url": "http://primary.test",
            "model": "m",
            "capabilities": {"coding": 99, "reasoning": 99, "context": 99},
        })
        client.post("/api/v1/agents", json={
            "agent_id": "agent-backup",
            "base_url": "http://backup.test",
            "model": "m",
            "capabilities": {"coding": 80, "reasoning": 80, "context": 80},
        })

        # 1. Verify status monitoring reports primary as offline and backup as idle
        health_list = {item["agent_id"]: item["status"] for item in client.get("/api/v1/agents/health").json()}
        assert health_list["agent-primary"] == "offline"
        assert health_list["agent-backup"] == "idle"

        # 2. Verify dispatch and plan execution skip offline primary and assign to backup
        client.post("/api/v1/requirements/decompose_and_map", json={"requirement_text": "Failover"})
        report = client.post("/api/v1/plan/run", params={"wait": "true"}).json()

        assert report["completed"] == ["T1"]
        assert report["failed"] == []
        t1 = client.get("/api/v1/plan/tasks").json()[0]
        assert [(a["agent_id"], a["outcome"]) for a in t1["attempts"]] == [("agent-backup", "completed")]
