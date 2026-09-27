from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from docdelta.checkout import export_commit, seal
from docdelta.conditions import (
    PATCHED,
    WITH_DOCS,
    WITHOUT_DOCS,
    ConditionError,
    find_agent_docs,
    prepare,
)
from docdelta.models import RepoPin


def _export(pin: RepoPin, tmp_path: Path) -> Path:
    return export_commit(pin, tmp_path / "checkout", tmp_path / "cache")


def test_finds_harness_files_but_not_human_docs(tmp_path: Path) -> None:
    for rel in (
        "AGENTS.md", "pkg/AGENTS.md", "CLAUDE.md", ".cursor/rules/style.mdc",
        ".github/copilot-instructions.md", ".clinerules", "README.md", "docs/agents.md",
        "CONTRIBUTING.md", ".git/AGENTS.md",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    assert find_agent_docs(tmp_path) == [
        ".clinerules", ".cursor/rules/style.mdc", ".github/copilot-instructions.md",
        "AGENTS.md", "CLAUDE.md", "pkg/AGENTS.md",
    ]


def test_without_docs_removes_them_and_sealed_history_never_has_them(
    sample_repo: RepoPin, tmp_path: Path
) -> None:
    checkout = _export(sample_repo, tmp_path)
    removed = prepare(checkout, WITHOUT_DOCS)
    seal(checkout)
    assert removed == ["AGENTS.md", "src/AGENTS.md"]
    assert (checkout / "README.md").is_file() and (checkout / "docs/agents.md").is_file()
    tracked = subprocess.run(
        ["git", "log", "--all", "--name-only", "--format="],
        cwd=checkout, check=True, capture_output=True, text=True,
    ).stdout.split()
    assert "AGENTS.md" not in tracked and "src/AGENTS.md" not in tracked


def test_with_docs_changes_nothing(sample_repo: RepoPin, tmp_path: Path) -> None:
    checkout = _export(sample_repo, tmp_path)
    assert prepare(checkout, WITH_DOCS) == []
    assert (checkout / "AGENTS.md").is_file()


def test_patched_applies_patch(sample_repo: RepoPin, tmp_path: Path) -> None:
    checkout = _export(sample_repo, tmp_path)
    patch = tmp_path / "sample.patch"
    patch.write_text(
        "--- a/AGENTS.md\n+++ b/AGENTS.md\n@@ -3,2 +3,3 @@\n"
        " - Run `make test` before every commit.\n - Never edit generated files.\n"
        "+- Lint with `ruff check .`.\n",
        encoding="utf-8",
    )
    assert prepare(checkout, PATCHED, patch=patch) == ["AGENTS.md"]
    assert "ruff check ." in (checkout / "AGENTS.md").read_text(encoding="utf-8")


def test_patched_without_patch_fails(sample_repo: RepoPin, tmp_path: Path) -> None:
    checkout = _export(sample_repo, tmp_path)
    with pytest.raises(ConditionError):
        prepare(checkout, PATCHED)
