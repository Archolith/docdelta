# AGENTS.md

## Project Instructions For Coding Agents

1. Before making changes, read the guidance files in `.agent/`.
2. Start with `.agent/README.md` for project workflow and conventions.
3. Use `.agent/data_models.md` for entity and schema expectations.
4. Use `.agent/architecture.md` for module map, run flow and safety invariants.
5. Check `.agent/workflows/` for commands and conventions before running anything.
6. Tests: `.venv/Scripts/python -m pytest -q tests/<file>` for what you touched.
7. Never run a paid matrix without `--cap-usd` or `--cap-tokens`; stop on any 429.
8. If code and `.agent` docs conflict, call it out explicitly and ask for clarification.
