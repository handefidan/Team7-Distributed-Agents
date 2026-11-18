"""Runs a decomposed plan end to end (Sprint 4, Week 7).

The loop the master follows for every task of a plan:

    ready task -> pick an agent and send it -> wait for the agent's result
               -> completed: store the output, release the tasks waiting on it
               -> problem:   apply the recovery rules (docs/error-recovery.md)

Everything that touches the network is passed in as a function (`send`,
`fetch_result`), so this module has no HTTP code and is easy to test.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel

from master.dependency import DependencyManager, TaskLifecycleStatus
from shared.schemas import AgentRegistration, TaskSpec


@dataclass
class RunSettings:
    max_attempts: int = 3  # tries per task, each on a different agent
    busy_wait_seconds: float = 2.0  # pause when every suitable agent is busy
    max_busy_wait_seconds: float = 120.0  # give up waiting for a free agent after this
    poll_interval_seconds: float = 1.0  # how often the agent's result is asked for
    result_timeout_seconds: float = 300.0  # an attempt without a result after this counts as failed
    max_result_errors: int = 3  # consecutive unreachable result polls before the attempt fails


@dataclass
class DispatchResult:
    """What happened when the master tried to send a task."""

    kind: str  # accepted | rejected | unreachable | no_agent | all_busy
    agent: AgentRegistration | None = None
    detail: str | None = None


SendFn = Callable[[TaskSpec, set[str]], Awaitable[DispatchResult]]
# Returns the agent's result as a dict (status/output/error), or None if the agent could not be reached.
FetchResultFn = Callable[[AgentRegistration, UUID], Awaitable[dict | None]]


class RunReport(BaseModel):
    finished: bool = False
    completed: list[str] = []
    failed: list[str] = []
    blocked: list[str] = []  # never started because a task they depend on failed


async def _wait_for_result(
    agent: AgentRegistration, task_id: UUID, fetch_result: FetchResultFn, settings: RunSettings
) -> tuple[str, str | None]:
    """Returns (outcome, text): ("completed", output) | ("agent_failed", error) |
    ("timeout", ...) | ("unreachable", ...)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.result_timeout_seconds
    errors = 0
    while loop.time() < deadline:
        result = await fetch_result(agent, task_id)
        if result is None:
            errors += 1
            if errors >= settings.max_result_errors:
                return "unreachable", "agent stopped answering while the task was running"
        else:
            errors = 0
            state = result.get("status")
            if state == "completed":
                return "completed", result.get("output") or ""
            if state == "failed":
                return "agent_failed", result.get("error") or "agent reported a failure"
        await asyncio.sleep(settings.poll_interval_seconds)
    return "timeout", f"no result after {settings.result_timeout_seconds:g} seconds"


async def run_task(
    dep: DependencyManager,
    task_uuid: UUID,
    send: SendFn,
    fetch_result: FetchResultFn,
    settings: RunSettings,
) -> None:
    """Run one task to completion, or mark it failed after the recovery rules are used up."""
    tried: set[str] = set()  # agents that already failed this task are not asked again
    attempts = 0
    busy_waited = 0.0

    def give_up(reason_outcome: str, agent_id: str | None, detail: str | None) -> None:
        dep.record_attempt(task_uuid, agent_id, reason_outcome, detail)
        dep.mark_failed(task_uuid)

    while True:
        sent = await send(dep.to_task_spec(task_uuid), tried)

        if sent.kind == "no_agent":  # waiting or retrying cannot help
            return give_up("no_agent", None, sent.detail)

        if sent.kind == "all_busy":  # not a failed attempt: wait for an agent to become free
            if busy_waited >= settings.max_busy_wait_seconds:
                return give_up("busy", None, sent.detail)
            await asyncio.sleep(settings.busy_wait_seconds)
            busy_waited += settings.busy_wait_seconds
            continue

        agent_id = sent.agent.agent_id if sent.agent else None
        attempts += 1

        if sent.kind == "accepted":
            outcome, text = await _wait_for_result(sent.agent, task_uuid, fetch_result, settings)
            if outcome == "completed":
                dep.record_attempt(task_uuid, agent_id, "completed")
                dep.mark_completed(task_uuid, text)
                return
        else:  # rejected | unreachable
            outcome, text = sent.kind, sent.detail

        dep.record_attempt(task_uuid, agent_id, outcome, text)
        if agent_id:
            tried.add(agent_id)
        if attempts >= settings.max_attempts:
            return dep.mark_failed(task_uuid)
        dep.mark_retry(task_uuid)  # back to pending, the next loop picks another agent


async def run_plan(
    dep: DependencyManager,
    send: SendFn,
    fetch_result: FetchResultFn,
    settings: RunSettings | None = None,
) -> RunReport:
    """Run every task of the plan in dependency order. Independent tasks run at the same time."""
    settings = settings or RunSettings()
    dep.reset_failed()
    running: dict[asyncio.Task, UUID] = {}
    started: set[UUID] = set()

    while True:
        for task in dep.get_ready_tasks():
            if task.task_uuid not in started:
                started.add(task.task_uuid)
                running[asyncio.create_task(run_task(dep, task.task_uuid, send, fetch_result, settings))] = task.task_uuid
        if not running:
            break
        done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
        for finished in done:
            task_uuid = running.pop(finished)
            if finished.exception():  # a bug must not leave the task hanging as "dispatched"
                dep.record_attempt(task_uuid, None, "error", repr(finished.exception()))
                dep.mark_failed(task_uuid)

    report = RunReport(finished=True)
    for task in dep.tasks.values():
        if task.status == TaskLifecycleStatus.COMPLETED:
            report.completed.append(task.label)
        elif task.status == TaskLifecycleStatus.FAILED:
            report.failed.append(task.label)
        else:
            report.blocked.append(task.label)
    return report
