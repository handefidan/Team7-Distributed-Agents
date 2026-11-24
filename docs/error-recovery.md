# Error Recovery Rules (Sprint 4)

Owner: Nehir Tunç · Code: [`master/orchestrator.py`](../master/orchestrator.py) · Tests: [`tests/test_orchestrator.py`](../tests/test_orchestrator.py)

When the master runs a plan (`POST /api/v1/plan/run`), things can go wrong: an agent is off, busy,
rejects the task, or its model fails. These rules decide what the master does. They are applied per task.

## Rules

| # | Situation | What the master does | Attempt recorded as |
|---|---|---|---|
| 1 | A task depends on a task that is not completed | Does not send it. It stays `blocked` | – |
| 2 | No registered agent meets the task's required scores | Task `failed` at once. Retrying cannot help | `no_agent` |
| 3 | Every suitable agent is busy or offline | Waits (every 2 s, up to 120 s) and checks again. Waiting is **not** a failed attempt. After 120 s the task fails | `busy` |
| 4 | The agent rejects the task (wrong type, queue full) | Excludes that agent, sends to the next best | `rejected` |
| 5 | The agent cannot be reached or does not answer in time (503/504/502) | Excludes that agent, sends to the next best | `unreachable` |
| 6 | The agent accepted, but its model failed (`result.status = failed`) | Excludes that agent, sends to the next best | `agent_failed` |
| 7 | The agent accepted, but no result after 300 s | Excludes that agent, sends to the next best | `timeout` |
| 8 | The agent stops answering while the task runs (3 result polls in a row fail) | Excludes that agent, sends to the next best | `unreachable` |
| 9 | 3 attempts were used (rules 4 to 8) | Task `failed` | – |
| 10 | No other suitable agent is left to try | Task `failed` | `no_agent` |
| 11 | A task fails | Tasks that depend on it **stay blocked** and are never sent. The run reports them as `blocked` | – |
| 12 | The decomposition has a dependency cycle | The plan is rejected (`422`), nothing is created | – |

An agent that failed a task is never asked for **the same task** again during that run, but it is still
used for other tasks.

## Run again after a failure

`POST /api/v1/plan/run` again: failed tasks get a fresh start (attempt history is kept), completed tasks are
not run again. An agent accepts a task id it has already **failed** or **rejected** (completed ones are still
answered as duplicates), so a fixed model or a freed queue is enough.

## Settings (environment variables of the master)

| Variable | Default | Meaning |
|---|---|---|
| `MAX_TASK_ATTEMPTS` | 3 | Tries per task, each on a different agent |
| `BUSY_WAIT_SECONDS` | 2 | Pause between checks when all suitable agents are busy |
| `MAX_BUSY_WAIT_SECONDS` | 120 | Total waiting time for a free agent |
| `RESULT_POLL_INTERVAL_SECONDS` | 1 | How often the agent's result is asked for |
| `TASK_RESULT_TIMEOUT_SECONDS` | 300 | Time an agent gets to finish one task (small models are slow: raise it if needed) |

## How to see what happened

`GET /api/v1/plan/tasks` returns every task with `status`, `output` and `attempts`:

```json
{
  "label": "T1", "status": "completed", "output": "…",
  "attempts": [
    {"agent_id": "agent-reasoner", "outcome": "agent_failed", "detail": "ollama call failed: …"},
    {"agent_id": "agent-coder",    "outcome": "completed",    "detail": null}
  ]
}
```

`GET /api/v1/plan/run` shows the summary: `completed`, `failed`, `blocked` task labels.

## Not covered yet

- The plan and its attempts are kept in the master's memory; restarting the master loses them.
