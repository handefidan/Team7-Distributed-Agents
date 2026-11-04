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