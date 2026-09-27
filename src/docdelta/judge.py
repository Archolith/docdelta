"""LLM judge for guardrails and key points: does a saved answer state each gold item?

The deterministic scorer needs every word of one accepted wording inside one answer entry,
which misses paraphrases ("open an issue and wait for acceptance" for "open an issue
first"). The judge adds ``guardrail_recall_judged`` and ``point_recall_judged`` beside the
deterministic metrics; the report prefers them when present. Nothing is replaced.

Ported from archolith-bench ``beacon_eval/judge.py`` (origin/master 67d7fc9), widened from
points to guardrails. Per gold item the judge sees the question, the item and its accepted
wordings, the gold citation quotes as reference, and the answer's entries. It never sees the
condition or other runs. A "met" verdict counts only when its evidence is copied from the
answer, and one passage credits one item. Verdicts are cached per run in ``judged.json``
keyed by model, prompt version and answer, so re-judging is free.

Stops: HTTP 429 raises :class:`JudgeRateLimited` (never retried); a call that could pass the
dollar cap raises :class:`JudgeBudgetExhausted` before it is made. The key is never logged.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

DEFAULT_JUDGE_MODEL = "gpt-6-luna"
#: Dollars per million (input, output) tokens; a model without a price cannot keep the cap.
PRICES_PER_M = {"gpt-6-luna": (0.10, 0.50), "gpt-4o-mini": (0.15, 0.60)}
TEMPERATURE_MODELS = frozenset({"gpt-4o-mini"})
CALL_RESERVE_USD = 0.002
JUDGED_FILE = "judged.json"
JUDGE_PROMPT_VERSION = 1
_MIN_EVIDENCE_CHARS = 8

SYSTEM_PROMPT = (
    "You grade one item of an answer to a question about a software project. Decide whether "
    "the ANSWER states the GOLD ITEM: the same specific rule, fact, command or point that the "
    "REFERENCE records, in any wording. Paraphrases and synonyms count. It is NOT met by a "
    "general principle or recommendation that would fit many projects unless it names the same "
    "specific thing, nor by a vaguer, partial or different item. The REFERENCE is the recorded "
    "source of the gold item; the answer need not quote it. Reply with a JSON object: "
    "{\"met\": true or false, \"evidence\": \"the shortest passage copied exactly from the "
    "ANSWER that states the item, or empty\", \"reason\": \"one short sentence\"}."
)

#: metric -> answer keys the judge reads for it.
JUDGED_METRICS = {
    "guardrail_recall_judged": ("guardrails", "findings", "plan"),
    "point_recall_judged": ("findings", "plan"),
}
_GOLD_KEY = {"guardrail_recall_judged": "guardrails", "point_recall_judged": "points"}

Call = Callable[[list[dict[str, str]]], tuple[str, dict[str, int]]]


class JudgeRateLimited(RuntimeError):
    """The judge's provider refused with a rate limit; judging stops, nothing is retried."""


class JudgeBudgetExhausted(RuntimeError):
    """The next judge call could pass the dollar cap."""


@dataclass
class Verdict:
    item: str
    met: bool
    raw_met: bool
    evidence: str
    reason: str


def _norm(text: str) -> str:
    return " ".join(str(text).split()).lower()


def answer_lines(answer: dict[str, Any] | None, keys: tuple[str, ...]) -> list[str]:
    if not isinstance(answer, dict):
        return []
    lines: list[str] = []
    for key in keys:
        value = answer.get(key)
        items = value if isinstance(value, list) else [value] if value else []
        lines += [
            json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item)
            for item in items
        ]
    return [line for line in lines if line.strip()]


def load_references(task_root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Per task: the question and, per judged metric, each item's wordings and cited quotes."""
    refs: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(task_root.glob("*/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        quotes: dict[str, list[str]] = {}
        for citation in data.get("gold_citations") or []:
            if isinstance(citation, dict):
                quotes.setdefault(str(citation.get("item", "")), []).append(str(citation.get("quote", "")))
        gold = data.get("gold") or {}
        items: dict[str, list[dict[str, Any]]] = {}
        for metric, gold_key in _GOLD_KEY.items():
            entries = []
            for entry in gold.get(gold_key) or []:
                wordings = [entry] if isinstance(entry, str) else list(entry)
                entries.append({"wordings": wordings, "quotes": quotes.get(wordings[0], [])})
            items[metric] = entries
        refs[(data["repo"], data["task_id"])] = {"prompt": data["prompt"], "items": items}
    return refs


def build_messages(question: str, item: dict[str, Any], lines: list[str]) -> list[dict[str, str]]:
    wordings = item["wordings"]
    user = "\n\n".join([
        "QUESTION:\n" + question.strip(),
        "GOLD ITEM: " + wordings[0]
        + ("\nAccepted wordings: " + "; ".join(wordings[1:]) if len(wordings) > 1 else ""),
        "REFERENCE:\n" + ("\n".join("- " + q for q in item["quotes"]) or "(none)"),
        "ANSWER:\n" + ("\n".join("- " + line for line in lines) or "(empty)"),
    ])
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def judge_item(call: Call, question: str, item: dict[str, Any], lines: list[str]) -> tuple[Verdict, dict[str, int]]:
    text, usage = call(build_messages(question, item, lines))
    try:
        data = json.loads(text)
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    raw_met = data.get("met") is True
    evidence = str(data.get("evidence") or "")
    grounded = len(evidence.strip()) >= _MIN_EVIDENCE_CHARS and _norm(evidence) in _norm("\n".join(lines))
    return Verdict(
        item=item["wordings"][0],
        met=raw_met and grounded,
        raw_met=raw_met,
        evidence=evidence[:400],
        reason=str(data.get("reason") or "")[:300],
    ), usage


def one_passage_per_item(verdicts: list[Verdict]) -> None:
    """A passage credits one item only: a later met item whose evidence overlaps is not met."""
    used: list[str] = []
    for verdict in verdicts:
        if not verdict.met:
            continue
        evidence = _norm(verdict.evidence)
        if any(evidence in prior or prior in evidence for prior in used):
            verdict.met = False
            verdict.reason = ("evidence already credited to another item; " + verdict.reason)[:300]
            continue
        used.append(evidence)


def call_cost(model: str, usage: dict[str, int]) -> float:
    price_in, price_out = PRICES_PER_M[model]
    return (usage.get("prompt_tokens", 0) * price_in + usage.get("completion_tokens", 0) * price_out) / 1e6


def openai_call(api_key: str, model: str, timeout_s: float = 60.0) -> Call:
    """A chat-completions caller with JSON output. HTTP 429 raises; nothing is retried."""

    def call(messages: list[dict[str, str]]) -> tuple[str, dict[str, int]]:
        payload: dict[str, Any] = {
            "model": model, "messages": messages, "response_format": {"type": "json_object"},
        }
        if model in TEMPERATURE_MODELS:
            payload["temperature"] = 0
        request = Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - fixed https URL
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 429:
                raise JudgeRateLimited(f"judge rate limited (HTTP 429): {exc.reason}") from None
            raise RuntimeError(f"judge call failed: HTTP {exc.code} {exc.reason}") from None
        usage = data.get("usage") or {}
        return str(data["choices"][0]["message"]["content"]), {
            "prompt_tokens": int(usage.get("prompt_tokens", 0)),
            "completion_tokens": int(usage.get("completion_tokens", 0)),
        }

    return call


def _digest(model: str, answer: dict[str, Any] | None) -> str:
    return hashlib.sha256(
        json.dumps([JUDGE_PROMPT_VERSION, model, answer], ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def judge_workdir(
    workdir: Path,
    task_root: Path,
    call: Call,
    model: str = DEFAULT_JUDGE_MODEL,
    budget_usd: float = 0.10,
) -> tuple[int, float]:
    """Judge every saved ``result.json``; returns (runs judged or reused, dollars spent)."""
    if model not in PRICES_PER_M:
        raise ValueError(f"no price for judge model {model!r}; the cap cannot be kept")
    refs = load_references(task_root)
    spent = 0.0
    judged = 0
    for path in sorted((workdir / "runs").rglob("result.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        ref = refs.get((result["repo"], result["task_id"]))
        if ref is None or not any(ref["items"].values()):
            continue
        answer = result.get("answer")
        digest = _digest(model, answer)
        cache = path.parent / JUDGED_FILE
        if cache.is_file():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("answer_sha256") == digest:
                judged += 1
                continue
        scores: dict[str, float] = {}
        details: dict[str, list[dict[str, Any]]] = {}
        for metric, keys in JUDGED_METRICS.items():
            items = ref["items"][metric]
            if not items:
                continue
            lines = answer_lines(answer, keys)
            verdicts: list[Verdict] = []
            for item in items:
                if not lines:
                    verdicts.append(Verdict(item["wordings"][0], False, False, "", "no answer"))
                    continue
                if spent + CALL_RESERVE_USD > budget_usd:
                    raise JudgeBudgetExhausted(
                        f"judge stopped at ${spent:.4f}: the next call could pass the ${budget_usd:.2f} cap"
                    )
                verdict, usage = judge_item(call, ref["prompt"], item, lines)
                spent += call_cost(model, usage)
                verdicts.append(verdict)
            one_passage_per_item(verdicts)
            scores[metric] = sum(v.met for v in verdicts) / len(verdicts)
            details[metric] = [asdict(v) for v in verdicts]
        cache.write_text(
            json.dumps(
                {"model": model, "answer_sha256": digest, "scores": scores, "verdicts": details},
                indent=1, ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        judged += 1
    return judged, spent


def judged_scores(run_dir: Path) -> dict[str, float]:
    """The judged metrics saved beside a run's result, or {} when it was not judged."""
    cache = run_dir / JUDGED_FILE
    if not cache.is_file():
        return {}
    data = json.loads(cache.read_text(encoding="utf-8"))
    return {key: float(value) for key, value in (data.get("scores") or {}).items()}
