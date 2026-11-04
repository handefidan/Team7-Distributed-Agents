# Task Dispatch API Contract (v1.0)

Sprint 2 · Owner: Berat Ermiş
Reviewed and approved for Sprint 2 Week 3-4: still accurate as written below.
Week 4 adds `POST /api/v1/tasks/dispatch`, where the master itself picks the
agent (see "Agent selection" below) — the original
`POST /api/v1/agents/{agent_id}/tasks` is unchanged and still available for
sending to an explicitly chosen agent.

Sprint 3 adds dependency management and the result flow (see "Sprint 3 endpoints" below
and [message-flow.md](message-flow.md)). The send/acknowledge contract itself is unchanged.

How the master agent sends a task to a chosen worker agent and how the agent
acknowledges it. This contract starts once an agent — chosen by the caller or
by the master's own selection algorithm — has been picked.

The Pydantic models in [`shared/schemas.py`](../../shared/schemas.py) are the
source of truth. Live OpenAPI docs: `http://<master>:8000/docs` and
`http://<agent>:8001/docs`.

## Sequence

```
Caller (UI / planner)          Master (:8000)                         Agent (:8001)
        |  POST /api/v1/agents/{id}/tasks |                                    |
        |-------------------------------->|  POST /agent/v1/tasks (TaskMessage) |
        |                                 |----------------------------------->|
        |                                 |   200 TaskAcknowledgement          |
        |                                 |<-----------------------------------|
        |       200 DispatchRecord        |  (stores record, measures latency) |
        |<--------------------------------|                                    |
```

The acknowledgement only confirms the agent **received** the task (accepted
into its queue or rejected). Execution results are a separate flow, see [message-flow.md](message-flow.md).

## Agent endpoint (implemented by every agent)

### `POST /agent/v1/tasks`

Request body — `TaskMessage`:

| Field | Type | Required | Notes |
|---|---|---|---|
| `task_id` | UUID | yes | Unique id; also the idempotency key |
| `agent_id` | string | yes | Id of the agent the task is addressed to |
| `type` | enum | yes | `requirements`, `frontend`, `backend`, `database`, `testing`, `integration`, `documentation` |
| `title` | string (1–200) | yes | |
| `description` | string | yes | |
| `priority` | enum | no | `low`, `medium` (default), `high` |
| `context` | object | no | Free-form input (requirement excerpt, file paths, …) |
| `depends_on` | UUID[] | no | Tasks that must finish first |
| `deadline` | ISO-8601 datetime | no | |
| `contract_version` | string | no | `"1.0"` |
| `sent_at` | ISO-8601 datetime | no | Set by the master |

Response `200` — `TaskAcknowledgement`:

| Field | Type | Notes |
|---|---|---|
| `task_id` | UUID | Must equal the received `task_id` |
| `agent_id` | string | The responding agent |
| `status` | `accepted` \| `rejected` | |
| `reason` | string \| null | Why it was rejected |
| `queue_position` | int \| null | 1-based position when accepted |
| `duplicate` | bool | `true` if this `task_id` was already received; the original ack is returned |
| `received_at` | ISO-8601 datetime | |
| `contract_version` | string | `"1.0"` |

An agent rejects a task when it is addressed to another agent, the task type is
not supported, or its queue is full. Malformed bodies get `422`.

### `GET /agent/v1/health`

Returns `agent_id`, `model`, `status` (`idle`/`busy`), `queue_size`,
`max_queue`, `supported_types`.

## Master endpoints

| Method & path | Purpose |
|---|---|
| `POST /api/v1/agents` | Register an agent (`agent_id`, `base_url`, `model`, `capabilities`) — temporary in-memory registry |
| `GET /api/v1/agents` | List registered agents |
| `POST /api/v1/agents/{agent_id}/tasks` | Send a task to the **named** agent and wait for its acknowledgement |
| `POST /api/v1/tasks/dispatch` | Send a task to the agent **the selection algorithm picks** and wait for its acknowledgement |
| `POST /api/v1/requirements/decompose` | Split a requirement into typed subtasks (labels `T1`, `T2`, …) |
| `POST /api/v1/requirements/decompose_and_map` | Same, plus label → UUID mapping and dependency graph (Sprint 3) |
| `GET /api/v1/plan/ready` | Mapped tasks whose dependencies are all completed (Sprint 3) |
| `GET /api/v1/plan/tasks/{task_id}/spec` | Ready-to-send `TaskSpec` of a mapped task (Sprint 3) |
| `POST /api/v1/tasks/{task_id}/complete` | Mark a task completed, optional `{"output": "..."}`; releases dependents (Sprint 3) |
| `POST /api/v1/tasks/{task_id}/fail` | Mark a task failed; dependents stay blocked (Sprint 3) |
| `GET /api/v1/tasks` | List all dispatch records |
| `GET /api/v1/tasks/{task_id}` | Get one dispatch record |

### Agent capability scores

Registration (`POST /api/v1/agents`) now takes an optional `capabilities`
object — `CapabilityScores` in `shared/schemas.py`:

| Field | Type | Default | Notes |
|---|---|---|---|
| `coding` | int (0–100) | 50 | |
| `reasoning` | int (0–100) | 50 | |
| `context` | int (0–100) | 50 | |

Omitting `capabilities` registers an "average" agent (50/50/50), so existing
callers that don't set it keep working.

### Agent selection

`TaskSpec` (used by both send endpoints) takes an optional
`required_capabilities` object, same shape as `CapabilityScores`, defaulting
to 50/50/50. `POST /api/v1/tasks/dispatch` (`master/selection.py`):

1. Keeps only registered agents whose `capabilities` meet or exceed every
   field of the task's `required_capabilities`.
2. Among those, picks the one with the highest total score (`coding +
   reasoning + context`); ties break on `agent_id` for a deterministic pick.
3. Returns `404` if no registered agent qualifies.

Once an agent is picked, dispatch proceeds exactly as `POST
/api/v1/agents/{agent_id}/tasks` below.

### `POST /api/v1/agents/{agent_id}/tasks`

Request body — `TaskSpec`: the `TaskMessage` fields above except `agent_id`,
`sent_at`, `contract_version`. `task_id` is optional; the master generates one
if omitted. Send the same `task_id` again to retry safely.

Response — `DispatchRecord`:

```json
{
  "task": { "task_id": "579b…", "agent_id": "agent-a", "type": "backend", "...": "..." },
  "status": "acknowledged",
  "acknowledgement": { "task_id": "579b…", "status": "accepted", "queue_position": 1, "...": "..." },
  "error": null,
  "round_trip_ms": 3.47
}
```

| HTTP | `status` | When |
|---|---|---|
| 200 | `acknowledged` | Agent accepted the task |
| 200 | `rejected` | Agent answered but rejected it (see `acknowledgement.reason`) |
| 404 | — | `agent_id` not registered |
| 422 | — | Invalid task body |
| 502 | `failed` | Agent returned an error or an invalid/mismatched acknowledgement |
| 503 | `failed` | Agent unreachable |
| 504 | `failed` | Agent did not answer within `AGENT_TIMEOUT_SECONDS` (default 5) |

For 502/503/504 the response body is
`{"detail": {"task_id": "...", "error": "..."}}` and the failed attempt is
stored, so it can be inspected via `GET /api/v1/tasks/{task_id}` and later
reassigned (Sprint 4).

## Sprint 3 endpoints

### Dependencies on dispatch

`POST /api/v1/agents/{agent_id}/tasks` and `POST /api/v1/tasks/dispatch` return
`400` when the task belongs to a decomposed plan and one of its dependencies is not `completed`:

```json
{"detail": "Task '<uuid>' cannot be dispatched yet because its prerequisite dependencies are not completed."}
```

Tasks that are not part of a plan (sent directly with a hand-written `TaskSpec`) are not checked.
After the agent acknowledges a plan task, the task becomes `dispatched` and no longer appears in `/plan/ready`.

### `POST /api/v1/requirements/decompose_and_map`

Body `{"requirement_text": "..."}` → `200`, list of `MappedTask`:

| Field | Notes |
|---|---|
| `task_uuid` | Runtime id; use it as `task_id` |
| `label` | `T1`, `T2`, … from the decomposition |
| `title`, `category`, `description` | From the decomposition (`category` = task type) |
| `dependency_uuids` | UUIDs of the tasks that must finish first |
| `required_capabilities` | Fixed by task type (`master/classify.py`) |
| `status` | `pending`, `blocked`, `dispatched`, `completed`, `failed` |
| `output` | Result text after `complete` |

`422` with `"dependency cycle: T1 -> T2 -> T1"` if the decomposition has a cycle; `400` for an empty requirement.

### `GET /api/v1/plan/tasks/{task_id}/spec`

Returns a `TaskSpec` with `task_id` = the plan UUID, `depends_on`, `required_capabilities` and
`context.dependency_outputs` (`{"T1": "<output>"}`, only for finished prerequisites with an output).
`404` if the id is not part of a plan.

### `POST /api/v1/tasks/{task_id}/complete` · `POST /api/v1/tasks/{task_id}/fail`

`complete` takes an optional body `{"output": "<text>"}`; both return `{"task_id": "...", "status": "completed" | "failed"}`.

### Agent result (already implemented by every agent)

`GET /agent/v1/tasks/{task_id}/result` → `TaskResult`:

| Field | Type | Notes |
|---|---|---|
| `task_id` | UUID | |
| `status` | `queued` \| `running` \| `completed` \| `failed` | |
| `output` | string \| null | Model output when `completed` |
| `error` | string \| null | Reason when `failed` (e.g. Ollama not reachable) |

`404` if the agent never received that `task_id`.

## Versioning

Breaking changes bump the major version and the URL prefix (`/v2`). Adding
optional fields does not.
