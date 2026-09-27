"""The conditions a task runs under: the repo as committed, without its agent docs, or patched.

Only files an agent harness auto-loads as instructions count as agent docs. Human docs
(README, CONTRIBUTING, docs/) stay in every condition, so the measured difference is the
agent docs alone. Matching is case-sensitive: ``docs/agents.md`` in a project about agents
is a human page, not an instruction file.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

WITH_DOCS = "with_docs"
WITHOUT_DOCS = "without_docs"
PATCHED = "patched"
CONDITIONS = (WITH_DOCS, WITHOUT_DOCS, PATCHED)
DEFAULT_CONDITIONS = (WITH_DOCS, WITHOUT_DOCS)

#: ``**/NAME`` matches NAME at any depth; ``DIR/**`` matches everything under DIR.
AGENT_DOC_GLOBS: tuple[str, ...] = (
    "**/AGENTS.md",
    "**/AGENTS.override.md",
    "**/CLAUDE.md",
    "**/CLAUDE.local.md",
    "**/GEMINI.md",
    "**/gemini.md",
    ".cursorrules",
    ".cursor/rules/**",
    ".windsurfrules",
    ".windsurf/rules/**",
    ".clinerules",
    ".clinerules/**",
    ".github/copilot-instructions.md",
    ".github/instructions/**",
)


class ConditionError(RuntimeError):
    """A condition could not be prepared as specified."""


def _matches(rel: str, pattern: str) -> bool:
    if pattern.startswith("**/"):
        name = pattern[3:]
        return rel == name or rel.endswith("/" + name)
    if pattern.endswith("/**"):
        return rel.startswith(pattern[:-3] + "/")
    return rel == pattern


def find_agent_docs(root: Path, extra_globs: tuple[str, ...] = ()) -> list[str]:
    """Repo-relative POSIX paths of the agent docs under *root*, sorted. ``.git`` is skipped."""
    patterns = (*AGENT_DOC_GLOBS, *extra_globs)
    found = []
    for path in root.rglob("*"):
        rel_parts = path.relative_to(root).parts
        if ".git" in rel_parts or not path.is_file():
            continue
        rel = "/".join(rel_parts)
        if any(_matches(rel, pattern) for pattern in patterns):
            found.append(rel)
    return sorted(found)


def prepare(
    checkout: Path,
    condition: str,
    patch: Path | None = None,
    extra_globs: tuple[str, ...] = (),
) -> list[str]:
    """Turn a fresh export into *condition*; returns the paths removed or patched.

    Must run before the checkout is sealed: sealing afterwards means the agent's git
    history never contains the removed docs.
    """
    if condition == WITH_DOCS:
        return []
    if condition == WITHOUT_DOCS:
        removed = find_agent_docs(checkout, extra_globs)
        for rel in removed:
            (checkout / rel).unlink()
        return removed
    if condition == PATCHED:
        if patch is None or not patch.is_file():
            raise ConditionError(f"condition {PATCHED!r} needs a patch file, got {patch}")
        # The export is not a repository yet; the ceiling stops git from treating an enclosing
        # repository (the workdir may sit inside one) as the root the patch paths resolve against.
        env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(checkout.resolve().parent)}
        env.pop("GIT_DIR", None)
        env.pop("GIT_WORK_TREE", None)
        patch_path = str(patch.resolve())
        try:
            stat = subprocess.run(
                ["git", "apply", "--numstat", patch_path],
                cwd=checkout, env=env, check=True, capture_output=True, text=True,
            ).stdout
            subprocess.run(
                ["git", "apply", patch_path],
                cwd=checkout, env=env, check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise ConditionError(f"patch {patch.name} did not apply: {exc.stderr.strip()}") from exc
        return sorted(line.split("\t", 2)[2] for line in stat.splitlines() if line.count("\t") >= 2)
    raise ConditionError(f"unknown condition {condition!r}; expected one of {CONDITIONS}")
