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

- A condition changes only the checkout. The prompt, tools and agent config are identical, and the agent works in
  a neutral temp path (`dd-*/repo`) that names no condition, task or workdir.
- `without_docs` removes every file a harness auto-loads: instruction files, harness config, skill
  and rule dirs (`conditions.AGENT_DOC_GLOBS`). On a case-insensitive filesystem, matching folds case, because
  OpenCode's AGENTS.md lookup does too.
- Docs are removed before sealing. Export and seal run without the operator's global/system git config,
  hooks or templates, and export uses read-tree + checkout-index, not `git archive`.
- The OpenCode adapter isolates `XDG_*` and HOME/USERPROFILE (not the Claude Code flag, which would hide a repo's
  own CLAUDE.md in `with_docs`). It records injected instruction files per run, and a `without_docs` run
  with any injected file is marked `contaminated` and excluded from reports.
- A saved result is reused only if its `run_key` (docdelta version, agent, model, commit, condition, patch
  and prompt hashes) matches; otherwise `StaleResults`.
- Rate limits: by default (`--rate-limit stop`) the first sign of one ends the matrix, and nothing is retried. `--rate-limit wait`
  (opt-in, meant for free models) lets OpenCode's own retry and back-off run for up to `--rate-limit-run-wait` seconds per run. If a
  run still ends rate-limited, the matrix waits the `--rate-limit-backoff` delays (default 5/15/45 min) and retries it, then stops.
  Every wait is logged in `ratelimit.log`.
- Spend stops (`over_reserve`, `no_usage`, `no_cost`, which includes a reported cost of 0) end the matrix.
  A timeout is saved as stopped and the matrix continues. None of these runs is reused as finished. 429s are detected
  only in error events and error-level lines.
- Caps count the workdir's whole spend ledger (`spend.jsonl`), including stopped runs and earlier invocations.
- Keys are redacted from the logs and from `final_text`/`error`, including on interrupt.
- Reports use judged metrics only when every compared run was judged; judged verdicts must match the
  saved answer.
- Proposed commands never run on the host. **Not yet:** the agent itself still runs on the host with the
  operator's environment and default-allow bash/edit/webfetch (audit F9). Sandbox it before testing
  untrusted repos.
