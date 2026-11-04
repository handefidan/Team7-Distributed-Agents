# Team 7 — Product Backlog and Definition of Done (DoD)

## 1. Project & Team Information
- **Project Name:** Distributed Autonomous Multi-Agent Software Development Platform
- **Team:** Team 7
- **Sprint Window:** Sprint 1, Sprint 2 & Sprint 3

### Team Members & Assigned Domains
| Full Name | Student ID | Primary Sprint 1 & 2 Responsibilities | Sprint 3 Responsibilities (Trello Aligned) |
| :--- | :---: | :--- | :--- |
| **Hande Reyyan Fidan** | 2304010611 | Product Backlog, DoD, SRS Architecture, Sprint 1 Demo & QA | Task Dependency Resolution (`master/dependency.py`), Prerequisites Execution Guardrails & Pytest Suite Expansion |
| **Berat Ermiş** | 2304010804 | Master Prototype, Task Decomposition, Register Agents & Models, Dispatch Contract | Gateway Endpoint Integration (`decompose_and_map`, `complete`), Lifecycle Pipeline & Ollama Setup |
| **Eyad Ahmed Mahmoud Zaidan** | 2404010370 | Web Interface Frontend & Register Agents Integration | Worker Ingestion Verification, Telemetry Polling & Live Performance Testing |
| **Nehir Tunç** | 2304010608 | Task Decomposition, Architecture Diagram, Network Isolation | Tailscale Mesh Network Audit & Multi-Node Routing Verification |
| **Mert Durdu** | 2304010805 | Requirements Document Finalization, Selection Algorithm | Fixed Task Capability Threshold Mapping (`master/classify.py`) & Capability Benchmarking Tests |

*Note: Former team member Mehin Baghirzade (Student ID: 2304010848) departed during Sprint 2 by her own volition, officially communicated to Teaching Assistant Res. Asst. Elif Bilge Şahin. The team syncs weekly to review sprint deliverables, Trello tasks, and technical alignment.*

### Team Cadence & Collaboration
- **Weekly Sync Meetings:** The team conducts synchronous coordination sessions **every Monday** to review completed tasks, align architectural contracts, and plan sprint deliverables.
- **Meeting Log Summary:**
  * **Week 1 (Monday):** Initial project scope alignment, basic task decomposition planning, and Trello card assignments.
  * **Week 2 (Monday):** Review of Master-agent prototype, dynamic agent registration, web interface demo, and finalization of Sprint 1 requirements.
  * **Week 3 (Monday):** Task Dispatch API v1.0 contract alignment, worker ingestion pipeline, and error code semantics (502/503/504).
  * **Week 4 (Monday):** Capability matrix scoring implementation, Tailscale IP isolation checks, and selection algorithm integration.
  * **Week 5 (Monday):** Sprint 3 Task dependency resolution (`T1, T2 -> UUID`), completion endpoint design, capability threshold mapping, and 39-test suite validation.

---

## 2. Definition of Done (DoD)
A backlog item or technical deliverable is considered **Done** only when all the following benchmarks are satisfied:
1. **Schema & Model Compliance:** All data models and payloads strictly adhere to `shared/schemas.py` (`AgentRegistration`, `TaskSpec`, `TaskMessage`, `TaskAcknowledgement`, `DispatchRecord`, `TaskResult`, `CapabilityScores`, `TaskType`).
2. **Deterministic Test Verification:** All 39 unit and integration test suites in `tests/` (`test_classify.py`, `test_decompose.py`, `test_dependency.py`, `test_dispatch.py`, `test_selection.py`) execute and pass 100% via `python -m pytest` using mock transports (`httpx.MockTransport`, `httpx.ASGITransport`) without requiring live external daemons.
3. **Dependency & Execution Guardrails:** Tasks with incomplete prerequisite dependencies are blocked from dispatch with HTTP `400 Bad Request`. Completing upstream tasks automatically unblocks downstream dependent tasks.
4. **Capability Mapping Enforcement:** Subtasks automatically inherit fixed capability thresholds (`coding`, `reasoning`, `context`) based on `master/classify.py` rules (Main threshold: 80, Secondary threshold: 60).
5. **Non-Blocking Operation:** Worker agents synchronously acknowledge tasks within SLA bounds (<= 200 ms), offloading execution to background workers via FastAPI `BackgroundTasks`.
6. **Idempotency & Resiliency:** Resending existing tasks returns cached acknowledgments (`duplicate: True`). Unreachable hosts (503), timeouts (504), full queues (rejection), and invalid candidates (404) are gracefully handled.
7. **Configuration & Security Standard:** Environment variables (`AGENT_ID`, `AGENT_MODEL`, `AGENT_SUPPORTED_TYPES`, `AGENT_MAX_QUEUE`, `OLLAMA_BASE_URL`, `MASTER_PORT`, `AGENT_PORT`, `TAILSCALE_IP`) function seamlessly with strict Tailscale private IP binding.
8. **Repository Cleanliness:** Changes are rebased and integrated into the `main` branch with semantic commit messages, and `README.md` / SRS documentation are fully updated.

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
| **[S2 \| W3]** | Worker Engine | **Background Ollama Execution & Health:** Asynchronous execution and queue telemetry. | Sprint 2 | Eyad, Team 7 |
| **[S2 \| W4]** | Orchestration | **Gateway Fault Resilience:** Handling 502/503/504 errors and failure recovery. | Sprint 2 | Berat, Team 7 |
| **[S2 \| W4]** | Routing Engine | **Dynamic Agent Selection Algorithm:** Capability matrix benchmarking & threshold filtering. | Sprint 2 | Mert |
| **[S2 \| W4]** | Security | **Tailscale Network Isolation:** Private IPv4 interface binding layer and fail-safe exit. | Sprint 2 | Nehir |
| **[S3 \| W5]** | Dependency | **Task Dependency Resolution & Guardrails:** Map `T1, T2` labels to runtime UUIDs & block dispatch if prerequisites incomplete. | Sprint 3 | Hande |
| **[S3 \| W5]** | Classifier | **Fixed Task Capability Threshold Mapping:** Static threshold lookup table (80/60) per task type (`master/classify.py`). | Sprint 3 | Mert |
| **[S3 \| W5]** | Gateway API | **Master Gateway Lifecycle Integration:** Endpoint routes for `decompose_and_map` and task completion. | Sprint 3 | Berat |
| **[S3 \| W6]** | Live Demo | **Tailnet Multi-Node Routing & Live Demo:** End-to-end integration testing across distributed worker nodes. | Sprint 3 | Team 7 |