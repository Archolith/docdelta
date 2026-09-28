<!--
DRAFT for huggingface/smolagents. Not posted. The owner reviews, edits and posts it personally.
Checked 2026-09-27:
- main == 227ef5e (the commit measured);
- no open issue about AGENTS.md;
- closed PRs #2520 (AI-authored AGENTS.md expansion, no issue, closed without comment) and #2295 (automated; the author withdrew it).
Suggested labels (maintainers set them): documentation, area:docs.
-->

**Title:** AGENTS.md predates the #2679 contribution rules; coding agents that follow it skip them

### What I observed

`AGENTS.md` (added in #1701, unchanged since) lists three style rules. The contribution rules added to
`CONTRIBUTING.md` in #2679 are "open an issue first" and "tell us if you used an AI assistant". When a coding
agent was asked how to contribute a change, it missed those rules more often when AGENTS.md was present than
when it was absent. The agent seems to treat AGENTS.md as the complete set of rules.

I asked an agent the same question about a small change to `LocalPythonExecutor`: which commands to run and
which project rules apply. I ran it 5 times in each setup, on fresh checkouts of `main` at `227ef5e`:

| AGENTS.md in the checkout | Runs naming all 4 relevant rules* |
|---|---|
| current file | 2 / 5 |
| no AGENTS.md | 5 / 5 |
| current file + one line pointing to CONTRIBUTING.md | 5 / 5 |

\*"Open an issue first", "disclose AI assistant use", "write unit tests", and "unsupported operations raise an
error" (from the secure code execution docs). All runs got the dev commands right (`pip install -e ".[dev]"`,
`make quality`, `make style`, `make test`). The difference is entirely in the two #2679 rules. With the current
file, AI-assistant disclosure was missed in 3 runs and "open an issue first" in 2.

### Proposal

Add one line to `AGENTS.md`:

```diff
 # Contributor Guidelines
 - Follow OOP principles
 - Be Pythonic: follow Python best practices and idiomatic patterns
 - Write unit tests for new functionality
+- Before contributing, read CONTRIBUTING.md and follow its process rules.
```

It keeps AGENTS.md short and leaves CONTRIBUTING.md as the single source, so the two can't drift apart again. I
can open the PR if you label this `status:accepted`. Or feel free to just make the change yourselves.

### Method and limits

- **Agent:** OpenCode 1.18.31 with `gpt-6-luna`, isolated from any global instructions. Each run used a fresh
  export of the commit, sealed as its own git repo, so removed files weren't in its history. The prompt was the same in every setup.
- **Grading:** an LLM judge. It gives credit only when it can quote the passage from the agent's answer.
- **Scope:** one question, one model, 5 runs per setup. That's a small sample; the 2/5 vs 5/5 gap is the only effect I'd claim.
- **What didn't work:**
  - A longer patch that restated the rules helped on this question but not on a held-out question about `SECURITY.md`.
  - A second pointer line to `SECURITY.md` didn't help that question either.
  - So I'm only proposing the CONTRIBUTING.md line.
- I can share the raw runs (prompts, transcripts, scores) if useful.

### Disclosure

I measured this with a small A/B tool I'm building for agent instruction files. An AI assistant helped run the
experiments and draft this issue. I've read the results and the diff and stand behind them.
