from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from docdelta.models import RepoPin


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c", "user.name=test",
            "-c", "user.email=test@localhost",
            "-c", "commit.gpgsign=false",
            "-c", "core.autocrlf=false",
            *args,
        ],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def sample_repo(tmp_path: Path) -> RepoPin:
    """A tiny upstream repository with an AGENTS.md, a nested one, and human docs."""
    root = tmp_path / "upstream"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "AGENTS.md").write_text(
        "# Agents\n\n- Run `make test` before every commit.\n- Never edit generated files.\n",
        encoding="utf-8",
    )
    (root / "src" / "AGENTS.md").write_text("- Keep modules small.\n", encoding="utf-8")
    (root / "README.md").write_text("# Sample\n\nA sample project.\n", encoding="utf-8")
    (root / "docs" / "agents.md").write_text("How the product's agents work.\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    _git(root, "init", "--quiet")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "--no-verify", "-m", "init")
    return RepoPin(name="sample", url=str(root), commit=_git(root, "rev-parse", "HEAD"))
