"""The conditions a task runs under: the repo as committed, without its agent docs, or patched.

"Agent docs" are the files a coding-agent harness loads as instructions or configuration on its
own: AGENTS.md, CLAUDE.md, CONTEXT.md, project OpenCode config, skills and rule directories of
the common harnesses. Human docs (README, CONTRIBUTING, docs/) stay in every condition, so the
measured difference is the agent docs alone.

Matching follows the filesystem. On a case-insensitive filesystem (Windows, default macOS)
OpenCode's lookup for ``AGENTS.md`` also finds ``docs/.../agents.md``, so there those count as
agent docs too. Otherwise a ``without_docs`` run could still receive one as instructions.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from docdelta.checkout import isolated_git_env

WITH_DOCS = "with_docs"
WITHOUT_DOCS = "without_docs"
PATCHED = "patched"
CONDITIONS = (WITH_DOCS, WITHOUT_DOCS, PATCHED)
DEFAULT_CONDITIONS = (WITH_DOCS, WITHOUT_DOCS)

#: ``**/NAME`` matches NAME at any depth; ``DIR/**`` matches everything under DIR at the root;
#: ``**/DIR/**`` matches everything under DIR at any depth; anything else is an exact root path.
AGENT_DOC_GLOBS: tuple[str, ...] = (
    # Instruction files OpenCode, Codex, Claude Code, Gemini and others auto-load.
    "**/AGENTS.md",
    "**/AGENTS.override.md",
    "**/AGENT.md",
    "**/CLAUDE.md",
    "**/CLAUDE.local.md",
    "**/CONTEXT.md",
    "**/GEMINI.md",
    # Harness project config and extension directories (instructions, agents, skills, MCP).
    "opencode.json",
    "opencode.jsonc",
    "**/.opencode/**",
    "**/.agents/**",
    "**/.claude/**",
    ".mcp.json",
    # Editor and assistant rule files.
    ".cursorrules",
    ".cursor/rules/**",
    ".windsurfrules",
    ".windsurf/rules/**",
    ".clinerules",
    ".clinerules/**",
    ".roo/rules/**",
    ".kiro/steering/**",
    ".junie/guidelines.md",
    ".github/copilot-instructions.md",
    ".github/instructions/**",
    ".github/prompts/**",
    ".github/chatmodes/**",
)


class ConditionError(RuntimeError):
    """A condition could not be prepared as specified."""


def case_insensitive(root: Path) -> bool:
    """Whether *root*'s filesystem ignores case, probed with a throwaway file."""
    probe = root / ".docdelta-CaseProbe"
    try:
        probe.write_text("", encoding="utf-8")
        return (root / ".docdelta-caseprobe").exists()
    except OSError:
        return False
    finally:
        probe.unlink(missing_ok=True)


def _matches(rel: str, pattern: str) -> bool:
    if pattern.startswith("**/") and pattern.endswith("/**"):
        name = pattern[3:-3]
        return rel.startswith(name + "/") or f"/{name}/" in rel
    if pattern.startswith("**/"):
        name = pattern[3:]
        return rel == name or rel.endswith("/" + name)
    if pattern.endswith("/**"):
        return rel.startswith(pattern[:-3] + "/")
    return rel == pattern


def find_agent_docs(
    root: Path, extra_globs: tuple[str, ...] = (), fold_case: bool | None = None
) -> list[str]:
    """Repo-relative POSIX paths of the agent docs under *root*, sorted. ``.git`` is skipped.

    *fold_case* defaults to what *root*'s filesystem does.
    """
    fold = case_insensitive(root) if fold_case is None else fold_case
    patterns = tuple(p.casefold() if fold else p for p in (*AGENT_DOC_GLOBS, *extra_globs))
    found = []
    for path in root.rglob("*"):
        rel_parts = path.relative_to(root).parts
        if not rel_parts or rel_parts[0] == ".git" or not path.is_file():
            continue
        rel = "/".join(rel_parts)
        key = rel.casefold() if fold else rel
        if any(_matches(key, pattern) for pattern in patterns):
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
        env = isolated_git_env(ceiling=checkout.resolve().parent)
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
