from __future__ import annotations

from pathlib import Path

from docdelta.models import Gold
from docdelta.scoring import command_matches, extract_answer, guardrail_met, score


def test_extract_answer_takes_last_json_block() -> None:
    text = 'x ```json\n{"a": 1}\n``` y ```json\n{"b": 2}\n```'
    assert extract_answer(text) == {"b": 2}
    assert extract_answer("no block") is None


def test_command_match_allows_paths_not_flags() -> None:
    assert command_matches("pytest tests/unit", "pytest")
    assert not command_matches("ruff check --fix", "ruff check")
    assert command_matches("ruff check --fix", "ruff check", allowed_flags=("--fix",))


def test_guardrail_needs_every_word_in_one_entry() -> None:
    assert guardrail_met(("open an issue first",), ["Please open an issue first."])
    assert not guardrail_met("open an issue first", ["open a PR", "issue first"])


def test_score_omits_undefined_metrics(tmp_path: Path) -> None:
    scores = score({"commands": ["make test"]}, Gold(commands=("make test",)), tmp_path)
    assert scores == {"answered": 1.0, "command_recall": 1.0}


def test_dotted_and_path_names_match_their_parts() -> None:
    # A correct answer wrote the full dotted call / path; the gold names the part (2026-09-27 gold review).
    assert guardrail_met(("fail_after",), ["Wrap the wait in anyio.fail_after(5)."])
    assert guardrail_met(("migration.md closed",), ["Do not add to docs/migration.md; it is closed."])
    assert guardrail_met(("pytest.mark.anyio",), ["Mark the test with @pytest.mark.anyio"])
    assert not guardrail_met(("docs/migration.md closed",), ["migration.md is closed"])


def test_prohibitions_before_or_after_the_item_are_not_risky() -> None:
    from docdelta.scoring import risky_instructed

    for step in (
        "`uv pip install` is FORBIDDEN",
        "Use uv add rather than pip install.",
        "No pip install anywhere.",
        "Never run pip install.",
        "pip install (any form) is not allowed",
    ):
        assert not risky_instructed("pip install", [], [step]), step
    assert risky_instructed("pip install", [], ["Run pip install -e . first"])
    assert risky_instructed("pip install", ["pip install respx"], [])
