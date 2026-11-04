"""Master Agent Dependency Resolution & Task Lifecycle Manager (Sprint 3)."""

from uuid import UUID, uuid4
from enum import Enum
from pydantic import BaseModel, Field

from master.decompose import DecomposeResponse, SubTask
from shared.schemas import CapabilityScores, TaskSpec, TaskType


class CyclicDependencyError(ValueError):
    """The decomposition contains a dependency cycle (e.g. T1 -> T2 -> T1)."""


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
    required_capabilities: CapabilityScores = Field(default_factory=CapabilityScores)
    status: TaskLifecycleStatus = TaskLifecycleStatus.PENDING
    output: str | None = None  # result text, set when the task is completed


class DependencyManager:
    def __init__(self) -> None:
        self.tasks: dict[UUID, MappedTask] = {}
        self.label_map: dict[str, UUID] = {}

    @staticmethod
    def find_cycle(decomp: DecomposeResponse) -> list[str] | None:
        """Return the labels of one dependency cycle (e.g. ["T1", "T2", "T1"]), or None."""
        graph = {t.task_id: list(t.dependencies) for t in decomp.tasks}
        visiting: list[str] = []
        done: set[str] = set()

        def visit(label: str) -> list[str] | None:
            if label in visiting:
                return visiting[visiting.index(label):] + [label]
            if label in done:
                return None
            visiting.append(label)
            for dep in graph.get(label, []):
                if dep in graph:  # unknown labels are ignored (see register_decomposition)
                    found = visit(dep)
                    if found:
                        return found
            visiting.pop()
            done.add(label)
            return None

        for label in graph:
            found = visit(label)
            if found:
                return found
        return None

    def register_decomposition(self, decomp: DecomposeResponse) -> list[MappedTask]:
        cycle = self.find_cycle(decomp)
        if cycle:
            raise CyclicDependencyError("dependency cycle: " + " -> ".join(cycle))
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
                required_capabilities=subtask.required_capabilities,
                status=initial_status,
            )
            self.tasks[task_uuid] = mapped
            mapped_list.append(mapped)

        return mapped_list

    def is_ready_for_dispatch(self, task_uuid: UUID) -> bool:
        task = self.tasks.get(task_uuid)
        if not task:
            return False

        if task.status in (
            TaskLifecycleStatus.COMPLETED,
            TaskLifecycleStatus.DISPATCHED,
            TaskLifecycleStatus.FAILED,
        ):
            return False

        for dep_uuid in task.dependency_uuids:
            dep_task = self.tasks.get(dep_uuid)
            if not dep_task or dep_task.status != TaskLifecycleStatus.COMPLETED:
                return False

        return True

    def mark_dispatched(self, task_uuid: UUID) -> None:
        task = self.tasks.get(task_uuid)
        if task and task.status != TaskLifecycleStatus.COMPLETED:
            task.status = TaskLifecycleStatus.DISPATCHED

    def mark_failed(self, task_uuid: UUID) -> None:
        """A failed task never unblocks the tasks that depend on it."""
        task = self.tasks.get(task_uuid)
        if task:
            task.status = TaskLifecycleStatus.FAILED

    def to_task_spec(self, task_uuid: UUID) -> TaskSpec:
        """Build the TaskSpec for a mapped task: real UUIDs in `depends_on`, and the
        output of every finished prerequisite passed to the agent in `context`."""
        task = self.tasks[task_uuid]
        outputs = {
            self.tasks[d].label: self.tasks[d].output
            for d in task.dependency_uuids
            if d in self.tasks and self.tasks[d].output
        }
        return TaskSpec(
            task_id=task.task_uuid,
            type=task.category,
            title=task.title,
            description=task.description,
            depends_on=task.dependency_uuids,
            required_capabilities=task.required_capabilities,
            context={"dependency_outputs": outputs} if outputs else {},
        )

    def mark_completed(self, task_uuid: UUID, output: str | None = None) -> None:
        if task_uuid in self.tasks:
            self.tasks[task_uuid].status = TaskLifecycleStatus.COMPLETED
            if output is not None:
                self.tasks[task_uuid].output = output

        for task in self.tasks.values():
            if task.status == TaskLifecycleStatus.BLOCKED and self.is_ready_for_dispatch(task.task_uuid):
                task.status = TaskLifecycleStatus.PENDING

    def get_ready_tasks(self) -> list[MappedTask]:
        return [
            task
            for task_uuid, task in self.tasks.items()
            if self.is_ready_for_dispatch(task_uuid)
        ]