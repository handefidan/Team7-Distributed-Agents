# Team 7 — LLM Model Evaluation & Capability Scoring Criteria

## 1. Overview & Objective
In the Distributed Autonomous Multi-Agent Software Development Platform, worker agents are registered with quantitative capability scores:
- **Coding (0–100):** Code generation accuracy, schema compliance, and syntax validity.
- **Reasoning (0–100):** Logical task decomposition, dependency resolution, and algorithmic problem-solving.
- **Context (0–100):** Token window size, long-prompt retention, and documentation processing.

These scores allow the Master Agent (`master/selection.py`) to deterministically route decomposed tasks (`requirements`, `database`, `backend`, `frontend`, `testing`, `integration`, `documentation`) to the most capable registered worker nodes based on fixed capability thresholds (`master/classify.py`).

---

## 2. Scoring Methodology & Benchmarks

### 2.1 Coding Score (Weight: 0–100)
Evaluates the model's ability to output syntactically valid code, respect Pydantic schemas, and pass unit tests.
- **Benchmark Source:** HumanEval (Python pass@1) & MultiPL-E benchmark suites.
- **Formulation:** `Coding Score = Normalized HumanEval (pass@1) %`
  - **90–100:** HumanEval pass@1 > 75%. Flawless syntax in Python/JavaScript, zero hallucination of library methods, strict JSON/schema output.
  - **75–89:** HumanEval pass@1 60–75%. Solid syntax, adheres to typing annotations, requires minor post-processing for complex logic.
  - **50–74:** HumanEval pass@1 40–59%. Suitable for boilerplate generation, occasional syntax errors or invalid imports.
  - **0–49:** HumanEval pass@1 < 40%. Unreliable syntax, invalid code blocks.

### 2.2 Reasoning Score (Weight: 0–100)
Evaluates logical decomposition, dependency graphing (e.g., recognizing that `backend` depends on `database`), and architectural decisions.
- **Benchmark Source:** GSM8K & MMLU / ARC-Challenge multi-step reasoning benchmarks.
- **Formulation:** `Reasoning Score = Average(GSM8K %, MMLU %)`
  - **90–100:** Benchmark score > 75%. Correctly identifies circular dependencies and orders task execution graphs without omissions.
  - **75–89:** Benchmark score 60–75%. Strong architectural mapping, resolves single-path dependencies correctly, minor edge-case oversights.
  - **50–74:** Benchmark score 45–59%. Understands basic sequential order (step 1 -> step 2), struggles with multi-agent parallel dependency graphs.
  - **0–49:** Benchmark score < 45%. Fails to map causal task relationships or generates conflicting dependencies.

### 2.3 Context Score (Weight: 0–100)
Evaluates effective context window size, instruction following across long prompts, and multi-file coherence.
- **Benchmark Source:** Effective native context window length and Needle-In-A-Haystack (NIAH) retrieval accuracy.
- **Formulation:** Normalized token window scale:
  - **90–100:** Effective context window >= 32k tokens with > 95% NIAH retrieval accuracy.
  - **75–89:** Context window between 8k and 16k tokens; maintains coherence across medium-length architecture specs.
  - **50–74:** Context window between 2k and 4k tokens; forgets early prompt instructions in multi-turn dispatches.
  - **0–49:** Context window < 2k tokens; high truncation rates.

---

## 3. Reference Model Scoring Matrix (Ollama Runtimes)

| Model Name | Primary Domain | Coding (0–100) | Reasoning (0–100) | Context (0–100) | Recommended Task Allocation |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **`qwen2.5-coder:1.5b`** | Code / Lightweight | **85** | **78** | **80** | Backend API, Test Generation, Decomposer |
| **`qwen2.5-coder:7b`** | Code / Specialized | **94** | **88** | **90** | Complex Backend, Schema Migrations |
| **`llama3.2:3b`** | General Purpose / Lightweight | **72** | **82** | **85** | Requirements, Testing, Architecture & Docs |
| **`llama3:8b`** | Reasoning / General | **78** | **86** | **85** | System Architecture, Task Decomposition |
| **`codellama:7b`** | Code Generation | **88** | **75** | **70** | Frontend UI Modals, Unit Tests |

---

## 4. Agent Dispatch Threshold Rules (`master/classify.py`)

Subtasks are automatically mapped to fixed capability thresholds (Main capability >= **80**, Secondary capability >= **60**, Unrequired capability = **0**). Candidate agents must meet or exceed **all** non-zero capability requirements to be eligible for selection:

| Task Type (`TaskType`) | Main Capability (>= 80) | Secondary Capability (>= 60) | Unrequired (0) |
| :--- | :--- | :--- | :--- |
| `requirements` | `reasoning` (80) | `context` (60) | `coding` (0) |
| `database` | `reasoning` (80) | `coding` (60) | `context` (0) |
| `backend` | `coding` (80) | `reasoning` (60) | `context` (0) |
| `frontend` | `coding` (80) | `context` (60) | `reasoning` (0) |
| `testing` | `reasoning` (80) | `coding` (60) | `context` (0) |
| `integration` | `context` (80) | `reasoning` (60) | `coding` (0) |
| `documentation` | `context` (80) | `reasoning` (60) | `coding` (0) |