import pytest

from master.classify import MAIN_THRESHOLD, SECOND_THRESHOLD, TYPE_CAPABILITIES, required_capabilities
from master.decompose import SubTask, mock_decomposition
from master.selection import select_agent
from shared.schemas import AgentRegistration, TaskType


def agent(agent_id: str, coding: int, reasoning: int, context: int) -> AgentRegistration:
    return AgentRegistration(
        agent_id=agent_id,
        base_url=f"http://{agent_id}.test",
        model="llama3",
        capabilities={"coding": coding, "reasoning": reasoning, "context": context},
    )


def test_every_task_type_is_classified():
    assert set(TYPE_CAPABILITIES) == set(TaskType)


@pytest.mark.parametrize("task_type", list(TaskType))
def test_main_and_second_capability_get_their_thresholds(task_type):
    main, second = TYPE_CAPABILITIES[task_type]
    required = required_capabilities(task_type).model_dump()
    assert main != second
    assert required[main] == MAIN_THRESHOLD
    assert required[second] == SECOND_THRESHOLD
    (third,) = set(required) - {main, second}
    assert required[third] == 0


def test_backend_needs_coding_most():
    required = required_capabilities(TaskType.BACKEND)
    assert required.coding == MAIN_THRESHOLD
    assert required.reasoning == SECOND_THRESHOLD
    assert required.context == 0


def test_documentation_needs_context_most():
    required = required_capabilities(TaskType.DOCUMENTATION)
    assert required.context == MAIN_THRESHOLD
    assert required.reasoning == SECOND_THRESHOLD
    assert required.coding == 0


def test_same_type_always_gives_same_requirement():
    assert required_capabilities(TaskType.TESTING) == required_capabilities(TaskType.TESTING)


def test_every_decomposed_subtask_is_labelled_with_its_requirement():
    for task in mock_decomposition("any requirement").tasks:
        assert task.required_capabilities == required_capabilities(task.category)


def test_model_supplied_capabilities_are_replaced():
    task = SubTask(
        task_id="T1",
        title="API",
        category=TaskType.BACKEND,
        description="Build the API.",
        required_capabilities={"coding": 5, "reasoning": 5, "context": 5},
    )
    assert task.required_capabilities == required_capabilities(TaskType.BACKEND)


def test_subtask_converts_to_a_dispatchable_task_spec():
    task = SubTask(task_id="T1", title="Schema", category=TaskType.DATABASE, description="Design tables.")
    spec = task.to_task_spec()
    assert spec.type == TaskType.DATABASE
    assert spec.required_capabilities == required_capabilities(TaskType.DATABASE)


def test_classified_task_goes_to_the_agent_that_fits_its_type():
    coder = agent("coder", coding=95, reasoning=70, context=60)
    thinker = agent("thinker", coding=60, reasoning=95, context=70)
    backend = SubTask(task_id="T1", title="API", category=TaskType.BACKEND, description="x").to_task_spec()
    database = SubTask(task_id="T2", title="Schema", category=TaskType.DATABASE, description="x").to_task_spec()
    assert select_agent([coder, thinker], backend).agent_id == "coder"
    assert select_agent([coder, thinker], database).agent_id == "thinker"


def test_no_agent_selected_when_nobody_is_strong_enough():
    weak = agent("weak", coding=40, reasoning=40, context=40)
    spec = SubTask(task_id="T1", title="API", category=TaskType.BACKEND, description="x").to_task_spec()
    assert select_agent([weak], spec) is None
