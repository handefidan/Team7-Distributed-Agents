import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.main import AgentSettings
from agent.main import create_app as create_agent
from master.main import AGENTS_FILE, load_agents
from master.main import create_app as create_master

TASK = {
    "type": "backend",
    "title": "Book CRUD API",
    "description": "Create REST endpoints for books.",
    "required_capabilities": {"coding": 80, "reasoning": 0, "context": 0},
}
ANY_AGENT = {"coding": 0, "reasoning": 0, "context": 0}


def busy_agent() -> FastAPI:
    app = FastAPI()

    @app.get("/agent/v1/health")
    def health() -> dict:
        return {"status": "busy"}

    return app


def route_by_host(apps: dict[str, FastAPI]) -> httpx.MockTransport:
    """Routes each request to the in-process app for its host; unknown hosts are unreachable."""

    async def handle(request: httpx.Request) -> httpx.Response:
        app = apps.get(request.url.host)
        if app is None:
            raise httpx.ConnectError("connection refused", request=request)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{request.url.host}") as client:
            return await client.send(request)

    return httpx.MockTransport(handle)


def make_master(apps: dict[str, FastAPI], agents_file: str | Path | None = None) -> TestClient:
    client = TestClient(create_master(transport=route_by_host(apps), agents_file=agents_file))
    client.__enter__()
    return client


def register(client: TestClient, agent_id: str, score: int) -> None:
    client.post(
        "/api/v1/agents",
        json={
            "agent_id": agent_id,
            "base_url": f"http://{agent_id}.test",
            "model": "llama3",
            "capabilities": {"coding": score, "reasoning": score, "context": score},
        },
    )


@pytest.fixture
def master():
    apps = {
        "agent-strong.test": create_agent(AgentSettings(agent_id="agent-strong", model="llama3")),
        "agent-weak.test": create_agent(AgentSettings(agent_id="agent-weak", model="llama3")),
    }
    client = make_master(apps)
    register(client, "agent-weak", 40)
    register(client, "agent-strong", 90)
    yield client
    client.__exit__(None, None, None)


def test_dispatch_picks_the_qualifying_agent(master):
    r = master.post("/api/v1/tasks/dispatch", json=TASK)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "acknowledged"
    assert body["task"]["agent_id"] == "agent-strong"  # the only one meeting coding >= 80


def test_dispatch_picks_highest_scoring_among_qualifiers(master):
    r = master.post("/api/v1/tasks/dispatch", json={**TASK, "required_capabilities": ANY_AGENT})
    assert r.json()["task"]["agent_id"] == "agent-strong"


def test_busy_agent_is_skipped():
    client = make_master({
        "agent-strong.test": busy_agent(),
        "agent-weak.test": create_agent(AgentSettings(agent_id="agent-weak", model="llama3")),
    })
    register(client, "agent-weak", 40)
    register(client, "agent-strong", 90)

    r = client.post("/api/v1/tasks/dispatch", json={**TASK, "required_capabilities": ANY_AGENT})
    assert r.status_code == 200
    assert r.json()["task"]["agent_id"] == "agent-weak"
    client.__exit__(None, None, None)


def test_offline_agent_is_skipped():
    client = make_master({  # agent-strong.test has no app, so it is unreachable
        "agent-weak.test": create_agent(AgentSettings(agent_id="agent-weak", model="llama3")),
    })
    register(client, "agent-weak", 40)
    register(client, "agent-strong", 90)

    r = client.post("/api/v1/tasks/dispatch", json={**TASK, "required_capabilities": ANY_AGENT})
    assert r.status_code == 200
    assert r.json()["task"]["agent_id"] == "agent-weak"
    client.__exit__(None, None, None)


def test_all_qualifying_agents_busy_or_offline_returns_503():
    client = make_master({"agent-strong.test": busy_agent()})
    register(client, "agent-strong", 90)
    register(client, "agent-other", 90)  # unreachable

    r = client.post("/api/v1/tasks/dispatch", json=TASK)
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "agent-strong is busy" in detail
    assert "agent-other is offline" in detail
    client.__exit__(None, None, None)


def test_no_agent_meets_capabilities_returns_404(master):
    task = {**TASK, "required_capabilities": {"coding": 99, "reasoning": 99, "context": 99}}
    r = master.post("/api/v1/tasks/dispatch", json=task)
    assert r.status_code == 404
    assert "required capabilities" in r.json()["detail"]


def test_no_registered_agents_returns_404():
    client = make_master({})
    r = client.post("/api/v1/tasks/dispatch", json=TASK)
    assert r.status_code == 404
    assert r.json()["detail"] == "no agents are registered"
    client.__exit__(None, None, None)


def test_registration_defaults_capabilities_to_average():
    client = TestClient(create_master(agents_file=None))
    r = client.post(
        "/api/v1/agents", json={"agent_id": "agent-a", "base_url": "http://a.test", "model": "llama3"}
    )
    assert r.status_code == 201
    assert r.json()["capabilities"] == {"coding": 50, "reasoning": 50, "context": 50}


def test_agents_file_is_loaded_at_startup(tmp_path):
    agents_file = tmp_path / "agents.json"
    agents_file.write_text(json.dumps([
        {"agent_id": "agent-weak", "model": "llama3", "base_url": "http://agent-weak.test",
         "capabilities": {"coding": 40, "reasoning": 40, "context": 40}},
        {"agent_id": "no-address", "model": "llama3", "name": "extra fields are ignored"},
        {"name": "written by backend/main.py, no agent_id", "model": "llama3"},
    ]))
    apps = {"agent-weak.test": create_agent(AgentSettings(agent_id="agent-weak", model="llama3"))}
    client = make_master(apps, agents_file=agents_file)

    listed = {a["agent_id"] for a in client.get("/api/v1/agents").json()}
    assert listed == {"agent-weak", "no-address"}  # the invalid entry is skipped

    r = client.post("/api/v1/tasks/dispatch", json={**TASK, "required_capabilities": ANY_AGENT})
    assert r.json()["task"]["agent_id"] == "agent-weak"  # no-address counts as offline
    client.__exit__(None, None, None)


def test_team_agents_file_is_valid():
    agents = load_agents(AGENTS_FILE)
    assert agents, "backend/agents.json should register at least one agent"
