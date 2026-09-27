"""Run the task x condition x repeat matrix.

Per run: fresh export of the pinned commit -> condition applied (docs removed or patched) ->
sealed as its own repository -> the same prompt for every condition -> the agent's final JSON
answer scored against the gold -> ``result.json`` saved. The checkout is deleted afterwards
unless asked to keep it.

Stops: the budget is checked before every run; a rate-limited run is saved and then ends the
matrix (never retried). A saved ``result.json`` is reused on the next invocation, so an
interrupted matrix resumes without paying twice.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from docdelta.agents.base import AgentAdapter
from docdelta.budget import AccountingError, Budget, BudgetExhausted, RateLimited
from docdelta.checkout import export_commit, remove_tree, seal
from docdelta.conditions import DEFAULT_CONDITIONS, PATCHED, prepare
from docdelta.execution import CommandCheck, CommandExecutor, command_success
from docdelta.judge import judged_scores
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


class UnreviewedTasks(RuntimeError):
    """Tasks whose gold no person has approved were selected without opting in."""


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


def run_one(
    task: Task,
    pin: RepoPin,
    condition: str,
    repeat: int,
    agent: AgentAdapter,
    config: MatrixConfig,
    executor: CommandExecutor | None = None,
) -> RunResult:
    # Absolute: the agent resolves PWD and its cwd against each other (a relative path doubles).
    directory = run_dir(config.workdir, pin.name, task.task_id, condition, repeat).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    checkout = directory / "checkout"
    remove_tree(checkout)
    export_commit(pin, checkout, config.workdir / "cache")
    patch = (
        config.patch_dir / f"{pin.name}.patch"
        if condition == PATCHED and config.patch_dir is not None
        else None
    )
    changed = prepare(checkout, condition, patch=patch)
    # Sealing after prepare() keeps removed docs out of the agent-visible history.
    sealed = seal(checkout)
    prompt = build_prompt(task)
    (directory / "prompt.txt").write_text(prompt, encoding="utf-8")

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
    )
    # A rate-limited or stopped run is kept for the record but never reused as finished.
    if run.rate_limited:
        name = RATE_LIMITED_FILE
    elif run.stop:
        name = STOPPED_FILE
    else:
        name = RESULT_FILE
    (directory / name).write_text(json.dumps(result.to_json(), indent=1), encoding="utf-8")
    if not config.keep_checkouts:
        remove_tree(checkout)
    return result


def load_results(workdir: Path, repo: str | None = None) -> list[RunResult]:
    """Saved finished runs, with any judged metrics merged into their scores."""
    root = workdir / "runs" / (repo or "")
    results = []
    for path in sorted(root.rglob(RESULT_FILE)):
        result = RunResult.from_json(json.loads(path.read_text(encoding="utf-8")))
        result.scores.update(judged_scores(path.parent))
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

    results: list[RunResult] = []
    for task in tasks:
        pin = repos[task.repo]
        for repeat in range(1, config.repeats + 1):
            for condition in config.conditions:
                saved = run_dir(config.workdir, pin.name, task.task_id, condition, repeat) / RESULT_FILE
                if saved.is_file():
                    results.append(RunResult.from_json(json.loads(saved.read_text(encoding="utf-8"))))
                    continue
                budget.check()
                result = run_one(task, pin, condition, repeat, agent, config, executor)
                budget.spend(result.total_tokens, result.cost_usd)
                where = f"{pin.name}/{task.task_id}/{condition}/r{repeat}"
                if result.rate_limited:
                    raise RateLimited(f"{where}: {result.error}")
                if result.stop == "over_reserve":
                    raise BudgetExhausted(f"{where} passed its per-run reserve and was killed")
                if result.stop:
                    raise AccountingError(f"{where}: {result.stop}; spend cannot be tracked")
                results.append(result)
    return results
