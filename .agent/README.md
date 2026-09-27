# docdelta .agent

A/B tests a repository's agent docs by running a coding agent with and without them.

| File | Purpose |
|---|---|
| `architecture.md` | Module map, run flow, what is built vs stubbed |
| `data_models.md` | Task, Gold, RepoPin, RunResult, results layout |
| `CHANGELOG.md` | Changes, newest first |
| `workflows/code_conventions.md` | Style, tests, safety rules |
| `workflows/commands.md` | CLI reference |

## Maintenance rules

- Update `data_models.md` when any dataclass or file layout changes.
- Update `architecture.md` when adding modules, adapters or integrations.
- Update the relevant workflow file when operational behavior changes.
- Add a `CHANGELOG.md` entry at the end of every session with meaningful changes
  (`## YYYY-MM-DD - <short description>`, bullets per file). Only this project's changes.
- Conventional commits (`feat:`, `fix:`, `refactor:`, `chore:`, `docs:`); stage files by path.
