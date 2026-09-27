from __future__ import annotations

from docdelta.models import RunResult
from docdelta.report import answer_score, badge, scorecard_markdown


def _result(task: str, condition: str, repeat: int, recall: float) -> RunResult:
    return RunResult(
        repo="r", task_id=task, condition=condition, repeat=repeat, answer={}, final_text="",
        input_tokens=100, output_tokens=10, seconds=1.0,
        scores={"answered": 1.0, "command_recall": recall, "doc_recall": 0.0},
    )


def test_answer_score_ignores_doc_metrics() -> None:
    assert answer_score({"answered": 1.0, "command_recall": 0.5, "doc_recall": 0.0}) == 0.5
    assert answer_score({"answered": 0.0}) == 0.0
    assert answer_score({"answered": 1.0, "doc_recall": 1.0}) is None


def test_badge_needs_repeats() -> None:
    results = [_result("t1", "with_docs", 1, 1.0), _result("t1", "without_docs", 1, 0.0)]
    assert badge(results)["message"] == "insufficient runs"


def test_badge_small_delta_is_grey() -> None:
    results = [
        _result("t1", c, n, 0.8) for c in ("with_docs", "without_docs") for n in (1, 2)
    ]
    shown = badge(results)
    assert shown["color"] == "lightgrey" and shown["message"].startswith("+0.00")


def test_badge_negative_delta_is_orange() -> None:
    results = [_result("t1", "with_docs", n, 0.2) for n in (1, 2)]
    results += [_result("t1", "without_docs", n, 0.9) for n in (1, 2)]
    assert badge(results)["color"] == "orange"


def test_scorecard_lists_conditions_and_tasks() -> None:
    results = [_result("t1", c, n, 1.0) for c in ("with_docs", "without_docs") for n in (1, 2)]
    text = scorecard_markdown(results, "r")
    assert "| with_docs | 2 |" in text and "| t1 |" in text
