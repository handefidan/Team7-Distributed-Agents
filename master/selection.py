"""Agent selection: which registered agent a task should go to.

Sprint 2 Week 4 — the dispatch contract (docs/api/task-dispatch-contract.md)
intentionally starts once an agent is already picked; this is what picks it.
"""

from shared.schemas import AgentRegistration, CapabilityScores, TaskSpec

CAPABILITY_FIELDS = tuple(CapabilityScores.model_fields)


def _meets_requirements(capabilities: CapabilityScores, required: CapabilityScores) -> bool:
    return all(
        getattr(capabilities, field) >= getattr(required, field) for field in CAPABILITY_FIELDS
    )


def _score(capabilities: CapabilityScores) -> int:
    return sum(getattr(capabilities, field) for field in CAPABILITY_FIELDS)


def select_agent(agents: list[AgentRegistration], task: TaskSpec) -> AgentRegistration | None:
    """Pick the registered agent best suited for `task`.

    An agent qualifies only if every one of its capability scores meets or
    exceeds `task.required_capabilities`. Among qualifying agents, the one
    with the highest total score wins; ties break on `agent_id` so the pick
    is deterministic. Returns None if no registered agent qualifies.
    """
    candidates = [a for a in agents if _meets_requirements(a.capabilities, task.required_capabilities)]
    if not candidates:
        return None
    return max(candidates, key=lambda a: (_score(a.capabilities), a.agent_id))
