# docdelta

**Status: scaffold (0.0.1).** The pipeline runs end to end with a free fake agent; the real
agent adapter, gold drafting, command execution and patch suggestion are stubs.

docdelta checks whether a repository's agent docs (AGENTS.md, CLAUDE.md, Cursor/Copilot/
Cline rules) actually help a coding agent. It runs the same tasks on fresh checkouts of a
pinned commit, **with** and **without** those files, scores each answer against a reviewed
gold answer, and reports the difference -- as a scorecard and a shields.io badge.

```
agent docs | +0.18 vs none | 4/5 tasks
```

## Why

Every agent harness now reads an instruction file, but nobody measures whether a given
repo's file helps. Static "agent readiness" scores check that files exist. docdelta runs an
agent and grades what it gets right.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # Windows; bin/ on POSIX
docdelta run --repos examples/repos.json --tasks examples/tasks --workdir work/example --repeats 2
docdelta report --workdir work/example --repo smolagents
docdelta badge  --workdir work/example --repo smolagents --out badge.json
docdelta list-agent-docs path/to/checkout
```

The default agent, `doc-reader`, is a deterministic fake that only reads the checkout's agent
docs and README. It costs nothing and proves the plumbing, not a result.

## How a run works

1. Export the pinned commit with `git archive` (no history, no remote).
2. Apply the condition: `with_docs` (as committed), `without_docs` (agent docs removed),
   `patched` (a suggested AGENTS.md patch applied).
3. Seal the checkout as its own one-commit git repository, so the agent cannot walk up into
   an enclosing repository or find removed docs in history.
4. Send the same prompt in every condition; the agent must end with a JSON answer.
5. Score deterministically (commands, guardrails, key points, verdict, citations) and save
   `runs/<repo>/<task>/<condition>/r<n>/result.json`.

Budget caps are checked before every run; a rate limit stops the matrix and is never retried.
Saved results are reused, so an interrupted matrix resumes.

## Task files

Tasks use the archolith-bench `beacon_eval` format: `tasks/<repo>/<task_id>.json` with
`prompt`, `kind`, `gold` and `gold_citations`. The runner refuses tasks with
`"reviewed": false` unless `--allow-unreviewed` is passed.

## Roadmap

See `.agent/architecture.md` (stubs and what fills them) and the workspace design note.

## Licence

Apache-2.0. Scoring is ported from archolith-bench (same author).
