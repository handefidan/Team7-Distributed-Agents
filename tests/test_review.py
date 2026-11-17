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