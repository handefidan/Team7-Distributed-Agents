# Task Dispatch API Contract (v1.0)

Sprint 2 · Owner: Berat Ermiş

How the master agent sends a task to a chosen worker agent and how the agent
acknowledges it. Choosing *which* agent gets a task (agent selection algorithm)
is out of scope; this contract starts once the master has picked an agent.

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
into its queue or rejected). Execution results are a separate Sprint 3 flow.

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
| `POST /api/v1/agents` | Register an agent (`agent_id`, `base_url`, `model`) — temporary in-memory registry |
| `GET /api/v1/agents` | List registered agents |
| `POST /api/v1/agents/{agent_id}/tasks` | **Send a task** to the chosen agent and wait for its acknowledgement |
| `GET /api/v1/tasks` | List all dispatch records |
| `GET /api/v1/tasks/{task_id}` | Get one dispatch record |

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

## Versioning

Breaking changes bump the major version and the URL prefix (`/v2`). Adding
optional fields does not.
