"""Run the task x condition x repeat matrix.

Per run: fresh export of the pinned commit into a neutral temp directory -> condition applied
(docs removed or patched) -> sealed as its own repository -> the same prompt for every
condition -> the agent's final JSON answer scored against the gold -> ``result.json`` saved in
the run directory. The agent never works inside the workdir, so its paths reveal nothing
about the condition or task, and sibling runs are not beside it.

Stops: the budget (seeded from the workdir's spend ledger) is checked before every run; a
rate-limited or spend-stopped run is saved under another name and ends the matrix (never
retried). A timed-out run is saved as stopped and the matrix continues. A saved
``result.json`` is reused only when its run key (agent, model, commit, condition, patch and
prompt) matches; otherwise the matrix refuses to mix it in.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docdelta import __version__
from docdelta.agents.base import AgentAdapter
from docdelta.budget import AccountingError, Budget, BudgetExhausted, RateLimited
from docdelta.checkout import export_commit, neutral_checkout, seal
from docdelta.conditions import DEFAULT_CONDITIONS, PATCHED, WITHOUT_DOCS, prepare
from docdelta.execution import CommandCheck, CommandExecutor, command_success
from docdelta.judge import JUDGED_FILE, judged_scores
from docdelta.models import RepoPin, RunResult, Task
from docdelta.scoring import extract_answer, score

#: Same line for every condition, so the only difference between conditions is the checkout.
TOOL_NOTE = "Use whatever tools are available to you."

ANSWER_INSTRUCTIONS = (
    "Work read-only: do not edit, create or delete files, and do not run tests or installs. "
    "When you are done, end your reply with one fenced ```json block containing exactly "
    'these keys: "docs" (paths of documents to read), "files" (source files involved), '
    '"commands" (exact shell commands), "guardrails" (rules that apply, each a short '
    'sentence), "verdict" (a single word when the task asks for one, else ""), "findings" '
    '(short statements that answer the question), "plan" (ordered steps), "citations" '
    '(a list of {"path", "line_start", "line_end"} backing your claims).'
)

RESULT_FILE = "result.json"
RATE_LIMITED_FILE = "rate_limited.json"
STOPPED_FILE = "stopped.json"
LEDGER_FILE = "spend.jsonl"
#: Stops after which no further run may start.
FATAL_STOPS = frozenset({"over_reserve", "no_usage", "no_cost"})


class UnreviewedTasks(RuntimeError):
    """Tasks whose gold no person has approved were selected without opting in."""


class StaleResults(RuntimeError):
    """A saved result was produced under a different agent, model, commit, patch or prompt."""


@dataclass
class MatrixConfig:
    workdir: Path
    conditions: tuple[str, ...] = DEFAULT_CONDITIONS
    repeats: int = 3
    timeout_s: float = 900.0
    #: ``<patch_dir>/<repo>.patch`` is applied for the ``patched`` condition.
    patch_dir: Path | None = None
    keep_checkouts: bool = False
    allow_unreviewed: bool = False


def build_prompt(task: Task) -> str:
    return f"{task.prompt}\n\n{TOOL_NOTE}\n\n{ANSWER_INSTRUCTIONS}\n"


def run_dir(workdir: Path, repo: str, task_id: str, condition: str, repeat: int) -> Path:
    return workdir / "runs" / repo / task_id / condition / f"r{repeat}"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def patch_for(config: MatrixConfig, pin: RepoPin, condition: str) -> Path | None:
    if condition != PATCHED or config.patch_dir is None:
        return None
    return config.patch_dir / f"{pin.name}.patch"


def run_key(agent: AgentAdapter, pin: RepoPin, task: Task, condition: str, patch: Path | None) -> dict[str, Any]:
    """Everything that decides what the agent saw; a saved result is reusable only if it matches."""
    return {
        "docdelta": __version__,
        "agent": agent.name,
        "model": str(getattr(agent, "model", "")),
        "repo_url": pin.url,
        "commit": pin.commit,
        "condition": condition,
        "patch_sha256": _sha(patch.read_bytes()) if patch is not None and patch.is_file() else "",
        "prompt_sha256": _sha(build_prompt(task).encode("utf-8")),
    }


def _record_spend(workdir: Path, where: str, tokens: int, usd: float) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    with (workdir / LEDGER_FILE).open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps({"run": where, "tokens": tokens, "usd": usd}) + "\n")


def spent_so_far(workdir: Path) -> tuple[int, float]:
    """Tokens and dollars recorded in *workdir*'s ledger, including stopped runs."""
    path = workdir / LEDGER_FILE
    if not path.is_file():
        return 0, 0.0
    tokens, usd = 0, 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            tokens += int(entry.get("tokens", 0))
            usd += float(entry.get("usd", 0.0))
    return tokens, usd


def run_one(
    task: Task,
    pin: RepoPin,
    condition: str,
    repeat: int,
    agent: AgentAdapter,
    config: MatrixConfig,
    executor: CommandExecutor | None = None,
) -> RunResult:
    directory = run_dir(config.workdir, pin.name, task.task_id, condition, repeat).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # A rerun never inherits the previous attempt's judge verdicts.
    (directory / JUDGED_FILE).unlink(missing_ok=True)
    patch = patch_for(config, pin, condition)
    key = run_key(agent, pin, task, condition, patch)
    prompt = build_prompt(task)
    (directory / "prompt.txt").write_text(prompt, encoding="utf-8")

    # The agent works in a neutral temp path: nothing in it names the condition, task or workdir.
    with neutral_checkout() as checkout:
        export_commit(pin, checkout, config.workdir / "cache")
        changed = prepare(checkout, condition, patch=patch)
        # Sealing after prepare() keeps removed docs out of the agent-visible history.
        sealed = seal(checkout)
        started = time.monotonic()
        run = agent.run(prompt, checkout, timeout_s=config.timeout_s, log_dir=directory)
        seconds = run.seconds or (time.monotonic() - started)

        answer = extract_answer(run.final_text)
        scores = score(answer, task.gold, checkout)
        checks: list[CommandCheck] = []
        if executor is not None and answer is not None:
            commands = [str(c) for c in answer.get("commands") or [] if isinstance(c, str)]
            checks = [executor.check(command, checkout) for command in commands]
            success = command_success(checks)
            if success is not None:
                scores["command_success"] = success
        if config.keep_checkouts:
            kept = directory / "checkout"
            shutil.rmtree(kept, ignore_errors=True)
            shutil.copytree(checkout, kept, ignore=shutil.ignore_patterns(".git"))

    result = RunResult(
        repo=pin.name,
        task_id=task.task_id,
        condition=condition,
        repeat=repeat,
        answer=answer,
        final_text=run.final_text,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        seconds=round(seconds, 3),
        cost_usd=run.cost_usd,
        error=run.error or ("" if answer is not None else "no JSON answer"),
        scores=scores,
        changed_docs=changed,
        command_checks=[check.to_json() for check in checks],
        agent=agent.name,
        sealed_commit=sealed,
        rate_limited=run.rate_limited,
        stop=run.stop,
        tool_calls=run.tool_calls,
        resumes=run.resumes,
        injected=list(run.injected),
        # Agent instructions reaching a without_docs run make it measure the wrong thing.
        contaminated=condition == WITHOUT_DOCS and bool(run.injected),
        run_key=key,
    )
    # A rate-limited or stopped run is kept for the record but never reused as finished.
    if run.rate_limited:
        name = RATE_LIMITED_FILE
    elif run.stop:
        name = STOPPED_FILE
    else:
        name = RESULT_FILE
    for stale in {RESULT_FILE, RATE_LIMITED_FILE, STOPPED_FILE} - {name}:
        (directory / stale).unlink(missing_ok=True)
    (directory / name).write_text(json.dumps(result.to_json(), indent=1), encoding="utf-8")
    return result


def _result_paths(root: Path) -> list[Path]:
    """``runs/<repo>/<task>/<condition>/r<n>/result.json`` only (never files inside a kept checkout)."""
    return sorted(path for path in root.glob(f"*/*/*/r*/{RESULT_FILE}"))


def load_results(workdir: Path, repo: str | None = None) -> list[RunResult]:
    """Saved finished runs, with judged metrics merged when they match the saved answer."""
    root = workdir / "runs"
    results = []
    for path in _result_paths(root):
        result = RunResult.from_json(json.loads(path.read_text(encoding="utf-8")))
        if repo is not None and result.repo != repo:
            continue
        result.scores.update(judged_scores(path.parent, result.answer))
        results.append(result)
    return results


def run_matrix(
    tasks: list[Task],
    repos: dict[str, RepoPin],
    agent: AgentAdapter,
    config: MatrixConfig,
    budget: Budget,
    executor: CommandExecutor | None = None,
) -> list[RunResult]:
    """Every task under every condition, *config.repeats* times; conditions interleave per task."""
    unreviewed = [task.task_id for task in tasks if not task.reviewed]
    if unreviewed and not config.allow_unreviewed:
        raise UnreviewedTasks(f"gold not reviewed: {', '.join(unreviewed)}")
    missing = sorted({task.repo for task in tasks} - repos.keys())
    if missing:
        raise ValueError(f"tasks name repos with no pin: {', '.join(missing)}")
    if PATCHED in config.conditions:
        for name in sorted({task.repo for task in tasks}):
            patch = patch_for(config, repos[name], PATCHED)
            if patch is None or not patch.is_file():
                raise ValueError(f"condition 'patched' needs {name}.patch in --patch-dir, got {patch}")

    results: list[RunResult] = []
    for task in tasks:
        pin = repos[task.repo]
        for repeat in range(1, config.repeats + 1):
            for condition in config.conditions:
                where = f"{pin.name}/{task.task_id}/{condition}/r{repeat}"
                saved = run_dir(config.workdir, pin.name, task.task_id, condition, repeat) / RESULT_FILE
                if saved.is_file():
                    prior = RunResult.from_json(json.loads(saved.read_text(encoding="utf-8")))
                    wanted = run_key(agent, pin, task, condition, patch_for(config, pin, condition))
                    if prior.run_key != wanted:
                        raise StaleResults(
                            f"{where} was produced with a different agent, model, commit, patch or "
                            "prompt; use a fresh --workdir"
                        )
                    results.append(prior)
                    continue
                budget.check()
                result = run_one(task, pin, condition, repeat, agent, config, executor)
                budget.spend(result.total_tokens, result.cost_usd)
                _record_spend(config.workdir, where, result.total_tokens, result.cost_usd)
                if result.rate_limited:
                    raise RateLimited(f"{where}: {result.error}")
                if result.stop == "over_reserve":
                    raise BudgetExhausted(f"{where} passed its per-run reserve and was killed")
                if result.stop in FATAL_STOPS:
                    raise AccountingError(f"{where}: {result.stop}; spend cannot be tracked")
                if not result.stop:
                    results.append(result)
    return results
