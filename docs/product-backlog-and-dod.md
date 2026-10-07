# Team 7 — Product Backlog and Definition of Done (DoD)

## 1. Project & Team Information
* **Project Name:** Distributed Autonomous Multi-Agent Software Development Platform
* **Team:** Team 7
* **Sprint Window:** Sprint 1 & Sprint 2

### Team Members & Assigned Domains
| Full Name | Student ID | Primary Sprint 1 & 2 Responsibilities (Trello Aligned) |
| :--- | :---: | :--- |
| **Hande Reyyan Fidan** | 2304010611 | Product Backlog, Definition of Done, Requirements Specification, Sprint 1 Demo & Review |
| **Berat Ermiş** | 2304010804 | Master-Agent Prototype, Task Decomposition, Register Agents & Models, Dispatch API Contract |
| **Eyad Ahmed Mahmoud Zaidan** | 2404010370 | Web Interface Frontend & Register Agents and Models Integration |
| **Mehin Baghirzade** | 2304010848 | Web Interface Development & Client Integration |
| **Nehir Tunç** | 2304010608 | Basic Task Decomposition, Architecture Diagram & Repository Code Structure |
| **Mert Durdu** | 2304010805 | Requirements Document Finalization & Verification |

### Team Cadence & Collaboration
* **Weekly Sync Meetings:** The team conducts synchronous coordination sessions **every Monday** to review completed tasks, align architectural contracts, and plan sprint deliverables.
* **Meeting Log Summary:**
  * **Week 1 (Monday):** Initial project scope alignment, basic task decomposition planning, and Trello card assignments.
  * **Week 2 (Monday):** Review of Master-agent prototype, dynamic agent registration, web interface demo, and finalization of Sprint 1 requirements.

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

## 3. Product & Sprint Backlog (Mapped to Trello)

| Task / Item ID | Component | Description & User Story | Sprint | Assignee(s) |
| :---: | :--- | :--- | :---: | :--- |
| **[S1 \| W1]** | Backlog & DoD | **Product Backlog and Definition of Done:** Establish baseline backlog and DoD criteria. | Sprint 1 | Hande |
| **[S1 \| W1]** | Core Logic | **Basic Task Decomposition:** Initial algorithmic decomposition of system requirements. | Sprint 1 | Nehir |
| **[S1 \| W1-2]** | Specification | **Requirements Document Draft:** Authoring functional and non-functional requirements. | Sprint 1 | Hande |
| **[S1 \| W2]** | Client UI | **Web Interface:** Interactive frontend console for requirement input and task review. | Sprint 1 | Mehin, Eyad |
| **[S1 \| W2]** | Master Hub | **Master-Agent Prototype & Task Decomposition:** Core FastAPI master engine for decomposition. | Sprint 1 | Berat |
| **[S1 \| W2]** | Registry | **Register Agents and Models:** Endpoint and payload specifications for worker agent registration. | Sprint 1 | Eyad, Berat |
| **[S1 \| W2]** | Specification | **Finalize Requirements Document:** Verification and final polish of the SRS document. | Sprint 1 | Hande, Mert |
| **[S1 \| W2]** | Architecture | **Architecture Diagram & Code on GitHub:** System architecture modeling and repository layout. | Sprint 1 | Nehir |
| **[S1 \| W2]** | Review & Demo | **Sprint 1 Demo, Review and Report:** Comprehensive review, demo preparation, and summary report. | Sprint 1 | Hande, Nehir |
| **[S2 \| W3]** | Dispatch Protocol | **Task Dispatch API Contract (v1.0):** Immediate synchronous acknowledgment and dispatching. | Sprint 2 | Berat |
| **[S2 \| W3]** | Worker Engine | **Background Ollama Execution & Health:** Asynchronous execution and queue telemetry. | Sprint 2 | Team 7 |
| **[S2 \| W4]** | Orchestration | **Gateway Fault Resilience:** Handling 502/503/504 errors and failure recovery. | Sprint 2 | Team 7 |
