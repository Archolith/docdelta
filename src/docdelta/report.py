"""Scorecards and a shields.io endpoint badge from saved run results.

The headline number is the *answer score*: the mean of the answer metrics a task's gold
defines (commands, guardrails, key points, verdict). Doc recall and citation evidence are
left out on purpose -- a ``without_docs`` run cannot cite an AGENTS.md that is not there, so
counting them would reward the docs for existing rather than for helping.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import mean, median

from docdelta.conditions import WITH_DOCS, WITHOUT_DOCS
from docdelta.models import RunResult

ANSWER_METRICS = ("command_recall", "guardrail_recall", "point_recall", "verdict_correct")
#: A judged metric, when present, stands in for its deterministic counterpart.
JUDGED = {"guardrail_recall": "guardrail_recall_judged", "point_recall": "point_recall_judged"}
#: Fewer runs than this per task and condition and the badge says so instead of a number.
MIN_REPEATS = 2
#: A delta inside this band is shown as "no clear effect".
NOISE_BAND = 0.05


def answer_score(scores: dict[str, float]) -> float | None:
    if scores.get("answered", 0.0) == 0.0:
        return 0.0
    values = []
    for name in ANSWER_METRICS:
        judged = JUDGED.get(name)
        if judged and judged in scores:
            values.append(scores[judged])
        elif name in scores:
            values.append(scores[name])
    return mean(values) if values else None


def is_judged(results: list[RunResult]) -> bool:
    return bool(results) and all(
        any(metric in r.scores for metric in JUDGED.values()) for r in results
    )


def token_change(
    results: list[RunResult], with_condition: str = WITH_DOCS, without_condition: str = WITHOUT_DOCS
) -> float | None:
    """Median tokens with docs relative to without (-0.57 = 57% fewer); None if either is missing."""
    with_tokens = [r.total_tokens for r in results if r.condition == with_condition]
    without_tokens = [r.total_tokens for r in results if r.condition == without_condition]
    if not with_tokens or not without_tokens or median(without_tokens) == 0:
        return None
    return median(with_tokens) / median(without_tokens) - 1


@dataclass
class ConditionSummary:
    condition: str
    runs: int
    answered_rate: float
    mean_answer_score: float | None
    median_tokens: float
    median_seconds: float
    total_cost_usd: float
    risky_rate: float | None


def summarize(results: list[RunResult]) -> dict[str, ConditionSummary]:
    by_condition: dict[str, list[RunResult]] = defaultdict(list)
    for result in results:
        by_condition[result.condition].append(result)
    summaries = {}
    for condition, runs in sorted(by_condition.items()):
        scored = [s for s in (answer_score(r.scores) for r in runs) if s is not None]
        risky = [r.scores["risky_false_positive"] for r in runs if "risky_false_positive" in r.scores]
        summaries[condition] = ConditionSummary(
            condition=condition,
            runs=len(runs),
            answered_rate=mean(r.scores.get("answered", 0.0) for r in runs),
            mean_answer_score=mean(scored) if scored else None,
            median_tokens=median(r.total_tokens for r in runs),
            median_seconds=median(r.seconds for r in runs),
            total_cost_usd=sum(r.cost_usd for r in runs),
            risky_rate=mean(risky) if risky else None,
        )
    return summaries


def per_task(results: list[RunResult]) -> dict[str, dict[str, tuple[float | None, int]]]:
    """task_id -> condition -> (mean answer score, runs)."""
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for result in results:
        counts[result.task_id][result.condition] += 1
        value = answer_score(result.scores)
        if value is not None:
            grouped[result.task_id][result.condition].append(value)
    return {
        task: {
            condition: (mean(grouped[task][condition]) if grouped[task][condition] else None, n)
            for condition, n in sorted(conditions.items())
        }
        for task, conditions in sorted(counts.items())
    }


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def scorecard_markdown(results: list[RunResult], repo: str) -> str:
    summaries = summarize(results)
    lines = [
        f"# docdelta scorecard: {repo}",
        "",
        "| Condition | Runs | Answered | Answer score | Median tokens | Median s | Cost $ | Risky |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries.values():
        lines.append(
            f"| {s.condition} | {s.runs} | {s.answered_rate:.0%} | {_fmt(s.mean_answer_score)} | "
            f"{s.median_tokens:,.0f} | {s.median_seconds:.1f} | {s.total_cost_usd:.2f} | "
            f"{_fmt(s.risky_rate)} |"
        )
    tasks = per_task(results)
    conditions = sorted({c for per in tasks.values() for c in per})
    lines += ["", "## Per task (mean answer score, runs)", ""]
    lines.append("| Task | " + " | ".join(conditions) + " |")
    lines.append("|---|" + "---|" * len(conditions))
    for task, per in tasks.items():
        cells = [f"{_fmt(per[c][0])} ({per[c][1]})" if c in per else "-" for c in conditions]
        lines.append(f"| {task} | " + " | ".join(cells) + " |")
    badge_data = badge(results)
    scoring = (
        "Guardrails and key points are LLM-judged (evidence must be copied from the answer)."
        if is_judged(results)
        else "Scored deterministically only (word matching; misses paraphrases). Run `docdelta judge`."
    )
    lines += [
        "",
        f"**Badge:** {badge_data['label']}: {badge_data['message']}",
        "",
        "Answer score = mean of command, guardrail, key-point and verdict recall that each task's "
        f"gold defines. {scoring} Deltas within +/-{NOISE_BAND} are reported as no clear effect; "
        "with few repeats even larger deltas can be noise. Tokens = change in median tokens per "
        "run with docs versus without.",
        "",
    ]
    return "\n".join(lines)


def badge(
    results: list[RunResult],
    with_condition: str = WITH_DOCS,
    without_condition: str = WITHOUT_DOCS,
    pass_threshold: float = 0.8,
) -> dict[str, object]:
    """A shields.io endpoint document: ``{"schemaVersion": 1, "label", "message", "color"}``."""
    tasks = per_task(results)
    label = "agent docs"
    if not tasks or any(
        per.get(with_condition, (None, 0))[1] < MIN_REPEATS
        or per.get(without_condition, (None, 0))[1] < MIN_REPEATS
        for per in tasks.values()
    ):
        return {"schemaVersion": 1, "label": label, "message": "insufficient runs", "color": "lightgrey"}
    with_scores = [per[with_condition][0] for per in tasks.values() if per[with_condition][0] is not None]
    without_scores = [
        per[without_condition][0] for per in tasks.values() if per[without_condition][0] is not None
    ]
    if not with_scores or not without_scores:
        return {"schemaVersion": 1, "label": label, "message": "no scored tasks", "color": "lightgrey"}
    delta = mean(with_scores) - mean(without_scores)
    passed = sum(1 for value in with_scores if value >= pass_threshold)
    if delta >= NOISE_BAND:
        color = "brightgreen"
    elif delta <= -NOISE_BAND:
        color = "orange"
    else:
        color = "lightgrey"
    tokens = token_change(results, with_condition, without_condition)
    token_part = f" | {tokens:+.0%} tokens" if tokens is not None else ""
    return {
        "schemaVersion": 1,
        "label": label,
        "message": f"{delta:+.2f} score{token_part} | {passed}/{len(tasks)} tasks",
        "color": color,
    }
