"""End-to-end plan runs: real master + real agent apps, only the network and Ollama are faked.

Covers Sprint 4 Week 7: result collection, dependency order, and the recovery rules in
docs/error-recovery.md (retry on another agent, wait when busy, give up cleanly).
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.main import AgentSettings
from agent.main import create_app as create_agent
from master.dependency import DependencyManager, TaskLifecycleStatus
from master.main import create_app as create_master
from master.orchestrator import RunSettings
from shared.schemas import TaskType

FAST = RunSettings(
    max_attempts=3,
    busy_wait_seconds=0.01,
    max_busy_wait_seconds=0.05,
    poll_interval_seconds=0.01,
    result_timeout_seconds=0.3,
    max_result_errors=2,
)
ALL = {"coding": 100, "reasoning": 100, "context": 100}


class Network(httpx.AsyncBaseTransport):
    """Routes the master's calls to in-process agent apps by host name, and can break things."""

    def __init__(self) -> None:
        self.apps: dict[str, object] = {}
        self.fail_post: set[str] = set()  # task POST raises ConnectError
        self.fail_result: set[str] = set()  # result GET raises ConnectError
        self.stuck: set[str] = set()  # result GET always says "running"
        self.busy_health: dict[str, int] = {}  # host -> how many health calls still answer "busy"

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if request.method == "POST" and host in self.fail_post:
            raise httpx.ConnectError("agent down", request=request)
        if path.endswith("/result") and host in self.fail_result:
            raise httpx.ConnectError("agent down", request=request)
        if path.endswith("/result") and host in self.stuck:
            task_id = path.split("/")[-2]
            return httpx.Response(200, json={"task_id": task_id, "status": "running"})
        if path.endswith("/health") and self.busy_health.get(host, 0) > 0:
            self.busy_health[host] -= 1
            return httpx.Response(200, json={"status": "busy"})
        return await httpx.ASGITransport(app=self.apps[host]).handle_async_request(request)


class Ollama:
    """Fake Ollama for one agent: answers with the task title, or fails while `broken`."""

    def __init__(self, name: str) -> None:
        self.name, self.broken, self.prompts = name, False, []

    def transport(self) -> httpx.MockTransport:
        def respond(request: httpx.Request) -> httpx.Response:
            if self.broken:
                return httpx.Response(500)
            prompt = json.loads(request.content)["prompt"]
            self.prompts.append(prompt)
            title = prompt.split("Title: ")[1].split("\n")[0]
            return httpx.Response(200, json={"response": f"[{self.name}] done: {title}"})

        return httpx.MockTransport(respond)


class World:
    def __init__(self, strong_types: set[TaskType] | None = None) -> None:
        self.net = Network()
        self.ollama = {"strong": Ollama("strong"), "second": Ollama("second")}
        for name in self.ollama:
            types = strong_types if (name == "strong" and strong_types) else set(TaskType)
            self.net.apps[f"{name}.test"] = create_agent(
                AgentSettings(agent_id=f"agent-{name}", model="m", supported_types=types),
                ollama_transport=self.ollama[name].transport(),
            )

        def no_ollama(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)  # the master falls back to its built-in 5-task plan

        self.client = TestClient(
            create_master(
                transport=self.net,
                ollama_transport=httpx.MockTransport(no_ollama),
                agents_file=None,
                run_settings=FAST,
            )
        )
        self.client.__enter__()

    def register(self, name: str, caps: dict) -> None:
        self.client.post("/api/v1/agents", json={
            "agent_id": f"agent-{name}", "base_url": f"http://{name}.test", "model": "m", "capabilities": caps,
        })

    def plan(self) -> dict[str, dict]:
        r = self.client.post("/api/v1/requirements/decompose_and_map", json={"requirement_text": "Library"})
        assert r.status_code == 200
        return {t["label"]: t for t in r.json()}

    def run(self) -> dict:
        r = self.client.post("/api/v1/plan/run", params={"wait": "true"})
        assert r.status_code == 200, r.text
        return r.json()

    def tasks(self) -> dict[str, dict]:
        return {t["label"]: t for t in self.client.get("/api/v1/plan/tasks").json()}


@pytest.fixture
def world():
    w = World()
    w.register("strong", {"coding": 95, "reasoning": 95, "context": 95})
    w.register("second", {"coding": 85, "reasoning": 85, "context": 85})
    yield w
    w.client.__exit__(None, None, None)


def outcomes(task: dict) -> list[tuple[str | None, str]]:
    return [(a["agent_id"], a["outcome"]) for a in task["attempts"]]


# ---------------------------------------------------------------- normal flow
def test_whole_plan_runs_in_dependency_order(world):
    world.plan()
    report = world.run()
    assert report["finished"] and report["failed"] == [] and report["blocked"] == []
    assert sorted(report["completed"]) == ["T1", "T2", "T3", "T4", "T5"]

    order = [r["task"]["title"] for r in world.client.get("/api/v1/tasks").json()]
    tasks = world.tasks()
    pos = {label: order.index(t["title"]) for label, t in tasks.items()}
    assert pos["T1"] < pos["T2"] < pos["T3"] < pos["T4"]
    assert pos["T2"] < pos["T5"]


def test_outputs_are_stored_and_passed_to_dependent_tasks(world):
    world.plan()
    world.run()
    tasks = world.tasks()
    assert tasks["T1"]["output"] == f"[strong] done: {tasks['T1']['title']}"
    t2_prompt = next(p for p in world.ollama["strong"].prompts if f"Title: {tasks['T2']['title']}" in p)
    assert tasks["T1"]["output"] in t2_prompt  # T2's agent saw T1's result
    assert all(t["status"] == "completed" for t in tasks.values())
    assert outcomes(tasks["T1"]) == [("agent-strong", "completed")]


def test_run_without_plan_is_400(world):
    assert world.client.post("/api/v1/plan/run").status_code == 400


def test_run_in_background_and_read_status(world):
    world.plan()
    assert world.client.post("/api/v1/plan/run").status_code == 200  # no wait
    for _ in range(300):
        report = world.client.get("/api/v1/plan/run").json()
        if report["finished"]:
            break
        import time

        time.sleep(0.01)
    assert report["finished"] and len(report["completed"]) == 5


def test_running_again_after_success_changes_nothing(world):
    world.plan()
    world.run()
    again = world.run()
    assert again["failed"] == [] and len(again["completed"]) == 5
    assert len(world.client.get("/api/v1/tasks").json()) == 5  # nothing was sent twice


# ------------------------------------------------------------ recovery rules
def test_model_error_retries_on_another_agent(world):
    world.ollama["strong"].broken = True
    world.plan()
    report = world.run()
    assert report["failed"] == [] and len(report["completed"]) == 5
    t1 = world.tasks()["T1"]
    assert outcomes(t1) == [("agent-strong", "agent_failed"), ("agent-second", "completed")]
    assert t1["output"].startswith("[second]")


def test_unreachable_agent_retries_on_another_agent(world):
    world.net.fail_post.add("strong.test")
    world.plan()
    report = world.run()
    assert len(report["completed"]) == 5
    assert outcomes(world.tasks()["T1"]) == [("agent-strong", "unreachable"), ("agent-second", "completed")]


def test_rejecting_agent_retries_on_another_agent():
    w = World(strong_types={TaskType.TESTING})  # strong only accepts testing tasks
    w.register("strong", {"coding": 95, "reasoning": 95, "context": 95})
    w.register("second", {"coding": 85, "reasoning": 85, "context": 85})
    try:
        w.plan()
        report = w.run()
        t1 = w.tasks()["T1"]
        assert outcomes(t1) == [("agent-strong", "rejected"), ("agent-second", "completed")]
        assert "not supported" in t1["attempts"][0]["detail"]
        assert len(report["completed"]) == 5
    finally:
        w.client.__exit__(None, None, None)


def test_result_that_never_arrives_counts_as_timeout(world):
    world.net.stuck.add("strong.test")
    world.plan()
    world.run()
    assert outcomes(world.tasks()["T1"]) == [("agent-strong", "timeout"), ("agent-second", "completed")]


def test_agent_that_stops_answering_while_running_is_unreachable(world):
    world.net.fail_result.add("strong.test")
    world.plan()
    world.run()
    assert outcomes(world.tasks()["T1"]) == [("agent-strong", "unreachable"), ("agent-second", "completed")]


def test_busy_agents_are_waited_for_not_failed(world):
    world.net.busy_health = {"strong.test": 2, "second.test": 2}
    world.plan()
    report = world.run()
    assert len(report["completed"]) == 5
    assert outcomes(world.tasks()["T1"]) == [("agent-strong", "completed")]  # waiting is not an attempt


def test_agents_that_stay_busy_make_the_task_fail_with_busy(world):
    world.net.busy_health = {"strong.test": 10_000, "second.test": 10_000}
    world.plan()
    report = world.run()
    assert report["failed"] == ["T1"]
    assert outcomes(world.tasks()["T1"]) == [(None, "busy")]


def test_all_agents_failing_fails_the_task_and_blocks_the_rest(world):
    world.ollama["strong"].broken = world.ollama["second"].broken = True
    world.plan()
    report = world.run()
    assert report["failed"] == ["T1"]
    assert sorted(report["blocked"]) == ["T2", "T3", "T4", "T5"]
    t1 = world.tasks()["T1"]
    assert [o for _, o in outcomes(t1)] == ["agent_failed", "agent_failed", "no_agent"]
    assert "no other agent left" in t1["attempts"][-1]["detail"]
    assert world.tasks()["T2"]["status"] == "blocked"  # was never sent


def test_no_agent_meets_the_requirements_fails_without_sending():
    w = World()
    w.register("strong", {"coding": 50, "reasoning": 50, "context": 50})  # below every threshold
    try:
        w.plan()
        report = w.run()
        assert report["failed"] == ["T1"]
        assert outcomes(w.tasks()["T1"]) == [(None, "no_agent")]
        assert w.client.get("/api/v1/tasks").json() == []
    finally:
        w.client.__exit__(None, None, None)


def test_max_attempts_limits_the_retries():
    w = World()
    extra = {}
    for name, score in (("strong", 95), ("second", 90), ("third", 85), ("fourth", 82)):
        if name not in w.ollama:
            extra[name] = Ollama(name)
            w.net.apps[f"{name}.test"] = create_agent(
                AgentSettings(agent_id=f"agent-{name}", model="m"), ollama_transport=extra[name].transport()
            )
        w.register(name, {"coding": score, "reasoning": score, "context": score})
    for o in (*w.ollama.values(), *extra.values()):
        o.broken = True
    try:
        w.plan()
        report = w.run()
        t1 = w.tasks()["T1"]
        # 3 attempts (FAST.max_attempts), best agents first; the fourth agent is never asked
        assert outcomes(t1) == [(f"agent-{n}", "agent_failed") for n in ("strong", "second", "third")]
        assert report["failed"] == ["T1"]
    finally:
        w.client.__exit__(None, None, None)


def test_failed_plan_can_be_run_again(world):
    world.ollama["strong"].broken = world.ollama["second"].broken = True
    world.plan()
    assert world.run()["failed"] == ["T1"]
    world.ollama["strong"].broken = world.ollama["second"].broken = False  # the models are back
    report = world.run()
    assert report["failed"] == [] and len(report["completed"]) == 5


# ------------------------------------------------------- dependency manager
def _manager_with_chain() -> tuple[DependencyManager, dict]:
    from master.decompose import DecomposeResponse, SubTask

    def sub(label, deps):
        return SubTask(task_id=label, title=label, category=TaskType.BACKEND, description=label, dependencies=deps)

    dep = DependencyManager()
    dep.register_decomposition(DecomposeResponse(project_title="p", summary="s", tasks=[sub("T1", []), sub("T2", ["T1"])]))
    return dep, dep.label_map


def test_reset_failed_restores_pending_and_blocked():
    dep, ids = _manager_with_chain()
    dep.mark_failed(ids["T1"])
    dep.mark_failed(ids["T2"])
    assert dep.reset_failed() == 2
    assert dep.tasks[ids["T1"]].status == TaskLifecycleStatus.PENDING
    assert dep.tasks[ids["T2"]].status == TaskLifecycleStatus.BLOCKED


def test_mark_retry_only_touches_dispatched_tasks():
    dep, ids = _manager_with_chain()
    dep.mark_dispatched(ids["T1"])
    dep.mark_retry(ids["T1"])
    assert dep.tasks[ids["T1"]].status == TaskLifecycleStatus.PENDING
    dep.mark_completed(ids["T1"])
    dep.mark_retry(ids["T1"])
    assert dep.tasks[ids["T1"]].status == TaskLifecycleStatus.COMPLETED


# ------------------------------------------------------ agent: resending a task
def _agent_client(ollama: Ollama) -> TestClient:
    return TestClient(create_agent(AgentSettings(agent_id="agent-x", model="m"), ollama_transport=ollama.transport()))


MESSAGE = {
    "task_id": "11111111-1111-1111-1111-111111111111",
    "agent_id": "agent-x",
    "type": "backend",
    "title": "Books API",
    "description": "d",
}


def test_agent_runs_a_failed_task_again_when_it_is_resent():
    ollama = Ollama("x")
    ollama.broken = True
    agent = _agent_client(ollama)
    assert agent.post("/agent/v1/tasks", json=MESSAGE).json()["status"] == "accepted"
    assert agent.get(f"/agent/v1/tasks/{MESSAGE['task_id']}/result").json()["status"] == "failed"

    ollama.broken = False
    second = agent.post("/agent/v1/tasks", json=MESSAGE).json()
    assert second["status"] == "accepted" and second["duplicate"] is False
    assert agent.get(f"/agent/v1/tasks/{MESSAGE['task_id']}/result").json()["status"] == "completed"


def test_agent_does_not_run_a_completed_task_twice():
    ollama = Ollama("x")
    agent = _agent_client(ollama)
    agent.post("/agent/v1/tasks", json=MESSAGE)
    again = agent.post("/agent/v1/tasks", json=MESSAGE).json()
    assert again["duplicate"] is True
    assert len(ollama.prompts) == 1
