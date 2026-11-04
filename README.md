# Team 7 — Distributed Autonomous LLM Multi-Agent System

A distributed multi-agent system where a central Master Agent decomposes software requirements into typed subtasks, dynamically evaluates candidate worker nodes via capability benchmarking, dispatches tasks over a secure private network (Tailscale), and receives non-blocking acknowledgements while workers execute prompts locally via Ollama.

- **API Contract Specification:** [`docs/api/task-dispatch-contract.md`](docs/api/task-dispatch-contract.md)
- **Master ↔ Agent Message Flow:** [`docs/api/message-flow.md`](docs/api/message-flow.md)
- **Model Evaluation Criteria:** [`docs/model-evaluation-criteria.md`](docs/model-evaluation-criteria.md)
- **Architecture Overview:** [`docs/architecture.md`](docs/architecture.md)
- **Sprint Reports:** [`Sprint 1`](docs/Team7_Sprint1_Report.docx) · [`Sprint 2`](docs/Team7_Sprint2_Report.docx)
- **Trello Board:** <https://trello.com/b/Zc1eozhQ>

```
backend/                  Sprint 1 web page API (port 8000)
frontend/index.html       Sprint 1 web interface (browser UI)
shared/schemas.py         API Contract v1.0 (Pydantic models & validation)
shared/network.py         Tailscale IP binding & fail-safe interface isolation
master/decompose.py       Requirement -> subtasks breakdown (Sprint 1)
master/classify.py        Fixed capability threshold mapping per task type (Sprint 2)
master/selection.py       Deterministic capability-based agent selection algorithm (Sprint 2)
master/dependency.py      Label-to-UUID mapping & prerequisite execution guardrails (Sprint 3)
master/main.py            Master Agent API gateway (port 8000)
agent/main.py             Worker Agent execution API & Ollama engine (port 8001+)
tests/                    Pytest suite (39 unit and integration tests)
docs/                     Architecture diagrams, specifications, and sprint reports
```

## Setup & Environment

```bash
# 1. Create and activate virtual environment
python -m venv .venv

# On Linux/macOS:
source .venv/bin/activate
# On Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt
```

---

## API Endpoints Matrix

All Master and Worker endpoints bind strictly to the private Tailscale interface (`shared/network.py`) or fallback to `TAILSCALE_IP`.

### Master Agent (`http://<master-tailscale-ip>:8000`)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/requirements/decompose` | Decomposes raw requirement text into structured, typed subtasks with capability weights. |
| `POST` | `/api/v1/requirements/decompose_and_map` | Decomposes requirements and automatically maps text labels (`T1`, `T2`) to runtime UUIDs with dependency graphs. |
| `POST` | `/api/v1/agents` | Registers a worker agent node with model metadata and capability scores (0–100). |
| `GET`  | `/api/v1/agents` | Lists all active registered worker agents. |
| `POST` | `/api/v1/tasks/dispatch` | Evaluates candidate nodes and dispatches a task to the best matching agent if prerequisites are satisfied. |
| `POST` | `/api/v1/agents/{agent_id}/tasks` | Dispatches a task directly to a specific target agent node (guarded by dependency status). |
| `GET`  | `/api/v1/plan/ready` | Lists mapped tasks whose prerequisites are all completed (ready to send). |
| `GET`  | `/api/v1/plan/tasks/{task_id}/spec` | Ready-to-send `TaskSpec` (UUID dependencies, outputs of finished prerequisites in `context`). |
| `POST` | `/api/v1/tasks/{task_id}/complete` | Marks a task as completed (optional `{"output": "..."}`) and unblocks downstream tasks waiting on it. |
| `POST` | `/api/v1/tasks/{task_id}/fail` | Marks a task as failed; tasks depending on it stay blocked. |
| `GET`  | `/api/v1/tasks` | Lists all dispatch history records and round-trip execution logs. |
| `GET`  | `/api/v1/tasks/{task_id}` | Retrieves dispatch status, errors, and worker acknowledgement for a specific task. |

### Worker Agent (`http://<worker-tailscale-ip>:8001+`)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/agent/v1/tasks` | Ingests a task, returns immediate ACK (`<= 200 ms`), and executes via Ollama in the background. |
| `GET`  | `/agent/v1/tasks/{task_id}/result` | Polls the asynchronous execution status and output text for a task. |
| `GET`  | `/agent/v1/health` | Telemetry endpoint returning node status (`idle`/`busy`), queue size, and supported task types. |

---

## Running the System

### Network Security (Tailnet Binding)
Each node must run Tailscale logged into the team tailnet (`tailscale up`). Services automatically read their bind address from `tailscale ip -4`. If neither Tailscale nor the `TAILSCALE_IP` environment variable is detected, startup fails loudly with `TailscaleIPError` to prevent accidental public network exposure (`0.0.0.0`).

### 1. Start a Worker Agent (Port 8001+)
```bash
# Pull model before starting (one time)
ollama run llama3.2:3b

# Run worker agent
AGENT_ID=agent-a AGENT_MODEL=llama3.2:3b AGENT_SUPPORTED_TYPES=backend,testing \
  python -m agent.main
```
*Worker Configuration Variables:*
- `AGENT_ID`: Unique node identifier (alphanumeric, dashes, underscores).
- `AGENT_MODEL`: Model name registered in Ollama.
- `AGENT_SUPPORTED_TYPES`: Comma-separated list (`frontend,backend,database,testing,documentation`).
- `AGENT_MAX_QUEUE`: Maximum queue depth (default `5`, excess triggers HTTP 200 rejection).
- `AGENT_PORT`: Port to listen on (default `8001`).

### 2. Start the Master Agent (Port 8000)
```bash
python -m master.main
```
*Master Configuration Variables:*
- `MASTER_PORT`: Gateway port (default `8000`).
- `AGENT_TIMEOUT_SECONDS`: Maximum latency tolerated for worker task ACK (default `5` seconds).

---

## Local Verification & Testing

To test everything on a single machine without an active Tailscale network, pin the bind address to localhost:

```bash
# PowerShell (Windows):
$env:TAILSCALE_IP="127.0.0.1"

# Linux / macOS:
export TAILSCALE_IP=127.0.0.1
```

### 1. Decompose & Map Requirements (Sprint 3)
```bash
curl -X POST http://localhost:8000/api/v1/requirements/decompose_and_map \
  -H "Content-Type: application/json" \
  -d '{"requirement_text": "Build a library management system with loan tracking and overdue fine calculation."}'
```

### 2. Register Agents
```bash
# Register a high-capability agent
curl -X POST http://localhost:8000/api/v1/agents \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"agent-strong","base_url":"http://localhost:8001","model":"llama3.2:3b","capabilities":{"coding":90,"reasoning":85,"context":80}}'

# Register a lightweight agent
curl -X POST http://localhost:8000/api/v1/agents \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"agent-weak","base_url":"http://localhost:8002","model":"qwen2.5-coder:1.5b","capabilities":{"coding":40,"reasoning":40,"context":50}}'
```

### 3. Dynamic Dispatching & Model Selection (Sprint 2 & 3)
Dispatch a task requiring high coding capability (`coding >= 80`). The master automatically routes the task to `agent-strong` provided all prerequisite dependencies are satisfied:
```bash
curl -X POST http://localhost:8000/api/v1/tasks/dispatch \
  -H "Content-Type: application/json" \
  -d '{"type":"backend","title":"Book CRUD API","description":"Build REST endpoints","required_capabilities":{"coding":80,"reasoning":0,"context":0}}'
```

### 4. Fetch Background Execution Result
```bash
curl http://localhost:8001/agent/v1/tasks/<task_id>/result
```

---

## Automated Test Suite

All 39 automated unit and integration tests run entirely in-memory using `httpx.ASGITransport` and `httpx.MockTransport` without requiring external network connectivity or a running Ollama daemon:

```bash
python -m pytest -q
```

**Test Coverage:**
- `tests/test_classify.py` (16 tests): Fixed capability threshold mapping per task type, type-to-score validation, model override guards.
- `tests/test_decompose.py` (5 tests): Requirement parsing, fallback handling on model error, empty payload rejection.
- `tests/test_dependency.py` (14 tests): Label-to-UUID mapping, lifecycle (`PENDING`/`BLOCKED`/`DISPATCHED`/`COMPLETED`/`FAILED`), cycle detection, ready-task ordering, dependency outputs in `context`, API-level dependency flow.
- `tests/test_dispatch.py` (12 tests): SLA acknowledgement (<= 200 ms), idempotency, queue overflow, network timeouts (504), unreachable hosts (503), health status.
- `tests/test_selection.py` (4 tests): Capability-based routing algorithm, threshold filtering, score tie-breaking, 404 unfulfilled criteria handling.