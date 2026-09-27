"""Drafting task prompts and gold answers for a new repository. NOT IMPLEMENTED.

This is the hard part of the product: a check only scales if gold answers can be drafted
without a person writing each one. Intended approach, per task kind in
:data:`docdelta.models.TASK_KINDS`:

- ``commands_and_guardrails``: gold commands from executable sources (Makefile targets,
  ``pyproject``/``package.json`` scripts, CI workflow steps), cross-checked against what
  CONTRIBUTING/README prescribe; CI-only commands are marked, not served as "run this".
- ``docs_and_files``: gold files from the code graph around a chosen change site.
- ``decision`` / ``stale_doc``: gold from ADRs/decision records when the repo has them;
  otherwise the task kind is skipped rather than invented.

Drafts are written with ``reviewed: false``; the runner refuses them unless a person
approves (``reviewed: true``) or the operator passes ``--allow-unreviewed``.
"""

from __future__ import annotations

from pathlib import Path

from docdelta.models import Task


def draft_tasks(checkout: Path, repo: str) -> list[Task]:
    raise NotImplementedError("gold drafting is not built yet; write task JSON by hand")
