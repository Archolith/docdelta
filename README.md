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

## Case study: smolagents (commit 227ef5e)

The task files are in `examples/tasks/smolagents/` and the patches in `examples/patches/`. One
question asks how to contribute a small change to `LocalPythonExecutor` and which rules apply.
Five runs per condition, gpt-6-luna through OpenCode 1.18.31:

| AGENTS.md | All 4 rules named | Median tokens |
|---|---|---|
| current file | 2/5 | 143k |
| none | 5/5 | 158k |
| current + two lines pointing to CONTRIBUTING.md and SECURITY.md | 5/5 | 105k |
| current + a longer section restating the rules | 5/5 | 144k |

With the current file, the agent missed "open an issue first" and "disclose AI assistant use" in
CONTRIBUTING.md. It seems to treat AGENTS.md as the complete rulebook.

**Correction (2026-09-27):** an earlier version of this table labelled the pointer row "one line".
The patch measured was two lines (`examples/patches/pointer/smolagents.patch`). A held-out question
about SECURITY.md was also run, but an audit found it confounded: on Windows, OpenCode's
case-insensitive AGENTS.md lookup injected `docs/.../agents.md` as instructions in some runs. That
result is withdrawn.

An audit also found that the agent could see its condition name in its working path. The harness is
being fixed, and these numbers will be re-measured, with a true one-line patch, in the fixed harness.

This is one question with one model and five runs. Treat it as a worked example of the method,
not a benchmark.

Reproduce it:

```bash
docdelta run ... --task smolagents-t2-commands --conditions with_docs,without_docs
docdelta run ... --workdir work/pointer --task smolagents-t2-commands \
  --conditions patched --patch-dir examples/patches/pointer
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
