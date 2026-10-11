# Distributed Agents — Task Dispatch (Sprint 2)

Master agent can send a task to a chosen worker agent and receive an
acknowledgement. API contract: [docs/api/task-dispatch-contract.md](docs/api/task-dispatch-contract.md).

```
shared/schemas.py     message formats (the contract in code)
master/decompose.py   requirement -> subtasks (Sprint 1)
master/main.py        master API  (port 8000)
agent/main.py         worker agent API (port 8001+), one per computer
tests/                pytest suite
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

Each computer needs Tailscale installed and logged into the same tailnet
(`tailscale up`). The master and every agent bind to that machine's
Tailscale IP, not `0.0.0.0`, so the API is only reachable over the tailnet —
not the LAN or the open internet.

```bash
# Terminal 1 — an agent (on any computer in the tailnet)
AGENT_ID=agent-a AGENT_MODEL=llama3 AGENT_SUPPORTED_TYPES=backend,testing \
  python -m agent.main

# Terminal 2 — the master (same or another computer in the tailnet)
python -m master.main
```

Agent environment variables: `AGENT_ID`, `AGENT_MODEL` (must match a model
pulled with `ollama pull <name>`), `AGENT_SUPPORTED_TYPES` (comma-separated,
default all), `AGENT_MAX_QUEUE` (default 5), `OLLAMA_BASE_URL` (default
`http://localhost:11434`), `OLLAMA_TIMEOUT_SECONDS` (default 120),
`AGENT_PORT` (default 8001). Master: `AGENT_TIMEOUT_SECONDS` (default 5),
`MASTER_PORT` (default 8000).

Both read their bind address from `tailscale ip -4`. If the `tailscale` CLI
isn't available or you want to pin a specific address, set `TAILSCALE_IP`
instead — startup fails loudly if neither is available, rather than silently
falling back to `0.0.0.0`.

An agent needs a running Ollama server to execute tasks: `ollama serve` (or
the system service), then `ollama pull <model>` once per model. A task is
still acknowledged immediately even if Ollama is unreachable or the pulled
model name doesn't match `AGENT_MODEL` — execution then fails in the
background and `GET /agent/v1/tasks/{task_id}/result` reports `status:
"failed"` with the reason.

## Try it

### Decompose a requirement (Sprint 1)

```bash
curl -X POST localhost:8000/api/v1/requirements/decompose -H 'content-type: application/json' \
  -d '{"requirement_text":"Build a library management system where librarians can add, update, and remove books, members can borrow and return books, and the system tracks due dates and overdue fines."}'
```

Returns a `project_title`, `summary`, and a `tasks` list — each task has a
`task_id`, `category` (`frontend`/`backend`/`database`/`testing`/`documentation`/…),
`description`, and `dependencies` (other `task_id`s that must finish first).
Empty or whitespace-only `requirement_text` returns `400` with a clear error.

This calls a local Ollama model (same setup as the worker agents — no cloud
API key). Master env vars: `MASTER_OLLAMA_BASE_URL` (default
`http://localhost:11434`), `MASTER_OLLAMA_MODEL` (default `llama3`, must be
pulled with `ollama pull <name>`), `MASTER_OLLAMA_TIMEOUT_SECONDS` (default
120). If Ollama is unreachable or answers with something that doesn't fit
the schema, the endpoint falls back to a fixed, deterministic breakdown
instead of failing — so it still works offline and in CI.

### Dispatch a task to an agent (Sprint 2)

```bash
curl -X POST localhost:8000/api/v1/agents -H 'content-type: application/json' \
  -d '{"agent_id":"agent-a","base_url":"http://localhost:8001","model":"llama3"}'

curl -X POST localhost:8000/api/v1/agents/agent-a/tasks -H 'content-type: application/json' \
  -d '{"type":"backend","title":"Book CRUD API","description":"Create REST endpoints for books."}'
```

The response's `task.task_id` lets you poll the agent directly for the
model's output once it finishes running (execution is a Sprint 3 preview,
separate from the ack contract above):

```bash
curl localhost:8001/agent/v1/tasks/<task_id>/result
```

Or use the Swagger UI at http://localhost:8000/docs (from the master's own
machine) or `http://<master-tailscale-ip>:8000/docs` from elsewhere in the
tailnet.

For agents on other computers, use that computer's Tailscale IP in
`base_url` (e.g. `http://100.x.y.z:8001`) — `tailscale ip -4` on that
computer prints it, or `tailscale status` lists every machine's address
from any computer in the tailnet.

## Test

```bash
pytest -q
```
