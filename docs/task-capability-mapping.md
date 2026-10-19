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
2. The master looks up the type in this table: main capability is `coding`.
3. The selection algorithm picks, among agents that are `idle`, the one with the highest score in that capability. The second capability is used to break ties.

## 4. Decisions to confirm in Sprint Planning

- **One fixed table (this one), not model guesses.** The local model must not invent capability numbers for each task. A fixed table gives the same result every time and is easy to test.
- **Names and scale.** Capability names are `coding`, `reasoning`, `context`; the scale is 0 to 100 for agents and tasks alike.
- **Type names.** Use the names in `shared/schemas.py` (`testing`, `documentation`). The older `backend/` API and some documents still say `test` and `docs`; they should be changed to match.

## 5. Week 4

The table becomes a Python dictionary in the master (`TaskType` to main and second capability), so every subtask is labelled with its type and required capability. It is then connected to Mert's selection algorithm.
