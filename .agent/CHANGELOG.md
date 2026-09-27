# Changelog

## 2026-09-27 - OpenCode adapter and first real run

- `agents/opencode.py`: ported from archolith-bench beacon_eval 67d7fc9. Adds an isolated
  config home, streamed events, kills on 429, reserve or timeout, resume, and key redaction.
  Absolute cwd/PWD, because a relative workdir doubled the path inside OpenCode.
- `runner.py`, `budget.py`, `models.py`: `stopped.json` for over-reserve and no-usage runs, and
  `AccountingError`. Agents receive `log_dir`.
- `cli.py`: `--env-file` and `--config-source`. Paid agents require a cap; the default
  `--reserve-usd` is 0.10.
- Tests: 26 passing, including a fake-`opencode` suite.
- First real run: smolagents t2, gpt-6-luna, 2 repeats, $0.035. With docs: answer score 0.58,
  median 130k tokens. Without docs: 0.79 and 300k tokens (n=2, noisy).

## 2026-09-27 - Scaffold

- New project: package layout, CLI (`run`, `report`, `badge`, `list-agent-docs`).
- Built: models, conditions, checkout/seal, scoring (ported from archolith-bench 67d7fc9),
  budget, runner with resume and rate-limit stop, report and badge, fake agents.
- Stubs: OpenCode adapter, container command execution, gold drafting, patch suggestion.
- Tests: 20 passing; example smolagents task runs end to end with the fake agent.
