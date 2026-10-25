"""Task classification: which capabilities a task of a given type needs.

Sprint 2 Week 4. The table below is the one in docs/task-capability-mapping.md.
It is fixed (not guessed by the local model), so the same task type always
gives the same requirement and the selection algorithm can be tested.

An agent is only selected if it meets *every* required score
(see master/selection.py), so the capability that is neither the main nor
the second one is required at 0.
"""

from shared.schemas import CapabilityScores, TaskType

MAIN_THRESHOLD = 80
SECOND_THRESHOLD = 60

# task type -> (main capability, second capability)
TYPE_CAPABILITIES: dict[TaskType, tuple[str, str]] = {
    TaskType.REQUIREMENTS: ("reasoning", "context"),
    TaskType.DATABASE: ("reasoning", "coding"),
    TaskType.BACKEND: ("coding", "reasoning"),
    TaskType.FRONTEND: ("coding", "context"),
    TaskType.TESTING: ("reasoning", "coding"),
    TaskType.INTEGRATION: ("context", "reasoning"),
    TaskType.DOCUMENTATION: ("context", "reasoning"),
}


def required_capabilities(task_type: TaskType) -> CapabilityScores:
    """Minimum scores an agent needs to take a task of this type."""
    main, second = TYPE_CAPABILITIES[task_type]
    scores = {"coding": 0, "reasoning": 0, "context": 0}
    scores[main] = MAIN_THRESHOLD
    scores[second] = SECOND_THRESHOLD
    return CapabilityScores(**scores)
