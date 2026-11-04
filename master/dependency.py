"""Master Agent Dependency Resolution & Task Lifecycle Manager (Sprint 3)."""

from uuid import UUID, uuid4
from enum import Enum
from pydantic import BaseModel, Field

from master.decompose import DecomposeResponse, SubTask
from shared.schemas import TaskSpec, TaskType


class TaskLifecycleStatus(str, Enum):
    PENDING = "pending"
    BLOCKED = "blocked"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"


class MappedTask(BaseModel):
    task_uuid: UUID = Field(default_factory=uuid4)
    label: str  # e.g., "T1"
    title: str
    category: TaskType
    description: str
    dependency_uuids: list[UUID] = Field(default_factory=list)
    status: TaskLifecycleStatus = TaskLifecycleStatus.PENDING


class DependencyManager:
    def __init__(self) -> None:
        self.tasks: dict[UUID, MappedTask] = {}
        self.label_map: dict[str, UUID] = {}

    def register_decomposition(self, decomp: DecomposeResponse) -> list[MappedTask]:
        mapped_list: list[MappedTask] = []

        for subtask in decomp.tasks:
            task_uuid = uuid4()
            self.label_map[subtask.task_id] = task_uuid

        for subtask in decomp.tasks:
            task_uuid = self.label_map[subtask.task_id]
            dep_uuids = [
                self.label_map[dep_label]
                for dep_label in subtask.dependencies
                if dep_label in self.label_map
            ]
            
            initial_status = (
                TaskLifecycleStatus.BLOCKED
                if len(dep_uuids) > 0
                else TaskLifecycleStatus.PENDING
            )

            mapped = MappedTask(
                task_uuid=task_uuid,
                label=subtask.task_id,
                title=subtask.title,
                category=subtask.category,
                description=subtask.description,
                dependency_uuids=dep_uuids,
                status=initial_status,
            )
            self.tasks[task_uuid] = mapped
            mapped_list.append(mapped)

        return mapped_list

    def is_ready_for_dispatch(self, task_uuid: UUID) -> bool:
        task = self.tasks.get(task_uuid)
        if not task:
            return False

        if task.status in (TaskLifecycleStatus.COMPLETED, TaskLifecycleStatus.DISPATCHED):
            return False

        for dep_uuid in task.dependency_uuids:
            dep_task = self.tasks.get(dep_uuid)
            if not dep_task or dep_task.status != TaskLifecycleStatus.COMPLETED:
                return False

        return True

    def mark_completed(self, task_uuid: UUID) -> None:
        if task_uuid in self.tasks:
            self.tasks[task_uuid].status = TaskLifecycleStatus.COMPLETED

        for task in self.tasks.values():
            if task.status == TaskLifecycleStatus.BLOCKED and self.is_ready_for_dispatch(task.task_uuid):
                task.status = TaskLifecycleStatus.PENDING

    def get_ready_tasks(self) -> list[MappedTask]:
        return [
            task
            for task_uuid, task in self.tasks.items()
            if self.is_ready_for_dispatch(task_uuid)
        ]