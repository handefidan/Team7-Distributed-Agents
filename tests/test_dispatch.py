import httpx
import pytest
from fastapi.testclient import TestClient

from agent.main import AgentSettings, ExecutionStatus
from agent.main import create_app as create_agent
from master.main import create_app as create_master
from shared.schemas import TaskType

AGENT_URL = "http://agent-a.test"
TASK = {"type": "backend", "title": "Book CRUD API", "description": "Create REST endpoints for books."}


def mock_ollama(response_text: str = "mocked model output") -> httpx.MockTransport:
    """Stands in for a local Ollama server so tests never hit the real network."""

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": response_text})

    return httpx.MockTransport(respond)


def make_master(transport: httpx.AsyncBaseTransport) -> TestClient:
    client = TestClient(create_master(transport=transport))
    client.__enter__()  # runs lifespan so the HTTP client exists
    client.post("/api/v1/agents", json={"agent_id": "agent-a", "base_url": AGENT_URL, "model": "llama3"})
    return client


@pytest.fixture
def master():
    agent = create_agent(
        AgentSettings(agent_id="agent-a", model="llama3", max_queue=2,
                      supported_types={TaskType.BACKEND, TaskType.TESTING}),
        ollama_transport=mock_ollama(),
    )
    client = make_master(httpx.ASGITransport(app=agent))
    yield client
    client.__exit__(None, None, None)


def test_send_task_is_acknowledged(master):
    r = master.post("/api/v1/agents/agent-a/tasks", json=TASK)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "acknowledged"
    ack = body["acknowledgement"]
    assert ack["status"] == "accepted"
    assert ack["task_id"] == body["task"]["task_id"]
    assert ack["queue_position"] == 1
    assert body["round_trip_ms"] is not None

    stored = master.get(f"/api/v1/tasks/{ack['task_id']}")
    assert stored.json()["status"] == "acknowledged"


def test_resending_same_task_id_is_idempotent(master):
    first = master.post("/api/v1/agents/agent-a/tasks", json=TASK).json()
    task_id = first["task"]["task_id"]
    second = master.post("/api/v1/agents/agent-a/tasks", json={**TASK, "task_id": task_id}).json()
    assert second["acknowledgement"]["duplicate"] is True
    assert second["acknowledgement"]["queue_position"] == 1


def test_unsupported_type_is_rejected(master):
    r = master.post("/api/v1/agents/agent-a/tasks", json={**TASK, "type": "frontend"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert "not supported" in r.json()["acknowledgement"]["reason"]


def test_full_queue_is_rejected(master):
    for _ in range(2):
        master.post("/api/v1/agents/agent-a/tasks", json=TASK)
    r = master.post("/api/v1/agents/agent-a/tasks", json=TASK)
    assert r.json()["status"] == "rejected"
    assert "queue is full" in r.json()["acknowledgement"]["reason"]


def test_unknown_agent_returns_404(master):
    assert master.post("/api/v1/agents/nobody/tasks", json=TASK).status_code == 404


def test_invalid_task_returns_422(master):
    assert master.post("/api/v1/agents/agent-a/tasks", json={"type": "cooking"}).status_code == 422


def test_unreachable_agent_is_recorded_as_failed():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    client = make_master(httpx.MockTransport(refuse))
    r = client.post("/api/v1/agents/agent-a/tasks", json=TASK)
    assert r.status_code == 503
    task_id = r.json()["detail"]["task_id"]
    assert client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "failed"
    client.__exit__(None, None, None)


def test_agent_timeout_returns_504():
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client = make_master(httpx.MockTransport(slow))
    assert client.post("/api/v1/agents/agent-a/tasks", json=TASK).status_code == 504
    client.__exit__(None, None, None)


def test_agent_health():
    agent = TestClient(create_agent(AgentSettings(agent_id="agent-b", model="qwen")))
    body = agent.get("/agent/v1/health").json()
    assert body["agent_id"] == "agent-b"
    assert body["status"] == "idle"


def test_accepted_task_runs_against_ollama_and_result_is_fetchable():
    agent = TestClient(create_agent(
        AgentSettings(agent_id="agent-a", model="llama3"),
        ollama_transport=mock_ollama("def add(a, b): return a + b"),
    ))
    task = {**TASK, "agent_id": "agent-a"}
    ack = agent.post("/agent/v1/tasks", json=task).json()

    result = agent.get(f"/agent/v1/tasks/{ack['task_id']}/result").json()
    assert result["status"] == ExecutionStatus.COMPLETED
    assert result["output"] == "def add(a, b): return a + b"


def test_ollama_failure_is_recorded_as_failed_result():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    agent = TestClient(create_agent(
        AgentSettings(agent_id="agent-a", model="llama3"),
        ollama_transport=httpx.MockTransport(refuse),
    ))
    task = {**TASK, "agent_id": "agent-a"}
    ack = agent.post("/agent/v1/tasks", json=task).json()

    result = agent.get(f"/agent/v1/tasks/{ack['task_id']}/result").json()
    assert result["status"] == ExecutionStatus.FAILED
    assert "ollama call failed" in result["error"]


def test_result_for_unknown_task_is_404():
    agent = TestClient(create_agent(AgentSettings(agent_id="agent-a", model="llama3")))
    unknown_id = "00000000-0000-0000-0000-000000000000"
    assert agent.get(f"/agent/v1/tasks/{unknown_id}/result").status_code == 404
