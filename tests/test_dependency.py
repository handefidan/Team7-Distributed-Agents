"""Automated tests for Sprint 3 Dependency Resolution."""

import pytest
from master.decompose import DecomposeResponse, SubTask
from master.dependency import DependencyManager, TaskLifecycleStatus
from shared.schemas import TaskType


@pytest.fixture
def sample_decomposition() -> DecomposeResponse:
    return DecomposeResponse(
        project_title="Test Project",
        summary="Testing dependency mapping",
        tasks=[
            SubTask(
                task_id="T1",
                title="Database Schema",
                category=TaskType.DATABASE,
                description="Create tables",
                dependencies=[],
            ),
            SubTask(
                task_id="T2",
                title="Backend API",
                category=TaskType.BACKEND,
                description="Create REST endpoints",
                dependencies=["T1"],
            ),
            SubTask(
                task_id="T3",
                title="Automated Tests",
                category=TaskType.TESTING,
                description="Write unit tests",
                dependencies=["T2"],
            ),
        ],
    )


def test_label_to_uuid_mapping(sample_decomposition: DecomposeResponse):
    manager = DependencyManager()
    mapped_tasks = manager.register_decomposition(sample_decomposition)

    assert len(mapped_tasks) == 3
    assert "T1" in manager.label_map
    assert "T2" in manager.label_map
    assert "T3" in manager.label_map

    t1_uuid = manager.label_map["T1"]
    t2_uuid = manager.label_map["T2"]

    assert len(manager.tasks[t1_uuid].dependency_uuids) == 0
    assert manager.tasks[t1_uuid].status == TaskLifecycleStatus.PENDING
    assert manager.tasks[t2_uuid].dependency_uuids == [t1_uuid]
    assert manager.tasks[t2_uuid].status == TaskLifecycleStatus.BLOCKED


def test_execution_order_enforcement(sample_decomposition: DecomposeResponse):
    manager = DependencyManager()
    manager.register_decomposition(sample_decomposition)

    t1_uuid = manager.label_map["T1"]
    t2_uuid = manager.label_map["T2"]

    assert manager.is_ready_for_dispatch(t2_uuid) is False

    manager.mark_completed(t1_uuid)
    assert manager.is_ready_for_dispatch(t2_uuid) is True

# ---- Sprint 3 Week 5 additions (Nehir) ----
from uuid import uuid4

from fastapi.testclient import TestClient

from master.dependency import CyclicDependencyError
from master.main import create_app


def _sub(label: str, deps: list[str], category: TaskType = TaskType.BACKEND) -> SubTask:
    return SubTask(task_id=label, title=f"Task {label}", category=category,
                   description=f"Do {label}", dependencies=deps)


def _decomp(*tasks: SubTask) -> DecomposeResponse:
    return DecomposeResponse(project_title="p", summary="s", tasks=list(tasks))


def test_cycle_is_detected():
    manager = DependencyManager()
    with pytest.raises(CyclicDependencyError) as exc:
        manager.register_decomposition(_decomp(_sub("T1", ["T2"]), _sub("T2", ["T1"])))
    assert "T1" in str(exc.value) and "T2" in str(exc.value)


def test_self_dependency_is_a_cycle():
    with pytest.raises(CyclicDependencyError):
        DependencyManager().register_decomposition(_decomp(_sub("T1", ["T1"])))


def test_long_cycle_is_detected():
    with pytest.raises(CyclicDependencyError):
        DependencyManager().register_decomposition(
            _decomp(_sub("T1", ["T3"]), _sub("T2", ["T1"]), _sub("T3", ["T2"]))
        )


def test_diamond_is_not_a_cycle():
    manager = DependencyManager()
    manager.register_decomposition(
        _decomp(_sub("T1", []), _sub("T2", ["T1"]), _sub("T3", ["T1"]), _sub("T4", ["T2", "T3"]))
    )
    assert [t.label for t in manager.get_ready_tasks()] == ["T1"]


def test_unknown_dependency_label_is_ignored():
    manager = DependencyManager()
    manager.register_decomposition(_decomp(_sub("T1", ["T9"])))
    t1 = manager.tasks[manager.label_map["T1"]]
    assert t1.dependency_uuids == [] and t1.status == TaskLifecycleStatus.PENDING


def test_ready_tasks_follow_completion_order():
    manager = DependencyManager()
    manager.register_decomposition(
        _decomp(_sub("T1", []), _sub("T2", ["T1"]), _sub("T3", ["T1"]), _sub("T4", ["T2", "T3"]))
    )
    u = manager.label_map
    manager.mark_completed(u["T1"])
    assert {t.label for t in manager.get_ready_tasks()} == {"T2", "T3"}
    manager.mark_completed(u["T2"])
    assert {t.label for t in manager.get_ready_tasks()} == {"T3"}  # T4 still waits for T3
    manager.mark_completed(u["T3"])
    assert {t.label for t in manager.get_ready_tasks()} == {"T4"}


def test_failed_task_keeps_dependents_blocked():
    manager = DependencyManager()
    manager.register_decomposition(_decomp(_sub("T1", []), _sub("T2", ["T1"])))
    manager.mark_failed(manager.label_map["T1"])
    assert manager.get_ready_tasks() == []
    assert manager.is_ready_for_dispatch(manager.label_map["T2"]) is False


def test_task_spec_has_uuids_capabilities_and_dependency_outputs():
    manager = DependencyManager()
    manager.register_decomposition(_decomp(_sub("T1", [], TaskType.DATABASE), _sub("T2", ["T1"])))
    t1, t2 = manager.label_map["T1"], manager.label_map["T2"]
    assert manager.to_task_spec(t2).context == {}
    manager.mark_completed(t1, output="CREATE TABLE books(...)")
    spec = manager.to_task_spec(t2)
    assert spec.task_id == t2 and spec.depends_on == [t1]
    assert spec.context == {"dependency_outputs": {"T1": "CREATE TABLE books(...)"}}
    assert spec.required_capabilities.coding == 80  # backend: coding is the main skill


# ---- API level ----
def _agent_transport():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agent/v1/health":
            return httpx.Response(200, json={"status": "idle"})
        body = __import__("json").loads(request.content)
        return httpx.Response(200, json={
            "task_id": body["task_id"], "agent_id": body["agent_id"], "status": "accepted",
            "queue_position": 1, "duplicate": False,
            "received_at": "2026-01-01T00:00:00Z", "contract_version": "1.0",
        })
    return httpx.MockTransport(handler)


def _client() -> TestClient:
    import httpx
    from master.decompose import mock_decomposition

    def ollama(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)  # forces the mock decomposition (T1..T5)

    app = create_app(transport=_agent_transport(), ollama_transport=httpx.MockTransport(ollama))
    client = TestClient(app)
    client.post("/api/v1/agents", json={
        "agent_id": "a1", "base_url": "http://agent:8001", "model": "m",
        "capabilities": {"coding": 90, "reasoning": 90, "context": 90},
    })
    return client


def test_api_full_dependency_flow():
    with _client() as client:
        _flow(client)


def _flow(client: TestClient) -> None:
    plan = client.post("/api/v1/requirements/decompose_and_map",
                       json={"requirement_text": "Library system"}).json()
    by_label = {t["label"]: t for t in plan}
    first = [t for t in plan if not t["dependency_uuids"]]
    blocked = next(t for t in plan if t["dependency_uuids"])

    # blocked task cannot be sent yet
    spec_blocked = client.get(f"/api/v1/plan/tasks/{blocked['task_uuid']}/spec").json()
    assert client.post("/api/v1/tasks/dispatch", json=spec_blocked).status_code == 400

    # ready list only holds tasks without open prerequisites
    ready = client.get("/api/v1/plan/ready").json()
    assert {t["label"] for t in ready} == {t["label"] for t in first}

    # send the first task, finish it with an output, the next one is released with that output
    spec1 = client.get(f"/api/v1/plan/tasks/{first[0]['task_uuid']}/spec").json()
    assert client.post("/api/v1/tasks/dispatch", json=spec1).json()["status"] == "acknowledged"
    # a dispatched task is no longer "ready"
    assert first[0]["label"] not in {t["label"] for t in client.get("/api/v1/plan/ready").json()}
    client.post(f"/api/v1/tasks/{first[0]['task_uuid']}/complete", json={"output": "OUT1"})
    ready_labels = {t["label"] for t in client.get("/api/v1/plan/ready").json()}
    assert by_label["T2"]["label"] in ready_labels
    spec2 = client.get(f"/api/v1/plan/tasks/{by_label['T2']['task_uuid']}/spec").json()
    assert spec2["context"]["dependency_outputs"]["T1"] == "OUT1"
    assert client.post("/api/v1/tasks/dispatch", json=spec2).json()["status"] == "acknowledged"


def test_api_cycle_returns_422(monkeypatch):
    import master.main as m

    async def fake(text, client, model):
        return _decomp(_sub("T1", ["T2"]), _sub("T2", ["T1"]))

    monkeypatch.setattr(m, "decompose_requirement", fake)
    client = _client()
    r = client.post("/api/v1/requirements/decompose_and_map", json={"requirement_text": "x"})
    assert r.status_code == 422 and "cycle" in r.json()["detail"]


def test_api_spec_of_unknown_task_is_404():
    assert _client().get(f"/api/v1/plan/tasks/{uuid4()}/spec").status_code == 404


def test_api_failed_task_blocks_dependents():
    client = _client()
    plan = client.post("/api/v1/requirements/decompose_and_map",
                       json={"requirement_text": "x"}).json()
    t1 = next(t for t in plan if t["label"] == "T1")
    client.post(f"/api/v1/tasks/{t1['task_uuid']}/fail")
    assert client.get("/api/v1/plan/ready").json() == []
