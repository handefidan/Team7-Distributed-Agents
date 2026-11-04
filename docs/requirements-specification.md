# Team 7 — Software Requirements Specification (SRS)

## 1. Project Overview & Scope
The **Distributed Autonomous Multi-Agent Software Development Platform** orchestrates modular AI worker agents managed by a centralized Master Agent across an isolated private mesh network (Tailscale).
- **Master Agent (`:8000`):** Manages dynamic agent registrations, accepts task specifications, automates LLM-driven task decomposition into multi-domain dependency graphs (`T1, T2 -> UUID`), dynamically benchmarks and selects optimal worker agents via capability threshold matching, dispatches tasks over private HTTP with prerequisite execution guardrails, measures round-trip latency (`round_trip_ms`), and audits execution records.
- **Worker Agents (`:8001+`):** Domain-specific agents running specialized local LLMs (via Ollama, e.g., `llama3.2:3b`, `qwen2.5-coder:1.5b`), acknowledging incoming tasks synchronously within strict SLA limits (<= 200 ms), and executing inference asynchronously in the background.

### Team 7 Contributors & Responsibilities
| Full Name | Student ID | Sprint 1 Focus | Sprint 2 Focus | Sprint 3 Focus |
| :--- | :---: | :--- | :--- | :--- |
| **Hande Reyyan Fidan** | 2304010611 | Backlog, DoD, SRS Architecture, Sprint 1 Demo & Review | Git Owner, README & SRS API Specifications, QA & Sprint 2 Docs | Dependency Resolution, Execution Guardrails & Pytest Coverage |
| **Berat Ermiş** | 2304010804 | Master-Agent Prototype, Task Decomposition, Agent Registry | Task Dispatch API Contract, Acknowledgement Pipeline, Error Handling | Master Gateway Integration, Lifecycle Endpoints & Ollama Setup |
| **Eyad Ahmed Mahmoud Zaidan** | 2404010370 | Web Interface Frontend & Agent Registration | Worker Agent Ingestion, Background Execution, Health Telemetry | Worker Execution Verification & Telemetry Polling |
| **Nehir Tunç** | 2304010608 | Task Decomposition, System Architecture Diagram & Repo Setup | Tailscale Network Isolation & Private IP Binding Layer, Test Setup | Tailscale Network Isolation Audit & Multi-Node Test Routing |
| **Mert Durdu** | 2304010805 | Requirements Finalization & Quality Assurance | Dynamic Agent Selection Algorithm, Capability Matrix Benchmarking | Fixed Task Capability Threshold Mapping & Tests |

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

### 2.2 Automated Task Decomposition & Dependency Engine
- **FR-04: Requirement Decomposition & Label Mapping Endpoint (Master)**
  - Endpoints: `POST /api/v1/requirements/decompose` and `POST /api/v1/requirements/decompose_and_map`.
  - Processes natural language software requirements via local Ollama models (`llama3.2:3b` / `qwen2.5-coder`).
  - Decomposes requests deterministically into a structured task breakdown spanning 7 key domains: `database`, `backend`, `frontend`, `testing`, `documentation`, `requirements`, `integration`.
  - Maps human-readable task labels (e.g., `T1`, `T2`) to runtime `UUID` identifiers via `DependencyManager`, registering upstream prerequisites to construct a directed execution graph.

### 2.3 Fixed Capability Threshold Mapping & Selection Algorithm
- **FR-05: Dynamic Task Routing & Capability Benchmarking (Master)**
  - Endpoint: `POST /api/v1/tasks/dispatch` (Payload: `TaskSpec`).
  - Automatically derives `required_capabilities` per task category via `master/classify.py` (Main capability threshold = 80, Secondary capability threshold = 60).
  - Filters candidate agents based on threshold compliance (`agent.capabilities >= required_capabilities`).
  - Selects candidate with highest total score; breaks ties deterministically using `agent_id`. Returns HTTP `404 Not Found` if no registered agent meets the criteria.

### 2.4 Task Dependency Resolution & Execution Guardrails (Sprint 3)
- **FR-06: Prerequisite Dispatch Guardrail (Master)**
  - Endpoints: `POST /api/v1/tasks/dispatch` and `POST /api/v1/agents/{agent_id}/tasks`.
  - Before dispatching a task, evaluates `dep_manager.is_ready_for_dispatch(task_uuid)`.
  - If any prerequisite upstream dependency is not in `COMPLETED` status, blocks dispatch immediately with HTTP `400 Bad Request`.
- **FR-07: Task Completion & Downstream Unblocking (Master)**
  - Endpoint: `POST /api/v1/tasks/{task_id}/complete`.
  - Marks a finished task as `COMPLETED` and automatically transitions dependent `BLOCKED` downstream tasks to `PENDING` status.

### 2.5 Task Dispatch & Acknowledgment Pipeline
- **FR-08: Direct Task Ingestion & Dispatch (Master)**
  - Endpoint: `POST /api/v1/agents/{agent_id}/tasks` (Payload: `TaskSpec`).
  - Compiles `TaskMessage` (attaching `contract_version="1.0"`, `agent_id`, and `sent_at`), forwarding via HTTP POST to worker's `/agent/v1/tasks`.
- **FR-09: Worker Reception & Guardrail Validation (Worker)**
  - Endpoint: `POST /agent/v1/tasks` (Payload: `TaskMessage`).
  - Synchronously returns `TaskAcknowledgement`. Rejects misaddressed tasks, unsupported domains, or exceeded queue capacity (`len(queue) >= max_queue`).
- **FR-10: Idempotent Retries & Dispatch Persistence (Master)**
  - Resubmitting an identical `task_id` returns cached acknowledgment with `duplicate: True`. Master tracks history in `dispatches: dict[UUID, DispatchRecord]`.

### 2.6 Background Execution & Result Retrieval
- **FR-11: Non-Blocking Background Offloading (Worker)**
  - Tasks transition to `ExecutionStatus.QUEUED` and execute asynchronously via `BackgroundTasks` without blocking HTTP response.
- **FR-12: Ollama LLM Inference Engine & Result Querying (Worker)**
  - Endpoint: `GET /agent/v1/tasks/{task_id}/result`. Formulates structured prompts and queries Ollama `/api/generate` asynchronously.

---

## 3. Non-Functional Requirements (NFR)
- **NFR-01: Low Latency Acknowledgment:** The master-to-worker HTTP acknowledgment cycle must execute within <= 200 ms.
- **NFR-02: Resilience & HTTP Error Semantics:**
  - Slow workers exceeding timeout limits trigger `504 Gateway Timeout`.
  - Unreachable worker instances trigger `503 Service Unavailable`.
  - Contract validation errors and malformed payloads trigger `502 Bad Gateway`.
  - Unmet dependency prerequisites trigger `400 Bad Request`.
- **NFR-03: Network Isolation & Private Binding:** Master and worker processes bind strictly to private Tailscale IPv4 interfaces (`tailscale ip -4`). Missing Tailscale interfaces trigger loud `TailscaleIPError` exits unless explicitly overridden by `TAILSCALE_IP=127.0.0.1` for local unit testing.
- **NFR-04: Configuration via Environment Variables:** 12-factor configuration support (`AGENT_ID`, `AGENT_MODEL`, `AGENT_SUPPORTED_TYPES`, `AGENT_MAX_QUEUE`, `OLLAMA_BASE_URL`, `MASTER_PORT`, `AGENT_PORT`, `TAILSCALE_IP`).
- **NFR-05: Testability via Automated Suite:** Applications pass **39 automated unit and integration tests** in `tests/` (`test_classify.py`, `test_decompose.py`, `test_dependency.py`, `test_dispatch.py`, `test_selection.py`) running entirely in-memory using `httpx.ASGITransport` without live Ollama daemons.