# Code conventions

- Python 3.11+, `from __future__ import annotations`, dataclasses, type hints throughout.
- No runtime dependencies without a reason; stdlib first.
- Tests with pytest under `tests/`; use the `sample_repo` fixture (a local git repo) instead
  of the network. Run focused tests for what you touched: `.venv/Scripts/python -m pytest -q tests/<file>`.
- Never print or log provider keys; adapters read them from the environment only.
- Stop on HTTP 429: set `AgentRun.rate_limited` and let the matrix stop. Never retry.
- Paid runs need an explicit `--cap-usd` or `--cap-tokens`.
