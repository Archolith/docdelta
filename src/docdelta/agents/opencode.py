"""OpenCode as the agent under test.

Ported from archolith-bench ``beacon_eval`` (origin/master 67d7fc9: ``isolation.py`` and
``runner.stream_opencode``), trimmed to what docdelta needs: the only difference between
conditions is the checkout, so there are no MCP servers and OpenCode always runs in the
checkout, where it auto-loads the checkout's AGENTS.md (with_docs) or finds none
(without_docs).

Isolation: each run gets a fresh ``XDG_CONFIG_HOME`` whose ``opencode/opencode.json`` holds
only ``$schema``, ``model`` and that model's provider, so the operator's global AGENTS.md,
plugins, agents and MCP servers never reach the run. ``OPENCODE_*`` variables are dropped,
the Claude Code fallbacks (``~/.claude/CLAUDE.md``) are disabled, and OpenCode's data and
state live in the same temp directory. Provider keys from ``env_file`` reach the OpenCode
process only and are redacted from the saved logs.

Stops: a rate limit seen in an error event, a non-JSON stdout line or stderr (never in model
text, which can quote "429"), the per-run token or dollar reserve, and the timeout each kill
the process tree. A run whose output carries no usage is flagged so the matrix stops.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from docdelta.agents.base import AgentRun
from docdelta.checkout import remove_tree
from docdelta.scoring import extract_answer

DEFAULT_MODEL = "openai/gpt-6-luna"
MAX_RESUMES = 2
RESUME_PROMPT = "Continue. When you are done, give the final answer in the required JSON block."
_RATE_LIMIT = re.compile(r"\b429\b|rate[ _-]?limit|too many requests", re.IGNORECASE)
KILL_WAIT_S = 15.0
PUMP_JOIN_S = 5.0
DATA_DIR = ".data"
STATE_DIR = ".state"
USER_DIR = ".user"
_ERROR_LINE = re.compile(r"level=(ERROR|FATAL)\b|^\s*(error|fatal)\b", re.IGNORECASE)
_INJECTED = re.compile(r"Instructions from: ([^\n\r<]+)")


def _is_error_line(line: str) -> bool:
    """Only error-level lines can report a provider rate limit; INFO lines carry file paths
    and grep patterns ("rate_limit.py", "429") that must not stop the matrix."""
    return bool(_ERROR_LINE.search(line))


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for inner in value.values():
            yield from _strings(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _strings(inner)


class IsolationError(RuntimeError):
    """The per-run config could not be built as specified."""


def resolve_opencode() -> list[str]:
    """The OpenCode executable; on Windows the real ``.exe`` behind the npm shim."""
    found = shutil.which("opencode")
    if not found:
        return ["opencode"]
    if sys.platform == "win32":
        exe = Path(found).parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
        if exe.is_file():
            return [str(exe)]
    return [found]


def default_config_source() -> Path:
    return Path.home() / ".config" / "opencode" / "opencode.json"


def load_api_keys(env_file: Path) -> dict[str, str]:
    """``*_API_KEY`` entries of a ``.env`` file. Values are never printed or logged."""
    keys: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.strip().partition("=")
        name = name.removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if sep and name.endswith("_API_KEY") and value:
            keys[name] = value
    return keys


def minimal_config(source_config: Path | None, model: str, builtin_provider: bool) -> dict[str, Any]:
    """``$schema``, ``model`` and the model's provider only.

    A provider missing from the operator's config is left to OpenCode's built-in catalog
    when *builtin_provider* (its key then comes from the environment).
    """
    real: dict[str, Any] = {}
    if source_config is not None and source_config.is_file():
        real = json.loads(source_config.read_text(encoding="utf-8"))
    provider_id = model.split("/", 1)[0]
    providers = real.get("provider") or {}
    config: dict[str, Any] = {"model": model}
    if provider_id in providers:
        config["provider"] = {provider_id: providers[provider_id]}
    elif not builtin_provider:
        raise IsolationError(
            f"provider {provider_id!r} is not in {source_config} and no --env-file key was given"
        )
    if "$schema" in real:
        config["$schema"] = real["$schema"]
    return config


def isolated_env(base: Mapping[str, str], home: Path, cwd: Path) -> dict[str, str]:
    env = {key: value for key, value in base.items() if not key.upper().startswith("OPENCODE_")}
    env["XDG_CONFIG_HOME"] = str(home)
    env["XDG_DATA_HOME"] = str(home / DATA_DIR)
    env["XDG_STATE_HOME"] = str(home / STATE_DIR)
    # HOME points at an empty directory, so the operator's ~/.claude/CLAUDE.md, ~/.agents/skills
    # and ~/.opencode never reach the run. OPENCODE_DISABLE_CLAUDE_CODE is not used: it would
    # also stop OpenCode loading the repo's own CLAUDE.md in with_docs.
    env["HOME"] = str(home / USER_DIR)
    env["USERPROFILE"] = str(home / USER_DIR)
    for name in ("HOMEDRIVE", "HOMEPATH"):
        env.pop(name, None)
    # An inherited PWD (Git Bash, MSYS) may root OpenCode in the caller's repository.
    env["PWD"] = str(cwd)
    return env


def _seed_ripgrep(data_home: Path) -> None:
    """Link the operator's downloaded ripgrep into the fresh data home, so runs don't fetch it."""
    xdg = os.environ.get("XDG_DATA_HOME")
    source_bin = (Path(xdg) if xdg else Path.home() / ".local" / "share") / "opencode" / "bin"
    target_bin = data_home / "opencode" / "bin"
    target_bin.mkdir(parents=True, exist_ok=True)
    for name in ("rg.exe", "rg"):
        found = source_bin / name
        if found.is_file():
            try:
                os.link(found, target_bin / name)
            except OSError:
                shutil.copy2(found, target_bin / name)


@contextmanager
def isolated_home(config: dict[str, Any]) -> Iterator[Path]:
    home = Path(tempfile.mkdtemp(prefix="docdelta-oc-"))
    try:
        (home / "opencode").mkdir()
        (home / "opencode" / "opencode.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        (home / STATE_DIR).mkdir()
        (home / USER_DIR).mkdir()
        _seed_ripgrep(home / DATA_DIR)
        yield home
    finally:
        try:
            remove_tree(home)
        except OSError:
            print(f"warning: could not remove per-run OpenCode config {home}", file=sys.stderr)


@dataclass
class EventLog:
    """Accumulates ``opencode run --format json`` events from their known locations only."""

    texts: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    usage_events: int = 0
    cost_usd: float = 0.0
    cost_events: int = 0
    tool_calls: int = 0
    session_id: str = ""
    last_step_reason: str = ""
    errors: list[str] = field(default_factory=list)
    rate_limited: bool = False
    #: Instruction files OpenCode injected mid-run ("Instructions from: <path>").
    injected: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_tokens

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if _is_error_line(line):
                self._scan(line)
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if isinstance(event.get("sessionID"), str) and not self.session_id:
            self.session_id = event["sessionID"]
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        for text in _strings(part):
            for match in _INJECTED.finditer(text):
                path = match.group(1).strip()
                if path not in self.injected:
                    self.injected.append(path)
        if kind == "error" or "error" in event:
            message = json.dumps(event.get("error", event))[:500]
            self.errors.append(message)
            self._scan(message)
        elif kind == "text" and isinstance(part.get("text"), str):
            self.texts.append(part["text"])
        elif kind == "tool_use":
            self.tool_calls += 1
        elif kind == "step_finish":
            tokens = part.get("tokens")
            if isinstance(tokens, dict):
                self.usage_events += 1
                self.input_tokens += int(tokens.get("input") or 0)
                self.output_tokens += int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0)
                cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
                self.cache_tokens += int(cache.get("read") or 0) + int(cache.get("write") or 0)
            self.last_step_reason = str(part.get("reason") or "")
            if isinstance(part.get("cost"), int | float):
                self.cost_usd += float(part["cost"])
                self.cost_events += 1

    def feed_stderr(self, line: str) -> None:
        if _is_error_line(line):
            self._scan(line)

    def _scan(self, text: str) -> None:
        if _RATE_LIMIT.search(text):
            self.rate_limited = True


def _pump(stream: IO[str], tag: str, sink: queue.Queue[tuple[str, str | None]]) -> None:
    try:
        for line in stream:
            sink.put((tag, line))
    except BaseException as exc:  # noqa: BLE001 - reported, never re-raised on a thread
        print(f"warning: {tag} pump failed ({exc!r})", file=sys.stderr)
    finally:
        sink.put((tag, None))


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """End the process tree: ``taskkill /T /F`` on Windows, the process group elsewhere."""
    if proc.poll() is None:
        if sys.platform == "win32":
            try:
                done = subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True, timeout=KILL_WAIT_S,
                )
                if done.returncode != 0:
                    proc.kill()
            except (subprocess.TimeoutExpired, OSError):
                proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
    try:
        proc.wait(timeout=KILL_WAIT_S)
    except (subprocess.TimeoutExpired, OSError):
        pass


def stream_opencode(
    cmd: list[str],
    prompt: str,
    cwd: Path,
    env: dict[str, str],
    log_dir: Path,
    timeout_s: float,
    reserve_tokens: int | None,
    reserve_usd: float | None,
    log: EventLog | None = None,
) -> tuple[EventLog, str, int | None]:
    """Run OpenCode once; returns (log, stop reason, exit code).

    Stop reasons: "" (finished), "rate_limited", "over_reserve", "timeout". Passing *log*
    continues a resumed session: limits apply to running totals and log files are appended.
    """
    mode = "a" if log is not None else "w"
    log = log if log is not None else EventLog()
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        **({} if sys.platform == "win32" else {"start_new_session": True}),
    )
    assert proc.stdin and proc.stdout and proc.stderr
    pumps: list[threading.Thread] = []
    reason = ""
    try:
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except OSError:
            pass  # exited early; its output says why
        lines: queue.Queue[tuple[str, str | None]] = queue.Queue()
        for stream, tag in ((proc.stdout, "out"), (proc.stderr, "err")):
            thread = threading.Thread(target=_pump, args=(stream, tag, lines), daemon=True)
            thread.start()
            pumps.append(thread)
        open_streams = 2
        deadline = time.monotonic() + timeout_s
        with (log_dir / "events.jsonl").open(mode, encoding="utf-8") as events, (
            log_dir / "stderr.log"
        ).open(mode, encoding="utf-8") as errors:
            while open_streams:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reason = "timeout"
                    break
                try:
                    tag, line = lines.get(timeout=min(remaining, 1.0))
                except queue.Empty:
                    continue
                if line is None:
                    open_streams -= 1
                    continue
                if tag == "out":
                    events.write(line)
                    log.feed(line)
                else:
                    errors.write(line)
                    log.feed_stderr(line)
                if log.rate_limited:
                    reason = "rate_limited"
                    break
                if (reserve_tokens is not None and log.total_tokens > reserve_tokens) or (
                    reserve_usd is not None and log.cost_usd > reserve_usd
                ):
                    reason = "over_reserve"
                    break
    finally:
        _kill_tree(proc)
        for thread in pumps:
            thread.join(timeout=PUMP_JOIN_S)
    return log, reason, proc.returncode


def redact_text(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def _relative(path: str, cwd: Path) -> str:
    """*path* relative to the checkout when it is inside it, POSIX-style."""
    try:
        return Path(path).resolve().relative_to(cwd.resolve()).as_posix()
    except (ValueError, OSError):
        return path


def _redact(log_dir: Path, secrets: list[str]) -> None:
    for name in ("events.jsonl", "stderr.log"):
        path = log_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        redacted = text
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "[REDACTED]")
        if redacted != text:
            path.write_text(redacted, encoding="utf-8")


class OpenCodeAgent:
    name = "opencode"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        env_file: Path | None = None,
        config_source: Path | None = None,
        opencode_cmd: list[str] | None = None,
        reserve_tokens: int | None = None,
        reserve_usd: float | None = None,
        builtin_provider: bool = False,
    ) -> None:
        self.model = model
        self.keys = load_api_keys(env_file) if env_file is not None else {}
        self.config_source = config_source if config_source is not None else default_config_source()
        self.opencode_cmd = opencode_cmd or resolve_opencode()
        self.reserve_tokens = reserve_tokens
        self.reserve_usd = reserve_usd
        #: Use the model from OpenCode's built-in catalog (e.g. keyless Zen free models).
        self.builtin_provider = builtin_provider

    def run(self, prompt: str, cwd: Path, *, timeout_s: float, log_dir: Path | None = None) -> AgentRun:
        cwd = cwd.resolve()
        log_dir = (log_dir or cwd.parent).resolve()
        config = minimal_config(self.config_source, self.model, builtin_provider=self.builtin_provider or bool(self.keys))
        # --title skips OpenCode's title request, whose tokens no event reports.
        cmd = [*self.opencode_cmd, "run", "--pure", "--print-logs", "--title", "docdelta",
               "-m", self.model, "--format", "json"]
        started = time.monotonic()
        secrets = [value for value in self.keys.values() if value]
        log, reason, code, resumes = EventLog(), "", None, 0
        try:
            with isolated_home(config) as home:
                env = isolated_env(os.environ, home, cwd)
                env.update(self.keys)
                log, reason, code = stream_opencode(
                    cmd, prompt, cwd, env, log_dir, timeout_s, self.reserve_tokens, self.reserve_usd
                )
                # OpenCode's run mode sometimes exits right after a tool-calls step; resume it.
                while (
                    not reason
                    and resumes < MAX_RESUMES
                    and log.last_step_reason == "tool-calls"
                    and log.session_id
                    and extract_answer("\n".join(log.texts)) is None
                ):
                    resumes += 1
                    log, reason, code = stream_opencode(
                        [*cmd, "--session", log.session_id], RESUME_PROMPT, cwd, env, log_dir,
                        timeout_s, self.reserve_tokens, self.reserve_usd, log=log,
                    )
        finally:
            # Also on Ctrl+C: raw logs never stay on disk with a key in them.
            _redact(log_dir, secrets)

        stop = ""
        if reason == "over_reserve":
            stop = "over_reserve"
        elif reason == "timeout":
            stop = "timeout"
        elif reason != "rate_limited" and log.usage_events == 0:
            stop = "no_usage"
        elif reason != "rate_limited" and self.reserve_usd is not None and (
            log.cost_events == 0 or log.cost_usd == 0.0
        ):
            # A provider reporting cost 0 (no price metadata) cannot keep a dollar cap.
            stop = "no_cost"
        error = reason or ("; ".join(log.errors) if log.errors else "")
        if not error and code not in (0, None):
            error = f"exit code {code}"
        return AgentRun(
            final_text=redact_text("\n".join(log.texts), secrets),
            input_tokens=log.input_tokens + log.cache_tokens,
            output_tokens=log.output_tokens,
            cost_usd=round(log.cost_usd, 6),
            seconds=round(time.monotonic() - started, 1),
            error=redact_text(error or stop, secrets)[:500],
            rate_limited=reason == "rate_limited",
            stop=stop,
            tool_calls=log.tool_calls,
            resumes=resumes,
            injected=[_relative(path, cwd) for path in log.injected],
        )
