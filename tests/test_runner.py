from __future__ import annotations

import json
from pathlib import Path

import pytest

from docdelta.agents.base import AgentRun
from docdelta.agents.fake import DocReadingAgent
from docdelta.budget import Budget, BudgetExhausted, RateLimited
from docdelta.cli import main
from docdelta.models import Gold, RepoPin, Task
from docdelta.report import badge
from docdelta.runner import MatrixConfig, UnreviewedTasks, load_results, run_dir, run_matrix

TASK = Task(
    repo="sample",
    task_id="sample-t1",
    kind="commands_and_guardrails",
    prompt="What must you run before committing, and what must you never do?",
    gold=Gold(commands=("make test",), guardrails=(("never edit generated files",),)),
    reviewed=True,
)


def test_matrix_measures_the_docs(sample_repo: RepoPin, tmp_path: Path) -> None:
    agent = DocReadingAgent()
    config = MatrixConfig(workdir=tmp_path / "work", repeats=2)
    results = run_matrix([TASK], {"sample": sample_repo}, agent, config, Budget())

    assert len(results) == 4
    by_condition = {r.condition: r for r in results}
    assert by_condition["with_docs"].scores["command_recall"] == 1.0
    assert by_condition["with_docs"].scores["guardrail_recall"] == 1.0
    assert by_condition["without_docs"].scores["command_recall"] == 0.0
    assert by_condition["without_docs"].changed_docs == ["AGENTS.md", "src/AGENTS.md"]
    assert not (run_dir(config.workdir, "sample", "sample-t1", "with_docs", 1) / "checkout").exists()

    shown = badge(results)
    assert shown["message"].startswith("+1.00") and shown["color"] == "brightgreen"


def test_saved_results_are_reused(sample_repo: RepoPin, tmp_path: Path) -> None:
    config = MatrixConfig(workdir=tmp_path / "work", repeats=1)
    first = DocReadingAgent()
    run_matrix([TASK], {"sample": sample_repo}, first, config, Budget())
    second = DocReadingAgent()
    again = run_matrix([TASK], {"sample": sample_repo}, second, config, Budget())
    assert first.calls == 2 and second.calls == 0 and len(again) == 2


def test_budget_stops_before_a_run(sample_repo: RepoPin, tmp_path: Path) -> None:
    config = MatrixConfig(workdir=tmp_path / "work", repeats=1)
    agent = DocReadingAgent()
    with pytest.raises(BudgetExhausted):
        run_matrix(
            [TASK], {"sample": sample_repo}, agent, config, Budget(cap_tokens=10, reserve_tokens=100)
        )
    assert agent.calls == 0


class _RateLimitedAgent:
    name = "limited"

    def __init__(self) -> None:
        self.calls = 0

    def run(self, prompt: str, cwd: Path, *, timeout_s: float) -> AgentRun:
        self.calls += 1
        return AgentRun(final_text="", error="429 too many requests", rate_limited=True)


def test_rate_limit_stops_and_is_not_reused(sample_repo: RepoPin, tmp_path: Path) -> None:
    config = MatrixConfig(workdir=tmp_path / "work", repeats=2)
    agent = _RateLimitedAgent()
    with pytest.raises(RateLimited):
        run_matrix([TASK], {"sample": sample_repo}, agent, config, Budget())
    assert agent.calls == 1
    assert load_results(config.workdir) == []


def test_unreviewed_tasks_need_opt_in(sample_repo: RepoPin, tmp_path: Path) -> None:
    draft = Task(**{**TASK.__dict__, "reviewed": False})
    with pytest.raises(UnreviewedTasks):
        run_matrix([draft], {"sample": sample_repo}, DocReadingAgent(),
                   MatrixConfig(workdir=tmp_path / "w"), Budget())


def test_cli_run_report_badge(sample_repo: RepoPin, tmp_path: Path, capsys) -> None:
    repos = tmp_path / "repos.json"
    repos.write_text(json.dumps({"repos": [sample_repo.__dict__]}), encoding="utf-8")
    task_dir = tmp_path / "tasks" / "sample"
    task_dir.mkdir(parents=True)
    (task_dir / "sample-t1.json").write_text(json.dumps({
        "repo": "sample", "task_id": "sample-t1", "kind": "commands_and_guardrails",
        "prompt": TASK.prompt, "reviewed": True, "gold": {"commands": ["make test"]},
    }), encoding="utf-8")
    work = tmp_path / "work"
    assert main(["run", "--repos", str(repos), "--tasks", str(tmp_path / "tasks"),
                 "--workdir", str(work), "--repeats", "2"]) == 0
    assert main(["report", "--workdir", str(work), "--repo", "sample"]) == 0
    assert "| with_docs | 2 |" in capsys.readouterr().out
    out = tmp_path / "badge.json"
    assert main(["badge", "--workdir", str(work), "--repo", "sample", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["schemaVersion"] == 1
