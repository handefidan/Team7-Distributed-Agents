"""Message formats shared by the master and the agents (API contract v1).

See docs/api/task-dispatch-contract.md for the human-readable contract.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, HttpUrl

CONTRACT_VERSION = "1.0"


def utcnow() -> datetime:
    return datetime.now(UTC)


class TaskType(StrEnum):
    REQUIREMENTS = "requirements"
    FRONTEND = "frontend"
    BACKEND = "backend"
    DATABASE = "database"
    TESTING = "testing"
    INTEGRATION = "integration"
    DOCUMENTATION = "documentation"


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskSpec(BaseModel):
    """What a caller asks the master to send to an agent."""

    task_id: UUID = Field(
        default_factory=uuid4,
        description="Optional. Reuse the same id to retry a send safely (idempotent).",
    )
    type: TaskType
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    priority: TaskPriority = TaskPriority.MEDIUM
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form input for the agent, e.g. requirement excerpt or file paths.",
    )
    depends_on: list[UUID] = Field(default_factory=list)
    deadline: datetime | None = None


class TaskMessage(TaskSpec):
    """The message the master POSTs to an agent."""

    contract_version: str = CONTRACT_VERSION
    agent_id: str
    sent_at: datetime = Field(default_factory=utcnow)


class AckStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class TaskAcknowledgement(BaseModel):
    """What an agent answers immediately after receiving a task."""

    contract_version: str = CONTRACT_VERSION
    task_id: UUID
    agent_id: str
    status: AckStatus
    reason: str | None = Field(default=None, description="Set when status is 'rejected'.")
    queue_position: int | None = Field(default=None, description="1-based, set when accepted.")
    duplicate: bool = Field(default=False, description="True if this task_id was already received.")
    received_at: datetime = Field(default_factory=utcnow)


class AgentRegistration(BaseModel):
    agent_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    base_url: HttpUrl
    model: str = Field(min_length=1)


class DispatchStatus(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    FAILED = "failed"


class DispatchRecord(BaseModel):
    """The master's record of one send attempt."""

    task: TaskMessage
    status: DispatchStatus
    acknowledgement: TaskAcknowledgement | None = None
    error: str | None = None
    round_trip_ms: float | None = None
