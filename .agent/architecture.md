# Architecture

Python 3.11+, stdlib only at runtime, pytest for tests. Package in `src/docdelta/`.

## Run flow

`cli.run` -> `runner.run_matrix` -> per task, repeat, condition: `runner.run_one`:
`checkout.export_commit` (bare cache clone + `git archive`) -> `conditions.prepare` ->
`checkout.seal` -> `agents.<adapter>.run` -> `scoring.extract_answer` + `scoring.score` ->
optional `execution` checks -> `result.json`. `report` builds the scorecard and badge from
saved results only.

## Modules

| Module | State | Role |
|---|---|---|
| `models.py` | built | RepoPin, Gold, Task, RunResult; beacon_eval-compatible task JSON |
| `conditions.py` | built | Agent-doc globs; with_docs / without_docs / patched |
| `checkout.py` | built | Cache clone, export, seal, Windows-safe removal |
| `scoring.py` | built (ported) | Deterministic scoring from archolith-bench 67d7fc9 |
| `budget.py` | built | Token and dollar caps; RateLimited |
| `runner.py` | built | Matrix, resume, rate-limit stop, unreviewed-gold guard |
| `report.py` | built | Answer score (judged metrics preferred), token change, scorecard, shields endpoint badge |
| `judge.py` | built (ported) | LLM judge for guardrails and key points; grounded evidence, per-run cache, $ cap, 429 stop |
| `cli.py` | built | run, judge, report, badge, list-agent-docs |
| `agents/fake.py` | built | ScriptedAgent, DocReadingAgent (free, deterministic) |
| `agents/opencode.py` | built (ported) | Isolated config home, streamed events, 429/reserve/timeout kill, resume, key redaction |
| `execution.py` | **stub** (NoExecution only) | Run proposed commands in a network-less container |
| `gold.py` | **stub** | Draft tasks and gold from build files, CI and docs |
| `patch.py` | **stub** | Suggest an AGENTS.md patch from missed gold items |

## Safety invariants

- A condition changes only the checkout; prompt, tools and agent config are identical.
- Docs are removed before sealing, so the agent's history never contains them.
- Adapters must isolate the agent from the operator's global instruction files; otherwise
  `without_docs` is contaminated.
- Proposed commands never run on the host.
- Rate limits and spend stops end the matrix; such runs are saved as `rate_limited.json` or
  `stopped.json` and never reused as finished runs.
