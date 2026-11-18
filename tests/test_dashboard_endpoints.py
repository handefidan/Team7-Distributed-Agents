"""Tests for Master Dashboard & Management endpoints."""

import json
from uuid import uuid4
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from master.main import create_app as create_master


def test_system_info_and_dashboard_serve():
    app = create_master(agents_file=None)
    client = TestClient(app)
    with client:
        # Dashboard HTML
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Distributed" in resp.text or "<!DOCTYPE html>" in resp.text

        resp_dash = client.get("/dashboard")
        assert resp_dash.status_code == 200

        # System info
        info = client.get("/api/v1/system/info").json()
        assert "tailscale_ip" in info
        assert "master_port" in info
        assert info["master_port"] == 8000


def test_plan_tasks_and_reset():
    app = create_master(agents_file=None)
    client = TestClient(app)
    with client:
        # Initially empty plan
        tasks = client.get("/api/v1/plan/tasks").json()
        assert tasks == []

        # Decompose & map
        res = client.post("/api/v1/requirements/decompose_and_map", json={"requirement_text": "Sample Library"}).json()
        assert len(res) == 5

        # Check plan tasks
        plan_tasks = client.get("/api/v1/plan/tasks").json()
        assert len(plan_tasks) == 5
        labels = [t["label"] for t in plan_tasks]
        assert "T1" in labels

        # Reset plan
        reset_res = client.post("/api/v1/plan/reset").json()
        assert reset_res["status"] == "ok"
        assert client.get("/api/v1/plan/tasks").json() == []


def test_agents_health_and_delete():
    # Mock transport for an in-process agent
    agent_app = FastAPI()

    @agent_app.get("/agent/v1/health")
    def health():
        return {
            "status": "idle",
            "queue_size": 0,
            "max_queue": 5,
            "supported_types": ["backend"],
            "model": "test-model",
        }

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "test-agent.net":
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent_app), base_url="http://test-agent.net") as c:
                return await c.send(request)
        raise httpx.ConnectError("host unreachable", request=request)

    app = create_master(transport=httpx.MockTransport(mock_handler), agents_file=None)
    client = TestClient(app)
    with client:
        # Register online agent
        client.post(
            "/api/v1/agents",
            json={
                "agent_id": "agent-online",
                "base_url": "http://test-agent.net",
                "model": "test-model",
                "capabilities": {"coding": 80, "reasoning": 80, "context": 80},
            },
        )

        # Register offline agent
        client.post(
            "/api/v1/agents",
            json={
                "agent_id": "agent-offline",
                "base_url": "http://offline-agent.net",
                "model": "offline-model",
                "capabilities": {"coding": 50, "reasoning": 50, "context": 50},
            },
        )

        # Health check
        health_list = client.get("/api/v1/agents/health").json()
        by_id = {h["agent_id"]: h for h in health_list}
        assert by_id["agent-online"]["status"] == "idle"
        assert by_id["agent-offline"]["status"] == "offline"

        # Delete agent
        del_resp = client.delete("/api/v1/agents/agent-offline")
        assert del_resp.status_code == 200
        assert len(client.get("/api/v1/agents").json()) == 1

        # Delete non-existent
        assert client.delete("/api/v1/agents/unknown").status_code == 404


def test_poll_worker_result():
    worker_app = FastAPI()

    @worker_app.post("/agent/v1/tasks")
    def accept_task():
        return {
            "contract_version": "1.0",
            "task_id": "11111111-1111-1111-1111-111111111111",
            "agent_id": "worker-1",
            "status": "accepted",
            "queue_position": 1,
            "duplicate": False,
        }

    status_state = {"status": "running", "output": None}

    @worker_app.get("/agent/v1/tasks/{task_id}/result")
    def get_result(task_id: str):
        return {
            "task_id": task_id,
            "status": status_state["status"],
            "output": status_state["output"],
        }

    @worker_app.get("/agent/v1/health")
    def health():
        return {"status": "idle"}

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=worker_app), base_url="http://worker.net") as c:
            return await c.send(request)

    app = create_master(transport=httpx.MockTransport(mock_handler), agents_file=None)
    client = TestClient(app)
    with client:
        # Register worker
        client.post(
            "/api/v1/agents",
            json={
                "agent_id": "worker-1",
                "base_url": "http://worker.net",
                "model": "worker-model",
                "capabilities": {"coding": 90, "reasoning": 90, "context": 90},
            },
        )

        task_id = "11111111-1111-1111-1111-111111111111"
        dispatch_res = client.post(
            "/api/v1/agents/worker-1/tasks",
            json={
                "task_id": task_id,
                "type": "backend",
                "title": "Backend API",
                "description": "Build endpoints",
                "required_capabilities": {"coding": 80, "reasoning": 60, "context": 0},
            },
        )
        assert dispatch_res.status_code == 200

        # Poll while running
        poll1 = client.get(f"/api/v1/tasks/{task_id}/poll_worker").json()
        assert poll1["status"] == "running"

        # Worker completes
        status_state["status"] = "completed"
        status_state["output"] = "FastAPI Code Sample"

        poll2 = client.get(f"/api/v1/tasks/{task_id}/poll_worker").json()
        assert poll2["status"] == "completed"
        assert poll2["output"] == "FastAPI Code Sample"
