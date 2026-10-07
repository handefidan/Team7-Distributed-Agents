# Team 7 — Software Requirements Specification (SRS)

## 1. Project Overview & Scope
The **Distributed Autonomous Multi-Agent Software Development Platform** orchestrates modular AI worker agents managed by a centralized Master Agent.
* **Master Agent (`:8000`):** Manages dynamic agent registrations, accepts task specifications, dispatches tasks over HTTP, measures round-trip latency (`round_trip_ms`), and audits records.
* **Worker Agents (`:8001+`):** Domain-specific agents running specialized local LLMs (via Ollama), acknowledging incoming tasks synchronously, and executing inference asynchronously in the background.

### Team 7 Contributors
| Full Name | Student ID |
| :--- | :---: |
| **Hande Reyyan Fidan** | 2304010611 |
| **Berat Ermiş** | 2304010804 |
| **Eyad Ahmed Mahmoud Zaidan** | 2404010370 |
| **Mehin Baghirzade** | 2304010848 |
| **Nehir Tunç** | 2304010608 |
| **Mert Durdu** | 2304010805 |

*Note: The team syncs every Monday to review sprint deliverables and technical alignment.*

---

## 2. Functional Requirements (FR)

### 2.1 Dynamic Agent Registration & Health Telemetry
* **FR-01: Dynamic Registration Endpoint (Master)**
  * Endpoint: `POST /api/v1/agents` (Payload: `AgentRegistration`).
  * Constraints: Validates `agent_id` regex `^[A-Za-z0-9_-]{1,64}$`, valid `base_url`, and non-empty `model`. Returns HTTP `201 Created`.
* **FR-02: Agent Listing Endpoint (Master)**
  * Endpoint: `GET /api/v1/agents`.
  * Returns an array of all active registered agents in memory.
* **FR-03: Worker Health & Telemetry (Worker)**
  * Endpoint: `GET /agent/v1/health`.
  * Reports `agent_id`, `model`, operational state (`idle` vs. `busy`), current `queue_size`, `max_queue`, and `supported_types`.

### 2.2 Task Dispatch & Acknowledgment Pipeline (v1.0 Contract)
* **FR-04: Task Ingestion & Dispatch (Master)**
  * Endpoint: `POST /api/v1/agents/{agent_id}/tasks` (Payload: `TaskSpec`).
  * Rejects unregistered agents with `404 Not Found`. Rejects malformed bodies with `422 Unprocessable Entity`.
  * Compiles `TaskMessage` (attaching `contract_version="1.0"`, `agent_id`, and `sent_at`), then forwards via HTTP POST to the agent's `/agent/v1/tasks`.
* **FR-05: Worker Reception & Guardrail Validation (Worker)**
  * Endpoint: `POST /agent/v1/tasks` (Payload: `TaskMessage`).
  * Synchronously returns `TaskAcknowledgement`.
  * Rejection criteria (`status: rejected`):
    1. Misaddressed: `task.agent_id != settings.agent_id`.
    2. Unsupported task domain: `task.type not in settings.supported_types`.
    3. Queue capacity exceeded: `len(queue) >= settings.max_queue`.
  * If accepted, returns `status: accepted` with 1-based `queue_position`.
* **FR-06: Idempotent Retries**
  * Resubmitting an identical `task_id` (UUID) returns the original cached acknowledgment with `duplicate: True`.
* **FR-07: Dispatch Persistence & Metrics (Master)**
  * Master records all dispatch attempts in `dispatches: dict[UUID, DispatchRecord]`, computing execution latency (`round_trip_ms`).
  * Accessible via `GET /api/v1/tasks` and `GET /api/v1/tasks/{task_id}`.

### 2.3 Background Execution & Result Retrieval
* **FR-08: Non-Blocking Background Offloading (Worker)**
  * Accepted tasks transition to `status: ExecutionStatus.QUEUED` and are processed via FastAPI `BackgroundTasks`.
* **FR-09: Ollama LLM Inference (Worker)**
  * Formulates prompt (`Task type`, `Title`, `Description`, `Context`) and invokes Ollama `/api/generate` asynchronously.
* **FR-10: Result Querying (Worker)**
  * Endpoint: `GET /agent/v1/tasks/{task_id}/result`.
  * Returns `TaskResult` (`task_id`, `status: ExecutionStatus`, `output`, `error`). Returns `404 Not Found` if the task is unrecognized.

---

## 3. Non-Functional Requirements (NFR)
* **NFR-01: Low Latency Acknowledgment:** The master-to-worker HTTP acknowledgment cycle must execute within <= 200 ms.
* **NFR-02: Resilience & HTTP Error Semantics:**
  * Slow workers (> 5s) trigger `504 Gateway Timeout`.
  * Unreachable workers trigger `503 Service Unavailable`.
  * Schema mismatches trigger `502 Bad Gateway`.
* **NFR-03: Configuration via Environment Variables:** 12-factor configuration support (`AGENT_ID`, `AGENT_MODEL`, `AGENT_SUPPORTED_TYPES`, `AGENT_MAX_QUEUE`, `OLLAMA_BASE_URL`, `OLLAMA_TIMEOUT_SECONDS`, `AGENT_TIMEOUT_SECONDS`).
* **NFR-04: Testability via Mock Transports:** Applications accept custom `httpx.AsyncBaseTransport` instances for testing without live daemons.
