# Data models

All in `src/docdelta/models.py`.

- **RepoPin** `name, url, commit`. `url` is anything `git clone` accepts.
- **Gold** `docs, files, acceptable_files, commands, guardrails, verdict, points, risky,
  evidence, allowed_flags`. Guardrails and points are one wording or a tuple of accepted
  wordings.
- **Task** `repo, task_id, kind, prompt, gold, reviewed`. Kinds: `TASK_KINDS`.
- **RunResult** `repo, task_id, condition, repeat, answer, final_text, input_tokens,
  output_tokens, seconds, cost_usd, error, scores, changed_docs, command_checks, agent,
  sealed_commit, rate_limited`.

## Files

- `repos.json`: `{"repos": [RepoPin...]}`
- `tasks/<repo>/<task_id>.json`: task plus `gold` and `gold_citations`
  (`{item, path, line_start, line_end, quote}`), beacon_eval format.
- `<workdir>/cache/<repo>.git`: bare clone.
- `<workdir>/runs/<repo>/<task>/<condition>/r<n>/`: `prompt.txt`, `result.json` or
  `rate_limited.json` or `stopped.json`, `judged.json` (judge verdicts and
  `guardrail_recall_judged` / `point_recall_judged`), `checkout/` only with `--keep-checkouts`.

## Conditions

`with_docs`, `without_docs`, `patched` (`<patch-dir>/<repo>.patch`).
