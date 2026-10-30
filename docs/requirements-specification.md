# Team 7 — Software Requirements Specification (SRS)

## 1. Project Overview & Scope
The **Distributed Autonomous Multi-Agent Software Development Platform** orchestrates modular AI worker agents managed by a centralized Master Agent across an isolated private mesh network (Tailscale).
- **Master Agent (`:8000`):** Manages dynamic agent registrations, accepts task specifications, automates LLM-driven task decomposition into multi-domain dependency graphs, dynamically benchmarks and selects optimal worker agents via weighted capability scoring, dispatches tasks over private HTTP, measures round-trip latency (`round_trip_ms`), and audits execution records.
- **Worker Agents (`:8001+`):** Domain-specific agents running specialized local LLMs (via Ollama, e.g., `llama3`, `qwen2.5-coder:1.5b`), acknowledging incoming tasks synchronously within strict SLA limits (<= 200 ms), and executing inference asynchronously in the background.

### Team 7 Contributors & Responsibilities
| Full Name | Student ID | Sprint 1 Focus | Sprint 2 Focus |
| :--- | :---: | :--- | :--- |
| **Hande Reyyan Fidan** | 2304010611 | Backlog, DoD, SRS Architecture, Sprint 1 Demo & Review | Git Owner, README & SRS API Specifications, Quality Assurance & Sprint 2 Documentation |
| **Berat Ermiş** | 2304010804 | Master-Agent Prototype, Task Decomposition, Agent/Model Registry | Task Dispatch API Contract, Acknowledgement Pipeline, Error Handling (502/503/504) |
| **Eyad Ahmed Mahmoud Zaidan** | 2404010370 | Web Interface Frontend & Agent Registration Integration | Worker Agent Ingestion & Non-blocking Background Execution, Agent Health & Telemetry |
| **Nehir Tunç** | 2304010608 | Task Decomposition, System Architecture Diagram & Repo Setup | Tailscale Network Isolation & Private IP Binding Layer, Test Framework Setup |
| **Mert Durdu** | 2304010805 | Requirements Finalization & Quality Assurance | Dynamic Agent Selection Algorithm, Capability Matrix Benchmarking, Pytest Suite Integration |

*Note: Former team member Mehin Baghirzade (Student ID: 2304010848) departed during Sprint 2 by her own volition, officially communicated to Teaching Assistant Res. Asst. Elif Bilge Şahin. The team syncs weekly to review sprint deliverables, Trello tasks, and technical alignment.*

---

## 2. Functional Requirements (FR)

### 2.1 Dynamic Agent Registration & Health Telemetry
- **FR-01: Dynamic Registration Endpoint (Master)**
  - Endpoint: `POST /api/v1/agents` (Payload: `AgentRegistration`).
  - Constraints: Validates `agent_id` regex `^[A-Za-z0-9_-]{1,64}$`, valid `base_url`, non-empty `model`, and quantitative capability metrics:
    - `capabilities.coding` (Integer, 0–100)
    - `capabilities.reasoning` (Integer, 0–100)
    - `capabilities.context` (Integer, 0–100)
  - Returns HTTP `201 Created`.
- **FR-02: Agent Listing & Management (Master)**
  - Endpoints: `GET /api/v1/agents` (returns active in-memory registered agents) and `DELETE /api/v1/agents/{agent_id}` (unregisters worker nodes).
- **FR-03: Worker Health & Telemetry (Worker)**
  - Endpoint: `GET /agent/v1/health`.
  - Reports `agent_id`, `model`, operational state (`idle` vs. `busy`), current `queue_size`, `max_queue`, and `supported_types`.

### 2.2 Automated Task Decomposition Engine
- **FR-04: Requirement Decomposition Endpoint (Master)**
  - Endpoint: `POST /api/v1/requirements/decompose` (Payload: `DecompositionRequest`).
  - Processes natural language software requirements via local Ollama models (`llama3` / `qwen2.5-coder`).
  - Decomposes requests deterministically into a structured task breakdown spanning 5 key domains:
    1. `database` (Schema, ORM, and migrations)
    2. `backend` (REST API routes, business logic, auth)
    3. `frontend` (UI views, forms, and interaction modals)
    4. `testing` (Unit and integration automated test suites)
    5. `documentation` (API contracts and developer guides)
  - Enforces explicit dependency resolution (`dependencies: ["T1"]`, etc.) where downstream tasks wait for prerequisite foundations. Falls back to deterministic templates when LLM inference is unreachable.

### 2.3 Dynamic Agent Selection Algorithm (Sprint 2)
- **FR-05: Dynamic Task Routing & Model Selection (Master)**
  - Endpoint: `POST /api/v1/tasks/dispatch` (Payload: `TaskSpec` with optional `required_capabilities`).
  - Filters candidate agents based on domain compatibility (`task.type in agent.supported_types`) and capability threshold compliance (`agent.capabilities >= task.required_capabilities`).
  - Computes a weighted matching score across coding, reasoning, and context metrics; ties are deterministically resolved by agent registration order or lowest latency.
  - If no suitable candidate meets the capability thresholds, responds with HTTP `404 Not Found` and a descriptive error message.

### 2.4 Task Dispatch & Acknowledgment Pipeline (v1.0 Contract)
- **FR-06: Direct Task Ingestion & Dispatch (Master)**
  - Endpoint: `POST /api/v1/agents/{agent_id}/tasks` (Payload: `TaskSpec`).
  - Rejects unregistered agents with `404 Not Found`. Rejects malformed bodies with `422 Unprocessable Entity`.
  - Compiles `TaskMessage` (attaching `contract_version="1.0"`, `agent_id`, and `sent_at`), then forwards via HTTP POST to the agent's `/agent/v1/tasks`.
- **FR-07: Worker Reception & Guardrail Validation (Worker)**
  - Endpoint: `POST /agent/v1/tasks` (Payload: `TaskMessage`).
  - Synchronously returns `TaskAcknowledgement`.
  - Rejection criteria (`status: rejected`):
    1. Misaddressed: `task.agent_id != settings.agent_id`.
    2. Unsupported task domain: `task.type not in settings.supported_types`.
    3. Queue capacity exceeded: `len(queue) >= settings.max_queue`.
  - If accepted, returns `status: accepted` with 1-based `queue_position`.
- **FR-08: Idempotent Retries**
  - Resubmitting an identical `task_id` (UUID) returns the original cached acknowledgment with `duplicate: True`.
- **FR-09: Dispatch Persistence & Metrics (Master)**
  - Master records all dispatch attempts in `dispatches: dict[UUID, DispatchRecord]`, computing round-trip execution latency (`round_trip_ms`).
  - Accessible via `GET /api/v1/tasks` and `GET /api/v1/tasks/{task_id}`.

### 2.5 Background Execution & Result Retrieval
- **FR-10: Non-Blocking Background Offloading (Worker)**
  - Accepted tasks transition to `status: ExecutionStatus.QUEUED` and are executed asynchronously via FastAPI `BackgroundTasks` without blocking the HTTP acknowledgment.
- **FR-11: Ollama LLM Inference Engine (Worker)**
  - Formulates structured prompts (`Task type`, `Title`, `Description`, `Context`) and invokes Ollama `/api/generate` asynchronously.
- **FR-12: Result Querying (Worker)**
  - Endpoint: `GET /agent/v1/tasks/{task_id}/result`.
  - Returns `TaskResult` (`task_id`, `status: ExecutionStatus`, `output`, `error`). Returns `404 Not Found` if the task ID is unrecognized.

---

## 3. Non-Functional Requirements (NFR)
- **NFR-01: Low Latency Acknowledgment:** The master-to-worker HTTP acknowledgment cycle must execute within <= 200 ms.
- **NFR-02: Resilience & HTTP Error Semantics:**
  - Slow workers exceeding timeout limits trigger `504 Gateway Timeout`.
  - Unreachable worker instances trigger `503 Service Unavailable`.
  - Contract validation errors and malformed payloads trigger `502 Bad Gateway`.
- **NFR-03: Network Isolation & Private Binding:** Master and worker processes bind strictly to private Tailscale IPv4 interfaces (`tailscale ip -4`). Binding to `0.0.0.0` or public internet interfaces is strictly forbidden; missing Tailscale interfaces trigger loud `TailscaleIPError` exits unless explicitly overridden by `TAILSCALE_IP=127.0.0.1` for local unit testing.
- **NFR-04: Configuration via Environment Variables:** 12-factor configuration support (`AGENT_ID`, `AGENT_MODEL`, `AGENT_SUPPORTED_TYPES`, `AGENT_MAX_QUEUE`, `OLLAMA_BASE_URL`, `OLLAMA_TIMEOUT_SECONDS`, `AGENT_TIMEOUT_SECONDS`, `MASTER_PORT`, `AGENT_PORT`, `TAILSCALE_IP`).
- **NFR-05: Testability via Mock Transports:** Applications accept custom `httpx.AsyncBaseTransport` / `httpx.MockTransport` instances, enabling end-to-end automated execution across all 21 unit/integration test suites without requiring live Ollama daemons or internet connectivity.
