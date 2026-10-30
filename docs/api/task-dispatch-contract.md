# Task Dispatch API Contract (v1.0)

Sprint 2 · Owner: Berat Ermiş
Reviewed and approved for Sprint 2 Week 3-4: still accurate as written below.
Week 4 adds `POST /api/v1/tasks/dispatch`, where the master itself picks the
agent (see "Agent selection" below) — the original
`POST /api/v1/agents/{agent_id}/tasks` is unchanged and still available for
sending to an explicitly chosen agent. The master now also pre-registers the
agents in `agents.json` at startup and skips busy or offline agents when
picking one.

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
| `POST /api/v1/agents` | Register an agent (`agent_id`, `base_url`, `model`, `capabilities`) — added to the registry seeded from `agents.json`; not written back to the file |
| `GET /api/v1/agents` | List registered agents |
| `POST /api/v1/agents/{agent_id}/tasks` | Send a task to the **named** agent and wait for its acknowledgement |
| `POST /api/v1/tasks/dispatch` | Send a task to the agent **the selection algorithm picks** and wait for its acknowledgement |
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

`base_url` is optional. An agent without one is registered but always
counts as offline: it is never picked by `/tasks/dispatch`, and sending to
it by name returns `503`.

### Startup registry (`agents.json`)

On startup the master registers every agent in `backend/agents.json` (or
the file named by `MASTER_AGENTS_FILE`), so the list never starts empty.
Each entry uses the `AgentRegistration` fields above; other keys (`name`,
`provider`, `description`) are ignored. An entry that fails validation (for
example, one without an `agent_id`) is skipped with a warning in the log
and doesn't stop the master from starting. If the file doesn't exist, the
master starts with no agents.

```json
{
  "agent_id": "qwen-coder-large",
  "model": "qwen2.5-coder:7b",
  "base_url": "http://100.64.0.12:8001",
  "capabilities": { "coding": 94, "reasoning": 88, "context": 90 }
}
```

### `POST /api/v1/tasks/dispatch`

Request body: `TaskSpec`, the same as `POST /api/v1/agents/{agent_id}/tasks`
below. It takes an optional `required_capabilities` object with the same
shape as `CapabilityScores`, defaulting to 50/50/50. The selection logic is
in `master/selection.py`:

1. Keep only registered agents whose `capabilities` meet or exceed every
   field of the task's `required_capabilities`.
2. Ask each of those agents for its status via `GET /agent/v1/health`, all
   at once, waiting at most `AGENT_HEALTH_TIMEOUT_SECONDS` (default 2):
   - `idle`: the agent reported `"status": "idle"`
   - `busy`: the agent reported anything else (it has tasks in its queue)
   - `offline`: no `base_url`, unreachable, timed out, or an error response
3. Among the `idle` agents, pick the one with the highest total score
   (`coding + reasoning + context`); ties break on `agent_id`.

Once an agent is picked, the task is sent exactly as in `POST
/api/v1/agents/{agent_id}/tasks` below, with the same response and error
codes.

When no agent can be picked, the response is `{"detail": "<message>"}`:

| HTTP | `detail` | When |
|---|---|---|
| 404 | `no agents are registered` | The registry is empty |
| 404 | `no registered agent meets the required capabilities {...}` | No agent scores high enough |
| 503 | `every agent that meets the required capabilities is busy or offline: qwen-coder-large is busy, codellama-ui is offline` | Agents qualify, but none is idle right now |

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
