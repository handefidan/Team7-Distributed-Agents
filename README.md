# Distributed Agents — Task Dispatch (Sprint 2)

Master agent can send a task to a chosen worker agent and receive an
acknowledgement. API contract: [docs/api/task-dispatch-contract.md](docs/api/task-dispatch-contract.md).

```
shared/schemas.py   message formats (the contract in code)
master/main.py      master API  (port 8000)
agent/main.py       worker agent API (port 8001+), one per computer
tests/              pytest suite
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
# Terminal 1 — an agent (on any computer)
AGENT_ID=agent-a AGENT_MODEL=llama3 AGENT_SUPPORTED_TYPES=backend,testing \
  uvicorn agent.main:app --host 0.0.0.0 --port 8001

# Terminal 2 — the master
uvicorn master.main:app --port 8000
```

Agent environment variables: `AGENT_ID`, `AGENT_MODEL` (must match a model
pulled with `ollama pull <name>`), `AGENT_SUPPORTED_TYPES` (comma-separated,
default all), `AGENT_MAX_QUEUE` (default 5), `OLLAMA_BASE_URL` (default
`http://localhost:11434`), `OLLAMA_TIMEOUT_SECONDS` (default 120). Master:
`AGENT_TIMEOUT_SECONDS` (default 5).

An agent needs a running Ollama server to execute tasks: `ollama serve` (or
the system service), then `ollama pull <model>` once per model. A task is
still acknowledged immediately even if Ollama is unreachable or the pulled
model name doesn't match `AGENT_MODEL` — execution then fails in the
background and `GET /agent/v1/tasks/{task_id}/result` reports `status:
"failed"` with the reason.

## Try it

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

Or use the Swagger UI at http://localhost:8000/docs.

For agents on other computers, use that computer's IP in `base_url`
(e.g. `http://192.168.1.42:8001`) and start the agent with `--host 0.0.0.0`.

## Test

```bash
pytest -q
```
