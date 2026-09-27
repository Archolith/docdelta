"""Fresh, sealed checkouts of a repository at a pinned commit.

Each run gets its own export (``git archive``, so no history and no remote), prepared for its
condition, then sealed as a new one-commit repository. Sealing matters twice: agent harnesses
walk up to the git root looking for instruction files, so an unsealed export inside another
repository would inherit that repository's AGENTS.md; and a sealed history never contains the
agent docs a ``without_docs`` run removed.
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
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


class CheckoutError(RuntimeError):
    """The pinned commit could not be fetched or exported."""


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
    """Extract the tree of *pin.commit* into the new directory *dest*."""
    cache = ensure_cache(pin, cache_root)
    try:
        archive = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "archive", "--format=tar", pin.commit],
            cwd=cache, check=True, capture_output=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise CheckoutError(f"git archive {pin.commit} failed: {exc.stderr!r}") from exc
    dest.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")
    return dest


def seal(checkout: Path) -> str:
    """Make *checkout* its own one-commit repository; returns the commit id."""
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(checkout.resolve().parent)}
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(name, None)
    _git(["init", "--quiet"], cwd=checkout, env=env)
    _git([*_SEAL_IDENTITY, "add", "--all"], cwd=checkout, env=env)
    _git(
        [*_SEAL_IDENTITY, "commit", "--quiet", "--no-verify", "--allow-empty", "-m", "docdelta checkout"],
        cwd=checkout, env=env,
    )
    return _git(["rev-parse", "HEAD"], cwd=checkout, env=env).strip()


def _make_writable(func, path, _exc) -> None:  # type: ignore[no-untyped-def]
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_tree(path: Path) -> None:
    """``shutil.rmtree`` that also removes git's read-only object files on Windows."""
    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_make_writable)
    else:  # pragma: no cover - 3.11 only
        shutil.rmtree(path, onerror=_make_writable)
