import httpx
import pytest
from fastapi.testclient import TestClient

from agent.main import AgentSettings
from agent.main import create_app as create_agent
from master.main import create_app as create_master
from shared.schemas import TaskType

STRONG_AGENT_URL = "http://agent-strong.test"
WEAK_AGENT_URL = "http://agent-weak.test"

TASK = {
    "type": "backend",
    "title": "Book CRUD API",
    "description": "Create REST endpoints for books.",
    "required_capabilities": {"coding": 80, "reasoning": 0, "context": 0},
}


def route_by_url(apps: dict[str, object]) -> httpx.MockTransport:
    """Routes requests to the in-process agent app matching the request's host."""

    async def handle(request: httpx.Request) -> httpx.Response:
        app = apps[str(request.url.copy_with(query=None, path="/"))]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=str(request.url.copy_with(path=""))) as client:
            return await client.send(request)

    return httpx.MockTransport(handle)


@pytest.fixture
def master():
    strong = create_agent(AgentSettings(agent_id="agent-strong", model="llama3"))
    weak = create_agent(AgentSettings(agent_id="agent-weak", model="llama3"))
    transport = route_by_url({STRONG_AGENT_URL + "/": strong, WEAK_AGENT_URL + "/": weak})

    client = TestClient(create_master(transport=transport))
    client.__enter__()
    client.post(
        "/api/v1/agents",
        json={
            "agent_id": "agent-weak",
            "base_url": WEAK_AGENT_URL,
            "model": "llama3",
            "capabilities": {"coding": 40, "reasoning": 40, "context": 40},
        },
    )
    client.post(
        "/api/v1/agents",
        json={
            "agent_id": "agent-strong",
            "base_url": STRONG_AGENT_URL,
            "model": "llama3",
            "capabilities": {"coding": 90, "reasoning": 90, "context": 90},
        },
    )
    yield client
    client.__exit__(None, None, None)


def test_dispatch_picks_the_qualifying_agent(master):
    r = master.post("/api/v1/tasks/dispatch", json=TASK)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "acknowledged"
    assert body["task"]["agent_id"] == "agent-strong"  # the only one meeting coding >= 80


def test_dispatch_picks_highest_scoring_among_qualifiers(master):
    task = {**TASK, "required_capabilities": {"coding": 0, "reasoning": 0, "context": 0}}
    r = master.post("/api/v1/tasks/dispatch", json=task)
    assert r.json()["task"]["agent_id"] == "agent-strong"  # both qualify, strong has the higher total


def test_dispatch_returns_404_when_no_agent_qualifies(master):
    task = {**TASK, "required_capabilities": {"coding": 99, "reasoning": 99, "context": 99}}
    r = master.post("/api/v1/tasks/dispatch", json=task)
    assert r.status_code == 404


def test_registration_defaults_capabilities_to_average():
    client = TestClient(create_master())
    r = client.post(
        "/api/v1/agents", json={"agent_id": "agent-a", "base_url": "http://a.test", "model": "llama3"}
    )
    assert r.status_code == 201
    assert r.json()["capabilities"] == {"coding": 50, "reasoning": 50, "context": 50}
