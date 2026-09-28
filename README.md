# docdelta

**Status: early alpha (0.0.1).** Works end to end with OpenCode as the agent and an LLM judge.
Gold drafting, command execution and patch suggestion are not built yet.

docdelta checks whether a repository's agent instruction files (AGENTS.md, CLAUDE.md, Cursor,
Copilot, Cline and Windsurf rules) actually help a coding agent. It asks an agent the same
questions on fresh checkouts of a pinned commit **with** the files, **without** them, and
optionally with a **patched** version. Each answer is scored against a reviewed gold answer.
The output is a scorecard, a list of what the agent missed, and a shields.io badge:

```
agent docs | +0.12 score | -10% tokens | 2/3 tasks
```

Static "agent readiness" checks look at whether files exist. docdelta runs an agent and grades
what it gets right, so you can test an AGENTS.md change the way you would test code.

## How a run works

1. Export the pinned commit with `git archive` (no history, no remote).
2. Apply the condition:
   - `with_docs`: as committed;
   - `without_docs`: agent instruction files removed (human docs such as README and
     CONTRIBUTING stay);
   - `patched`: a patch applied to them.
3. Seal the checkout as its own one-commit git repository. The agent can't walk up into an
   enclosing repository, or find removed files in history.
4. Run the agent with an isolated config: only the model's provider, and none of the operator's
   global instructions, plugins or MCP servers. The prompt is the same in every condition, and
   the agent must end with a JSON answer.
5. Score deterministically (commands, files, citations, verdict). Then, optionally, have an LLM
   judge rule and key-point recall; it gives credit only when it can quote the answer.
6. Save `runs/<repo>/<task>/<condition>/r<n>/result.json` (plus `judged.json`).

Spend caps are checked before every run and a per-run reserve kills runaway runs. A rate limit
stops everything and is never retried. Saved results are reused, so an interrupted run resumes.

## Quick start

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"     # .venv/Scripts/ on Windows

# Free dry run with a deterministic fake agent (proves the plumbing only)
docdelta run --repos examples/repos.json --tasks examples/tasks --workdir work/dry --repeats 2

# Real run: OpenCode + a model, with a dollar cap
docdelta run --agent opencode --model openai/gpt-6-luna --env-file .env \
  --repos examples/repos.json --tasks examples/tasks --workdir work/real \
  --repeats 5 --cap-usd 1 --reserve-usd 0.10
docdelta judge  --workdir work/real --tasks examples/tasks --env-file .env --cap-usd 0.10
docdelta report --workdir work/real --repo smolagents
docdelta badge  --workdir work/real --repo smolagents --out badge.json
```

`.env` holds `OPENAI_API_KEY`. Keys go only to the agent process and are scrubbed from saved logs.

## Case study: smolagents (commit 227ef5e): a result that did not replicate

One question (`examples/tasks/smolagents/smolagents-t2-commands.json`) asks how to contribute a
small change to `LocalPythonExecutor` and which rules apply. The agent is gpt-6-luna through OpenCode
1.18.31, with 5 runs per condition. "Rules" means all 4 gold rules were named (LLM-judged).

**First measurement (docdelta 0.0.1).** The current AGENTS.md named all 4 rules in 2/5 runs; without
it, 5/5. It looked as if the three-line AGENTS.md made agents skip CONTRIBUTING.md's "open an
issue first" and "disclose AI use".

**An audit then found confounds in that harness:**
- the agent's working path contained the condition name (`.../without_docs/...`);
- OpenCode's case-insensitive AGENTS.md lookup injected a human docs page as instructions in some runs;
- a "one-line" pointer patch that was actually two lines.

**Re-measured in the fixed harness (docdelta 0.0.2):**

| AGENTS.md | All 4 rules named | Median tokens |
|---|---|---|
| current file | 5/5 | 129k |
| none | 5/5 | 285k |
| current + one line pointing to CONTRIBUTING.md | 5/5 | 139k |

The rules gap is gone. The first result was most likely noise or a harness artefact; this data can't
tell which. The one difference left is tokens: with an AGENTS.md present, runs used about half the
tokens. Token counts have been noisy across batches (an earlier batch showed −10%), though.

In 2 of the 5 `with_docs` runs, OpenCode injected `docs/source/en/reference/agents.md` as
instructions (Windows, case-insensitive lookup). That's what a Windows user of OpenCode would get, so
it is left in `with_docs` and recorded per run. It is removed from `without_docs`.

The lesson is the method: five runs and one question can produce a convincing, wrong story. Re-measure
after any harness change before acting on a result. Raw results live in local workdirs and are not
published.

Reproduce:

```bash
docdelta run --agent opencode --model openai/gpt-6-luna --env-file .env   --repos examples/repos.json --tasks examples/tasks --workdir work/v2   --task smolagents-t2-commands --conditions with_docs,without_docs,patched   --patch-dir examples/patches/one-line --repeats 5 --cap-usd 0.25 --reserve-usd 0.05
docdelta judge --workdir work/v2 --tasks examples/tasks --env-file .env --cap-usd 0.03
docdelta report --workdir work/v2 --repo smolagents
```

## Task files

Tasks live at `tasks/<repo>/<task_id>.json` and contain `prompt`, `kind`, a `gold` answer
(docs, files, commands, guardrails, points, verdict) and `gold_citations` (the path, lines and
quote behind each gold item). The runner refuses tasks with `"reviewed": false` unless you pass
`--allow-unreviewed`: a person should approve every gold answer.

## Limits

- The only agent adapter is OpenCode. Claude Code and Codex adapters are next.
- The judge is an LLM and varies a little between runs, so small differences are noise. The
  report greys out deltas within ±0.05 and refuses a badge below 2 repeats.
- Commands the agent proposes are not executed yet. When they are, it will be in a network-less
  container, never on the host.

## Licence

Apache-2.0. Scoring and the OpenCode isolation are ported from
[archolith-bench](https://github.com/Archolith/archolith-bench) (same author).
