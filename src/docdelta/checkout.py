"""Fresh, sealed checkouts of a repository at a pinned commit.

Each run gets its own checkout of the committed tree, prepared for its condition, then sealed
as a new one-commit repository. Sealing matters twice: agent harnesses walk up to the git root
looking for instruction files, so an unsealed checkout inside another repository would inherit
that repository's AGENTS.md; and a sealed history never contains the agent docs a
``without_docs`` run removed.

Export and seal run with the operator's git configuration switched off (no global or system
config, no hooks, no templates). Otherwise, a global ``core.hooksPath`` post-commit hook or an
``excludesFile`` could change what the agent sees. The export writes the committed tree with
``read-tree`` + ``checkout-index``, not ``git archive``, so ``export-ignore`` and
``export-subst`` attributes don't alter it.

Checkouts live in a neutral temp directory (:func:`neutral_checkout`). Their paths carry no
condition, task or run name the agent could read.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from docdelta.models import RepoPin

_SEAL_IDENTITY = (
    "-c", "user.name=docdelta",
    "-c", "user.email=docdelta@localhost",
    "-c", "core.autocrlf=false",
    # The sealed repo is a throwaway fixture owned by the tool; a user's signing setup must
    # not prompt or fail inside a benchmark run.
    "-c", "commit.gpgsign=false",
)
_ISOLATION: dict[str, str] = {}


class CheckoutError(RuntimeError):
    """The pinned commit could not be fetched or exported."""


def _isolation() -> dict[str, str]:
    """An empty global config file and an empty hooks directory, created once per process."""
    if not _ISOLATION:
        root = Path(tempfile.mkdtemp(prefix="docdelta-git-"))
        (root / "empty.gitconfig").write_text("", encoding="utf-8")
        (root / "hooks").mkdir()
        _ISOLATION["config"] = str(root / "empty.gitconfig")
        _ISOLATION["hooks"] = str(root / "hooks")
    return _ISOLATION


def isolated_git_env(ceiling: Path | None = None) -> dict[str, str]:
    """The environment without GIT_* overrides and without the operator's global/system config."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = _isolation()["config"]
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    if ceiling is not None:
        env["GIT_CEILING_DIRECTORIES"] = str(ceiling)
    return env


def _hooks_off() -> tuple[str, ...]:
    return ("-c", f"core.hooksPath={_isolation()['hooks']}")


def _git(args: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise CheckoutError(f"git {' '.join(args[:2])} failed: {exc.stderr.strip()}") from exc


def ensure_cache(pin: RepoPin, cache_root: Path) -> Path:
    """A bare clone of *pin.url* under *cache_root* that contains *pin.commit*."""
    cache = cache_root / f"{pin.name}.git"
    if not cache.exists():
        cache_root.mkdir(parents=True, exist_ok=True)
        _git(["clone", "--bare", "--quiet", pin.url, str(cache)])
    try:
        _git(["cat-file", "-e", f"{pin.commit}^{{commit}}"], cwd=cache)
    except CheckoutError:
        _git(["fetch", "--quiet", "origin", pin.commit], cwd=cache)
        _git(["cat-file", "-e", f"{pin.commit}^{{commit}}"], cwd=cache)
    return cache


def export_commit(pin: RepoPin, dest: Path, cache_root: Path) -> Path:
    """Write the committed tree of *pin.commit* into the new directory *dest*."""
    cache = ensure_cache(pin, cache_root).resolve()
    dest.mkdir(parents=True, exist_ok=False)
    index_dir = Path(tempfile.mkdtemp(prefix="docdelta-index-"))
    try:
        env = isolated_git_env()
        env["GIT_INDEX_FILE"] = str(index_dir / "index")
        base = ["-c", "core.autocrlf=false", f"--git-dir={cache}", f"--work-tree={dest.resolve()}"]
        _git([*base, "read-tree", pin.commit], env=env)
        _git([*base, "checkout-index", "--all", "--force"], env=env)
    finally:
        shutil.rmtree(index_dir, ignore_errors=True)
    return dest


def seal(checkout: Path) -> str:
    """Make *checkout* its own one-commit repository; returns the commit id."""
    env = isolated_git_env(ceiling=checkout.resolve().parent)
    _git(["init", "--quiet", "--template="], cwd=checkout, env=env)
    _git([*_SEAL_IDENTITY, *_hooks_off(), "add", "--all", "--force"], cwd=checkout, env=env)
    _git(
        [*_SEAL_IDENTITY, *_hooks_off(), "commit", "--quiet", "--no-verify", "--allow-empty",
         "-m", "docdelta checkout"],
        cwd=checkout, env=env,
    )
    return _git(["rev-parse", "HEAD"], cwd=checkout, env=env).strip()


@contextmanager
def neutral_checkout() -> Iterator[Path]:
    """A not-yet-created ``repo`` directory under a fresh random temp directory; removed on exit.

    The agent works here, so its paths reveal nothing about the condition, task or workdir,
    and nothing else docdelta writes sits beside it.
    """
    parent = Path(tempfile.mkdtemp(prefix="dd-"))
    try:
        yield parent / "repo"
    finally:
        remove_tree(parent)


def _make_writable(func, path, _exc) -> None:  # type: ignore[no-untyped-def]
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except FileNotFoundError:
        pass


def remove_tree(path: Path) -> None:
    """``shutil.rmtree`` that also removes git's read-only object files on Windows."""
    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_make_writable)
    else:  # pragma: no cover - 3.11 only
        shutil.rmtree(path, onerror=_make_writable)
