# Team 7 — Product Backlog and Definition of Done (DoD)

## 1. Project & Team Information
* **Project Name:** Distributed Autonomous Multi-Agent Software Development Platform
* **Team:** Team 7
* **Sprint Window:** Sprint 1 & Sprint 2

### Team Members
| Full Name | Student ID | Role / Primary Domain |
| :--- | :---: | :--- |
| **Hande Reyyan Fidan** | 2304010611 | Product Backlog, Definition of Done & Requirements Architecture |
| **Berat Ermiş** | 2304010804 | Task Dispatch API Contract & Distribution Architecture |
| **Eyad Ahmed Mahmoud Zaidan** | 2404010370 | Task Decomposition Engine & Unit Test Suite |
| **Mehin Baghirzade** | 2304010848 | Web Interface & Client Integration |
| **Nehir Tunç** | 2304010608 | System Requirements & Domain Modeling |
| **Mert Durdu** | 2304010805 | Verification, Quality Assurance & Deployment |

### Team Cadence & Collaboration
* **Weekly Sync Meetings:** The team conducts synchronous coordination sessions **every Monday** to review completed tasks, align architectural contracts, and plan sprint deliverables.
* **Meeting Log Summary:**
  * **Week 1 (Monday):** Initial project scope alignment, architectural decomposition, and responsibility division.
  * **Week 2 (Monday):** Review of API Contract v1.0, validation of schema models, and verification of dispatch mechanisms.

---

## 2. Definition of Done (DoD)
A backlog item or technical deliverable is considered **Done** only when all the following benchmarks are satisfied:
1. **Schema & Model Compliance:** All data models and payloads strictly adhere to `shared/schemas.py` (`AgentRegistration`, `TaskSpec`, `TaskMessage`, `TaskAcknowledgement`, `DispatchRecord`, `TaskResult`).
2. **Deterministic Test Verification:** All test suites in `tests/test_dispatch.py` execute and pass via `pytest -q` using mock transports (`mock_ollama`, `httpx.MockTransport`, `httpx.ASGITransport`) without requiring running external daemons.
3. **Non-Blocking Operation:** Worker agents synchronously acknowledge tasks within latency bounds (<= 200 ms), offloading execution to background workers.
4. **Idempotency & Resiliency:** Resending existing tasks returns cached acknowledgments (`duplicate: True`). Invalid targets (404), full queues (rejected), and unhandled timeouts (504) are properly audited.
5. **Configuration Standard:** Environment variables (`AGENT_ID`, `AGENT_MODEL`, `AGENT_SUPPORTED_TYPES`, `AGENT_MAX_QUEUE`, `OLLAMA_BASE_URL`, `OLLAMA_TIMEOUT_SECONDS`, `AGENT_TIMEOUT_SECONDS`) are fully functional without hardcoded variables.
6. **Repository Cleanliness:** Changes are integrated into the `main` branch with descriptive semantic commit messages, and dependencies are pinned in `requirements.txt`.

---

## 3. Product Backlog

| Backlog ID | Feature / Component | Description | Priority | Sprint | Owner |
| :---: | :--- | :--- | :---: | :---: | :---: |
| **PB-01** | Master Core | **Agent Registration & Discovery:** In-memory registry (`POST /api/v1/agents`, `GET /api/v1/agents`) tracking active worker nodes. | High | Sprint 1 | Hande Reyyan Fidan |
| **PB-02** | Master Core | **Requirements Decomposition:** Master agent decomposes user requests into typed sub-tasks with assigned dependencies. | High | Sprint 1 | Eyad Ahmed Mahmoud Zaidan |
| **PB-03** | Web Client | **Interactive Console:** Reactive web UI for submitting software specifications and rendering task tables. | Medium | Sprint 1 | Mehin Baghirzade |
| **PB-04** | Dispatch Protocol | **Task Dispatch Pipeline (v1.0):** Route `TaskSpec` via `POST /api/v1/agents/{agent_id}/tasks` and store latency-measured `DispatchRecord`. | High | Sprint 2 | Berat Ermiş |
| **PB-05** | Worker Core | **Worker Ingestion & Fast Ack:** Implement `POST /agent/v1/tasks` with immediate `TaskAcknowledgement` and guardrails. | High | Sprint 2 | Team 7 |
| **PB-06** | Gateway Resilience | **Timeout & Network Error Traps:** Master handles timeouts (`504`) and unreachable nodes (`503`), saving failed attempts. | High | Sprint 2 | Team 7 |
| **PB-07** | Local Inference | **Asynchronous Ollama Execution:** Worker formats prompts and invokes local Ollama (`/api/generate`) asynchronously. | High | Sprint 2/3 | Team 7 |
| **PB-08** | Observability | **Audit & Result Querying:** Retrieve node telemetry via `GET /agent/v1/health` and output artifacts via `GET /agent/v1/tasks/{task_id}/result`. | Medium | Sprint 3 | Team 7 |
| **PB-09** | Orchestration | **Task Reassignment Engine:** Automatically re-assign failed tasks (`status: failed`) to available eligible backup agents. | Medium | Sprint 4 | Team 7 |
