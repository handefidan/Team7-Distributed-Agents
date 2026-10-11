import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from master.decompose import decompose_requirement
from master.main import create_app
from shared.schemas import TaskType

LIBRARY_REQUIREMENT = (
    "Build a library management system where librarians can add, update, and "
    "remove books, members can borrow and return books, and the system tracks "
    "due dates and overdue fines."
)

MOCK_MODEL_OUTPUT = {
    "project_title": "Library Management System",
    "summary": "Borrow/return tracking with due dates and fines.",
    "tasks": [
        {
            "task_id": "T1",
            "title": "Database Schema",
            "category": "database",
            "description": "Books, members, loans tables.",
            "dependencies": [],
        },
        {
            "task_id": "T2",
            "title": "Backend API",
            "category": "backend",
            "description": "Borrow/return/fine endpoints.",
            "dependencies": ["T1"],
        },
    ],
}


def mock_ollama(payload: dict | None = None, status_code: int = 200) -> httpx.MockTransport:
    """Stands in for a local Ollama server so tests never hit the real network."""

    def respond(request: httpx.Request) -> httpx.Response:
        if payload is None:
            return httpx.Response(status_code)
        return httpx.Response(status_code, json={"response": json.dumps(payload)})

    return httpx.MockTransport(respond)


def test_decompose_requirement_uses_local_model():
    client = httpx.AsyncClient(base_url="http://ollama.test", transport=mock_ollama(MOCK_MODEL_OUTPUT))
    result = asyncio.run(decompose_requirement(LIBRARY_REQUIREMENT, client, "llama3"))

    assert result.project_title == "Library Management System"
    categories = {task.category for task in result.tasks}
    assert TaskType.DATABASE in categories
    assert TaskType.BACKEND in categories


def test_decompose_requirement_falls_back_when_model_unreachable():
    client = httpx.AsyncClient(base_url="http://ollama.test", transport=mock_ollama(status_code=500))
    result = asyncio.run(decompose_requirement(LIBRARY_REQUIREMENT, client, "llama3"))

    assert len(result.tasks) > 0  # deterministic fallback, not an error


def test_decompose_endpoint_returns_subtasks():
    client = TestClient(create_app(ollama_transport=mock_ollama(MOCK_MODEL_OUTPUT)))
    response = client.post(
        "/api/v1/requirements/decompose", json={"requirement_text": LIBRARY_REQUIREMENT}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["tasks"]) > 0
    categories = {task["category"] for task in body["tasks"]}
    assert "database" in categories
    assert "backend" in categories


def test_decompose_endpoint_rejects_empty_input():
    client = TestClient(create_app())
    response = client.post("/api/v1/requirements/decompose", json={"requirement_text": "   "})

    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


def test_decompose_endpoint_rejects_missing_field():
    client = TestClient(create_app())
    response = client.post("/api/v1/requirements/decompose", json={})

    assert response.status_code == 422
