"""Agent selection: which registered agent a task should go to.

The dispatch contract (docs/api/task-dispatch-contract.md) starts once an
agent is picked; this is what picks it. An agent is chosen only if it meets
the task's required capabilities AND its /agent/v1/health reports it idle.
"""

import asyncio
from enum import StrEnum

import httpx

from shared.schemas import AgentRegistration, CapabilityScores, TaskSpec

CAPABILITY_FIELDS = tuple(CapabilityScores.model_fields)


class AgentStatus(StrEnum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"


def _meets_requirements(capabilities: CapabilityScores, required: CapabilityScores) -> bool:
    return all(
        getattr(capabilities, field) >= getattr(required, field) for field in CAPABILITY_FIELDS
    )


def _score(agent: AgentRegistration) -> tuple[int, str]:
    # agent_id breaks ties so the pick is deterministic.
    return sum(getattr(agent.capabilities, field) for field in CAPABILITY_FIELDS), agent.agent_id


def qualifying_agents(agents: list[AgentRegistration], task: TaskSpec) -> list[AgentRegistration]:
    """Agents whose every capability score meets the task's requirement, best first."""
    matches = [a for a in agents if _meets_requirements(a.capabilities, task.required_capabilities)]
    return sorted(matches, key=_score, reverse=True)


async def fetch_status(client: httpx.AsyncClient, agent: AgentRegistration, timeout: float) -> AgentStatus:
    if agent.base_url is None:
        return AgentStatus.OFFLINE
    try:
        response = await client.get(f"{str(agent.base_url).rstrip('/')}/agent/v1/health", timeout=timeout)
        response.raise_for_status()
        reported = response.json().get("status")
    except (httpx.HTTPError, ValueError):
        return AgentStatus.OFFLINE
    return AgentStatus.IDLE if reported == AgentStatus.IDLE else AgentStatus.BUSY


async def fetch_statuses(
    client: httpx.AsyncClient, agents: list[AgentRegistration], timeout: float
) -> dict[str, AgentStatus]:
    statuses = await asyncio.gather(*(fetch_status(client, a, timeout) for a in agents))
    return {a.agent_id: s for a, s in zip(agents, statuses)}
