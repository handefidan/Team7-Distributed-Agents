# Task Types and Capability Mapping

**Sprint 2, Week 3** · **Author:** Nehir Tunç

## 1. Purpose

The master agent splits a requirement into subtasks and gives each one a **type**. To send a task to the right agent, the master also needs to know **which capability the task needs**. This table connects the two:

- Task types come from `TaskType` in `shared/schemas.py`.
- Capabilities are the three scores every agent has: `coding`, `reasoning`, `context` (each 0 to 100; see `docs/model-evaluation-criteria.md`).

## 2. Mapping

| Task type | Main capability | Second capability | Why |
|---|---|---|---|
| `requirements` | reasoning | context | Read the requirement text, find what is missing, and decide what to build. |
| `database` | reasoning | coding | Designing tables and relations needs logic; the SQL itself is small. |
| `backend` | coding | reasoning | Mostly writing API and server code. |
| `frontend` | coding | context | Mostly writing screen code; it must stay consistent with the rest of the pages. |
| `testing` | reasoning | coding | Choosing which cases to test needs thinking; the test code is simple. |
| `integration` | context | reasoning | Joins the work of several agents, so it must keep many files in mind. |
| `documentation` | context | reasoning | Reads the whole project and summarises it. |

## 3. How the master uses it

1. The decomposition gives a subtask a type, for example `backend`.
2. The classifier (`master/classify.py`) turns the type into required scores: the main capability needs at least **80**, the second at least **60**, the third **0**. For `backend` this is coding 80, reasoning 60, context 0. The values are put on the subtask as `required_capabilities`.
3. The selection algorithm (`master/selection.py`) keeps only the agents whose three scores all meet the required scores, and picks the one with the highest total score. If no agent qualifies, nothing is sent.
