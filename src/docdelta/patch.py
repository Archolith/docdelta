"""Suggesting an agent-docs patch from failed runs. NOT IMPLEMENTED.

Intended approach: collect the gold items that ``with_docs`` runs missed (commands,
guardrails, points), locate each in the repo's human docs or build files via its gold
citation, and propose AGENTS.md lines that state it with a source link. The result is a
unified diff; the ``patched`` condition then re-runs the matrix on it, so a suggested patch
ships with its own measured delta.
"""

from __future__ import annotations

from pathlib import Path

from docdelta.models import RunResult, Task


def suggest_patch(results: list[RunResult], tasks: list[Task], checkout: Path) -> str:
    raise NotImplementedError("patch suggestion is not built yet")
