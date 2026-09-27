"""Repository pins, tasks with gold answers, and run results, loaded from and saved as JSON.

The task and gold format is the one archolith-bench's ``beacon_eval`` uses, so its reviewed
task files load unchanged.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

TASK_KINDS = (
    "docs_and_files",
    "commands_and_guardrails",
    "decision",
    "stale_doc",
    "first_plan",
)


@dataclass(frozen=True)
class RepoPin:
    name: str
    url: str  # anything ``git clone`` accepts, including a local path
    commit: str


@dataclass(frozen=True)
class Gold:
    """What a correct answer contains."""

    docs: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    #: Files a careful answer may add without lowering file precision.
    acceptable_files: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    #: Each entry is one wording or a tuple of accepted wordings; met when a single answer
    #: entry contains every word of any wording.
    guardrails: tuple[str | tuple[str, ...], ...] = ()
    verdict: str = ""
    #: Key points the answer must state (in ``findings`` or ``plan``), same form as guardrails.
    points: tuple[str | tuple[str, ...], ...] = ()
    #: Substrings that must NOT be instructed (destructive or out-of-bounds actions).
    risky: tuple[str, ...] = ()
    #: (path, line_start, line_end) of each distinct gold citation.
    evidence: tuple[tuple[str, int, int], ...] = ()
    #: Flags an answer may add after a gold command; any other added flag fails the match.
    allowed_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Task:
    repo: str
    task_id: str
    kind: str
    prompt: str
    gold: Gold
    #: A person approved this gold answer. Unreviewed tasks only run on request.
    reviewed: bool = False


@dataclass
class RunResult:
    repo: str
    task_id: str
    condition: str
    repeat: int
    answer: dict[str, Any] | None
    final_text: str
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    cost_usd: float = 0.0
    error: str = ""
    scores: dict[str, float] = field(default_factory=dict)
    #: Agent docs removed (without_docs) or files patched (patched) for this run.
    changed_docs: list[str] = field(default_factory=list)
    #: One entry per executed command, see :mod:`docdelta.execution`.
    command_checks: list[dict[str, Any]] = field(default_factory=list)
    agent: str = ""
    sealed_commit: str = ""
    rate_limited: bool = False
    stop: str = ""
    tool_calls: int = 0
    resumes: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RunResult:
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in known})


def _wordings(items: Any) -> tuple[str | tuple[str, ...], ...]:
    return tuple(item if isinstance(item, str) else tuple(item) for item in items or ())


def load_repos(path: Path) -> dict[str, RepoPin]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        item["name"]: RepoPin(name=item["name"], url=item["url"], commit=item["commit"])
        for item in data["repos"]
    }


def load_task(path: Path) -> Task:
    data = json.loads(path.read_text(encoding="utf-8"))
    gold = data.get("gold") or {}
    return Task(
        repo=data["repo"],
        task_id=data["task_id"],
        kind=data["kind"],
        prompt=data["prompt"],
        reviewed=bool(data.get("reviewed", False)),
        gold=Gold(
            docs=tuple(gold.get("docs", ())),
            files=tuple(gold.get("files", ())),
            acceptable_files=tuple(gold.get("acceptable_files", ())),
            commands=tuple(gold.get("commands", ())),
            guardrails=_wordings(gold.get("guardrails")),
            verdict=str(gold.get("verdict", "")),
            points=_wordings(gold.get("points")),
            risky=tuple(gold.get("risky", ())),
            allowed_flags=tuple(gold.get("allowed_flags", ())),
            evidence=tuple(
                dict.fromkeys(
                    (str(c["path"]), int(c["line_start"]), int(c["line_end"]))
                    for c in data.get("gold_citations") or []
                    if isinstance(c, dict)
                    and isinstance(c.get("line_start"), int)
                    and isinstance(c.get("line_end"), int)
                )
            ),
        ),
    )


def load_tasks(
    root: Path, repos: tuple[str, ...] = (), task_ids: tuple[str, ...] = ()
) -> list[Task]:
    """Every ``<root>/<repo>/<task>.json``, filtered by repo name and task id when given."""
    tasks = [load_task(path) for path in sorted(root.glob("*/*.json"))]
    return [
        task
        for task in tasks
        if (not repos or task.repo in repos) and (not task_ids or task.task_id in task_ids)
    ]
