# Demo Scenario (Sprint 3 and 4)

Owner: Nehir Tunç · Flow: [api/message-flow.md](api/message-flow.md) · Failure rules: [error-recovery.md](error-recovery.md)

## What we show

One requirement becomes 5 dependent tasks. The master sends them **in dependency order** to **4 agents**
(each on its own Ollama model), collects the results by itself, and passes each result to the next task.
Then one agent is made to fail, and the master moves the task to another agent.

- **Requirement:** `Library system: add books, register members, and let members borrow and return books.`
- **Result shown:** `GET /api/v1/plan/tasks` – for every task the status, the agent that did it, its output and the attempt history.

## Agents

Scores are demo values chosen so that every agent gets work and one has a fallback; replace them with the team's real
scores from `backend/agents.json` if you prefer. `agent_id` may only contain letters, digits, `-` and `_`.

| agent_id | Port | Model | coding | reasoning | context | Gets |
|---|---|---|---|---|---|---|
| `agent-coder` | 8001 | `qwen3:4b` | 90 | 65 | 60 | T2 backend, T3 frontend |
| `agent-reasoner` | 8002 | `gemma3:4b` | 65 | 90 | 75 | T1 database, T4 testing |
| `agent-writer` | 8003 | `llama3.2:3b` | 60 | 70 | 90 | T5 documentation |
| `agent-backup` | 8004 | `phi4-mini` | 80 | 60 | 60 | only if `agent-coder` fails (backend, frontend) |

## The plan (the master's built-in decomposition, used when its own model is not available)

| Task | Type | Depends on | Needs (coding / reasoning / context) |
|---|---|---|---|
| T1 Database Schema Design | database | – | 60 / 80 / 0 |
| T2 Backend REST APIs | backend | T1 | 80 / 60 / 0 |
| T3 Frontend UI | frontend | T2 | 80 / 0 / 60 |
| T4 Automated Tests | testing | T2, T3 | 60 / 80 / 0 |
| T5 Documentation | documentation | T2 | 0 / 60 / 80 |

Order enforced by the master: T1 → T2 → (T3 and T5 together) → T4. With a real model decomposing the
requirement the list can differ; the checks stay the same.

## Setup (single computer)

Needs: `pip install -r requirements.txt`, Ollama running, models pulled
(`ollama pull qwen3:4b`, `gemma3:4b`, `llama3.2:3b`, `phi4-mini`). Use 5 PowerShell windows
(Linux/macOS: `export NAME=value`).

```powershell
# every window
$env:TAILSCALE_IP="127.0.0.1"          # one computer: no Tailscale needed

# windows 1-4: one agent each
$env:AGENT_ID="agent-coder";    $env:AGENT_MODEL="qwen3:4b";    $env:AGENT_PORT="8001"; python -m agent.main
$env:AGENT_ID="agent-reasoner"; $env:AGENT_MODEL="gemma3:4b";   $env:AGENT_PORT="8002"; python -m agent.main
$env:AGENT_ID="agent-writer";   $env:AGENT_MODEL="llama3.2:3b"; $env:AGENT_PORT="8003"; python -m agent.main
$env:AGENT_ID="agent-backup";   $env:AGENT_MODEL="phi4-mini";   $env:AGENT_PORT="8004"; python -m agent.main

# window 5: master (start with no agents from agents.json, we register our own)
$env:MASTER_AGENTS_FILE="none"
$env:TASK_RESULT_TIMEOUT_SECONDS="600"   # small models on a laptop can be slow
python -m master.main
```

Several computers: every person starts one agent with their own `TAILSCALE_IP` (`tailscale ip -4`),
and the master registers it with `base_url = http://<their-tailscale-ip>:<port>`.

## Steps (Swagger: `http://127.0.0.1:8000/docs`)

Restart the master before the demo so that only one plan exists.

1. **Register the agents** – `POST /api/v1/agents`, four times:
   ```json
   {"agent_id":"agent-coder","base_url":"http://127.0.0.1:8001","model":"qwen3:4b","capabilities":{"coding":90,"reasoning":65,"context":60}}
   {"agent_id":"agent-reasoner","base_url":"http://127.0.0.1:8002","model":"gemma3:4b","capabilities":{"coding":65,"reasoning":90,"context":75}}
   {"agent_id":"agent-writer","base_url":"http://127.0.0.1:8003","model":"llama3.2:3b","capabilities":{"coding":60,"reasoning":70,"context":90}}
   {"agent_id":"agent-backup","base_url":"http://127.0.0.1:8004","model":"phi4-mini","capabilities":{"coding":80,"reasoning":60,"context":60}}
   ```
   Expect `201` each.
2. **Decompose** – `POST /api/v1/requirements/decompose_and_map` with the requirement. Expect 5 tasks: T1 `pending`, the rest `blocked`.
3. **Show the guard** – copy the `task_uuid` of T2 into `GET /api/v1/plan/tasks/{task_id}/spec`, paste the JSON into
   `POST /api/v1/tasks/dispatch`. Expect `400 … prerequisite dependencies are not completed`.
4. **Run the plan** – `POST /api/v1/plan/run` (leave `wait` as `false`).
5. **Watch it** – refresh `GET /api/v1/plan/tasks`: T1 goes `dispatched` → `completed`, then T2 starts, then T3 and T5 together, then T4.
6. **Show the result** – when `GET /api/v1/plan/run` says `finished: true`, open `GET /api/v1/plan/tasks`: every task is `completed` with
   its `output` and the agent in `attempts`. Open T2: its context contained T1's output (visible in the agent window or via `GET /api/v1/tasks`).

### Failure part (about 1 minute)

Stop `agent-coder`'s Ollama connection by starting that agent with a wrong address
(`$env:OLLAMA_BASE_URL="http://127.0.0.1:9"`), restart the master, repeat steps 1, 2 and 4.
`agent-coder` accepts T2 but its model fails, so T2's attempts show
`agent-coder → agent_failed`, then `agent-backup → completed`. The plan still finishes. Explain: *"the master tried
another suitable agent; after 3 failed tries or when no agent is left, the task fails and the tasks that depend on it stay blocked."*

## Rehearsal

Run on one machine with 4 real agent processes and the real master; only the Ollama server was a stand-in that
answers `FAKE-MODEL[<model>] result for: <task title>` (the sandbox has no Ollama):

| Check | Result |
|---|---|
| T2 sent before T1 is completed | `400`, nothing sent |
| Whole plan with `POST /api/v1/plan/run?wait=true` | `completed: T1..T5`, about 1 second |
| Agent per task | T1 `agent-reasoner`, T2 `agent-coder`, T3 `agent-coder`, T4 `agent-reasoner`, T5 `agent-writer` |
| `agent-coder`'s model unreachable | T2 and T3: `agent-coder → agent_failed`, `agent-backup → completed`; plan completed |
| Automated tests (`python -m pytest -q`) | all pass |

**Still to rehearse on a computer with real Ollama and the team's agents:** real model outputs and speed (a small
model can take 10 to 60 seconds per task, so the full plan can take several minutes; set `TASK_RESULT_TIMEOUT_SECONDS`
high enough) and the Tailscale addresses.

## If something goes wrong in the demo

| Problem | What to do |
|---|---|
| Plan runs slowly | Show `running` in `GET /plan/tasks`, explain that agents work in the background; use the smaller model |
| A task shows `failed` with `no_agent` | The agent list is wrong: check `GET /api/v1/agents` (scores, or the agent was not registered) |
| A task shows `failed` with `busy` | The agent is off or very slow: check its window and `http://<agent>/agent/v1/health` |
| Plan stuck | `GET /api/v1/plan/run`; if `finished` is `false`, an agent is still working; wait or restart the master |
| Run again after fixing | `POST /api/v1/plan/run` again: failed tasks are retried, completed tasks are kept |
