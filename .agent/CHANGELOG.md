# Changelog

## 2026-09-27 - Held-out tasks for the smolagents patch (provisional)

- `examples/tasks/smolagents/smolagents-h1-vuln-report.json` and `smolagents-h2-docs-preview.json`: drafted
  from SECURITY.md and docs/README.md, with neither touched by the patch. `reviewed: false`, pending owner review.
- 30 runs across 3 conditions × 5 repeats, judged ($0.155 for agents, $0.016 for the judge):
  - h1 answer score: patched 0.76, with docs 0.83, without docs 0.84. Patched runs missed a reporting rule in 3/5 runs.
  - h2: 1.00 in every condition. At ceiling, so it doesn't discriminate.
  - The patch's gain on t2 did not carry over to the held-out tasks.

## 2026-09-27 - Patched condition measured on smolagents

- `examples/patches/smolagents.patch`: AGENTS.md gains CONTRIBUTING.md's process rules and the
  dev commands. It was hand-drafted with knowledge of the t2 gold.
- smolagents t2, 5 judged repeats, $0.029 for agents plus $0.003 for the judge:
  - patched: 0.87 score, 144k median tokens, all 4 rules in 5/5 runs;
  - with docs: 0.63, 2/5;
  - without docs: 0.80, 5/5.
  - Key points: 3/5 patched vs 2/5 in both other conditions; the patch doesn't mention them, so treat this as noise.

## 2026-09-27 - LLM judge, token change in the badge, 5-repeat run

- `judge.py` (new, ported from beacon_eval and widened to guardrails): the evidence must be copied from
  the answer, one passage credits one item, results are cached per run in `judged.json`, and it stops
  at its dollar cap or on a 429.
- `report.py`: the answer score prefers judged metrics. Adds the median token change. The badge now
  reads `<score> score | <tokens> tokens | n/m tasks`.
- `runner.load_results` merges judged scores. `cli.py` gains a `judge` subcommand.
- Tests: 28 passing.
- smolagents t2 with gpt-6-luna, 5 repeats, judged ($0.078 for agents, $0.005 for the judge):
  - with docs: 0.63 score, 143k median tokens;
  - without docs: 0.80 score, 158k median tokens;
  - all 4 guardrails: 2/5 runs with docs vs 5/5 without (Fisher p ~ 0.17).

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
