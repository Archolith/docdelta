from __future__ import annotations

import json
from pathlib import Path

import pytest

from docdelta.judge import JudgeBudgetExhausted, judge_workdir
from docdelta.models import RunResult
from docdelta.report import answer_score
from docdelta.runner import load_results

TASK = {
    "repo": "r", "task_id": "t1", "kind": "commands_and_guardrails", "prompt": "Q?",
    "reviewed": True,
    "gold": {
        "commands": ["make test"],
        "guardrails": [["open an issue first"], ["disclose AI assistant"]],
        "points": [["evaluate_ast"]],
    },
    "gold_citations": [{"item": "open an issue first", "path": "C.md", "line_start": 1,
                        "line_end": 1, "quote": "Open an issue first."}],
}
ANSWER = {
    "commands": ["make test"],
    "guardrails": ["Open an issue and wait until it is accepted before a PR."],
    "findings": ["Add a branch in evaluate_ast for ast.Match."],
    "plan": [],
}


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    tasks = tmp_path / "tasks" / "r"
    tasks.mkdir(parents=True)
    (tasks / "t1.json").write_text(json.dumps(TASK), encoding="utf-8")
    work = tmp_path / "work"
    for condition in ("with_docs", "without_docs"):
        run = work / "runs" / "r" / "t1" / condition / "r1"
        run.mkdir(parents=True)
        result = RunResult(repo="r", task_id="t1", condition=condition, repeat=1, answer=ANSWER,
                           final_text="", scores={"answered": 1.0, "command_recall": 1.0,
                                                  "guardrail_recall": 0.0, "point_recall": 1.0})
        (run / "result.json").write_text(json.dumps(result.to_json()), encoding="utf-8")
    return work, tmp_path / "tasks"


class FakeJudge:
    """Meets 'open an issue first' (grounded), claims 'disclose AI' with made-up evidence,
    and meets evaluate_ast."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, messages):
        self.calls += 1
        user = messages[1]["content"]
        if "GOLD ITEM: open an issue first" in user:
            reply = {"met": True, "evidence": "Open an issue and wait until it is accepted"}
        elif "GOLD ITEM: disclose AI assistant" in user:
            reply = {"met": True, "evidence": "Tell us you used an AI assistant"}
        else:
            reply = {"met": True, "evidence": "branch in evaluate_ast"}
        return json.dumps(reply), {"prompt_tokens": 1000, "completion_tokens": 100}


def test_judge_grounds_evidence_and_caches(tmp_path: Path) -> None:
    work, tasks = _setup(tmp_path)
    fake = FakeJudge()
    count, spent = judge_workdir(work, tasks, fake, budget_usd=1.0)
    assert count == 2 and fake.calls == 6 and spent > 0

    results = load_results(work)
    for result in results:
        # The ungrounded "disclose AI" verdict does not count.
        assert result.scores["guardrail_recall_judged"] == 0.5
        assert result.scores["point_recall_judged"] == 1.0
        # Judged guardrails (0.5) replace deterministic (0.0): mean(1.0, 0.5, 1.0).
        assert answer_score(result.scores) == pytest.approx(2.5 / 3)

    again = FakeJudge()
    assert judge_workdir(work, tasks, again, budget_usd=1.0) == (2, 0.0)
    assert again.calls == 0


def test_judge_stops_before_passing_cap(tmp_path: Path) -> None:
    work, tasks = _setup(tmp_path)
    with pytest.raises(JudgeBudgetExhausted):
        judge_workdir(work, tasks, FakeJudge(), budget_usd=0.001)


def test_gold_change_forces_rejudge(tmp_path: Path) -> None:
    work, tasks = _setup(tmp_path)
    judge_workdir(work, tasks, FakeJudge(), budget_usd=1.0)
    task_file = tasks / "r" / "t1.json"
    data = json.loads(task_file.read_text(encoding="utf-8"))
    data["gold"]["guardrails"] = data["gold"]["guardrails"][:1]
    task_file.write_text(json.dumps(data), encoding="utf-8")
    again = FakeJudge()
    judge_workdir(work, tasks, again, budget_usd=1.0)
    assert again.calls == 4
    assert all(r.scores["guardrail_recall_judged"] == 1.0 for r in load_results(work))
