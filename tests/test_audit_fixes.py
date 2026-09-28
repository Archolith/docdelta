"""Counterexamples for the 2026-09-27 audit findings (IDs refer to that report)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from docdelta.agents.base import AgentRun
from docdelta.agents.fake import DocReadingAgent
from docdelta.budget import Budget
from docdelta.checkout import export_commit, seal
from docdelta.conditions import find_agent_docs
from docdelta.judge import JUDGED_FILE, answer_hash
from docdelta.models import Gold, RepoPin, RunResult, Task
from docdelta.report import badge, scorecard_markdown
from docdelta.runner import (
    MatrixConfig,
    StaleResults,
    load_results,
    run_dir,
    run_matrix,
    spent_so_far,
)

TASK = Task(
    repo="sample", task_id="sample-t1", kind="commands_and_guardrails", prompt="What do you run?",
    gold=Gold(commands=("make test",)), reviewed=True,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


class RecordingAgent:
    """Answers 'make test' and records where it was run."""

    name = "recording"

    def __init__(self, model: str = "m1", injected: list[str] | None = None, stop: str = "") -> None:
        self.model = model
        self.cwds: list[Path] = []
        self.injected = injected or []
        self.stop = stop

    def run(self, prompt: str, cwd: Path, *, timeout_s: float, log_dir: Path | None = None) -> AgentRun:
        self.cwds.append(cwd)
        text = '```json\n{"commands": ["make test"]}\n```'
        return AgentRun(final_text=text, input_tokens=10, cost_usd=0.01,
                        injected=list(self.injected), stop=self.stop)


def test_f1_agent_path_names_no_condition_task_or_workdir(sample_repo: RepoPin, tmp_path: Path) -> None:
    agent = RecordingAgent()
    work = tmp_path / "work"
    run_matrix([TASK], {"sample": sample_repo}, agent, MatrixConfig(workdir=work, repeats=1), Budget())
    for cwd in agent.cwds:
        text = str(cwd)
        assert "with_docs" not in text and "without_docs" not in text and "sample-t1" not in text
        assert work.resolve() not in cwd.resolve().parents


def test_f3_harness_config_and_skill_files_count_as_agent_docs(tmp_path: Path) -> None:
    for rel in ("CONTEXT.md", "AGENT.md", "opencode.json", ".opencode/agent/rev.md",
                ".agents/skills/x/SKILL.md", ".github/prompts/p.prompt.md", "src/.claude/x.md",
                "README.md"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    found = find_agent_docs(tmp_path, fold_case=False)
    assert "README.md" not in found
    assert set(found) == {"CONTEXT.md", "AGENT.md", "opencode.json", ".opencode/agent/rev.md",
                          ".agents/skills/x/SKILL.md", ".github/prompts/p.prompt.md", "src/.claude/x.md"}


def _result(condition: str, repeat: int, scores: dict[str, float], **extra) -> RunResult:
    return RunResult(repo="r", task_id="t1", condition=condition, repeat=repeat, answer={},
                     final_text="", input_tokens=100, scores=scores, **extra)


def test_f5_partial_judging_is_not_mixed_into_the_badge() -> None:
    det = {"answered": 1.0, "guardrail_recall": 0.0}
    results = [_result("with_docs", n, {**det, "guardrail_recall_judged": 1.0}) for n in (1, 2)]
    results += [_result("without_docs", n, dict(det)) for n in (1, 2)]
    shown = badge(results)
    assert shown["color"] == "lightgrey" and shown["message"].startswith("+0.00")


def test_f6_resume_refuses_results_from_another_model(sample_repo: RepoPin, tmp_path: Path) -> None:
    config = MatrixConfig(workdir=tmp_path / "work", repeats=1)
    run_matrix([TASK], {"sample": sample_repo}, RecordingAgent("m1"), config, Budget())
    other = RecordingAgent("m2")
    with pytest.raises(StaleResults):
        run_matrix([TASK], {"sample": sample_repo}, other, config, Budget())
    assert other.cwds == []


def test_f11_stale_judge_verdicts_are_ignored(sample_repo: RepoPin, tmp_path: Path) -> None:
    config = MatrixConfig(workdir=tmp_path / "work", repeats=1)
    run_matrix([TASK], {"sample": sample_repo}, RecordingAgent(), config, Budget())
    directory = run_dir(config.workdir, "sample", "sample-t1", "with_docs", 1)
    (directory / JUDGED_FILE).write_text(json.dumps({
        "answer_only_sha256": answer_hash({"commands": ["something else"]}),
        "scores": {"guardrail_recall_judged": 1.0},
    }), encoding="utf-8")
    loaded = [r for r in load_results(config.workdir) if r.condition == "with_docs"]
    assert "guardrail_recall_judged" not in loaded[0].scores


def test_f12_ledger_counts_stopped_runs(sample_repo: RepoPin, tmp_path: Path) -> None:
    config = MatrixConfig(workdir=tmp_path / "work", repeats=1)
    run_matrix([TASK], {"sample": sample_repo}, RecordingAgent(stop="timeout"), config, Budget())
    tokens, usd = spent_so_far(config.workdir)
    assert tokens == 20 and usd == pytest.approx(0.02)


def test_f13_timeout_is_saved_as_stopped_and_rerun(sample_repo: RepoPin, tmp_path: Path) -> None:
    config = MatrixConfig(workdir=tmp_path / "work", repeats=1)
    first = run_matrix([TASK], {"sample": sample_repo}, RecordingAgent(stop="timeout"), config, Budget())
    assert first == [] and load_results(config.workdir) == []
    again = RecordingAgent()
    second = run_matrix([TASK], {"sample": sample_repo}, again, config, Budget())
    assert len(again.cwds) == 2 and len(second) == 2


def test_contaminated_without_docs_runs_are_excluded(sample_repo: RepoPin, tmp_path: Path) -> None:
    config = MatrixConfig(workdir=tmp_path / "work", repeats=2)
    run_matrix([TASK], {"sample": sample_repo}, RecordingAgent(injected=["docs/AGENTS.md"]), config, Budget())
    results = load_results(config.workdir)
    assert [r.contaminated for r in results if r.condition == "without_docs"] == [True, True]
    assert not any(r.contaminated for r in results if r.condition == "with_docs")
    assert "Excluded:** 2 without_docs" in scorecard_markdown(results, "sample")
    assert badge(results)["message"] == "insufficient runs"


def test_f15_operator_git_hooks_do_not_run_in_seal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "post-commit"
    hook.write_text("#!/bin/sh\necho injected > AGENTS.md\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    config = tmp_path / "global.gitconfig"
    config.write_text(f"[core]\n\thooksPath = {hooks.as_posix()}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "a.py").write_text("x", encoding="utf-8")
    seal(checkout)
    assert not (checkout / "AGENTS.md").exists()


def test_f19_export_ignore_does_not_hide_committed_files(tmp_path: Path) -> None:
    upstream = tmp_path / "up"
    upstream.mkdir()
    (upstream / "notes.md").write_text("n", encoding="utf-8")
    (upstream / ".gitattributes").write_text("notes.md export-ignore\n", encoding="utf-8")
    _git(upstream, "init", "-q")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-q", "--no-verify", "-m", "i")
    pin = RepoPin(name="up", url=str(upstream), commit=_git(upstream, "rev-parse", "HEAD"))
    checkout = export_commit(pin, tmp_path / "co", tmp_path / "cache")
    assert (checkout / "notes.md").is_file()


def test_f21_result_json_inside_kept_checkout_is_ignored(sample_repo: RepoPin, tmp_path: Path) -> None:
    config = MatrixConfig(workdir=tmp_path / "work", repeats=1, keep_checkouts=True)
    run_matrix([TASK], {"sample": sample_repo}, DocReadingAgent(), config, Budget())
    kept = run_dir(config.workdir, "sample", "sample-t1", "with_docs", 1) / "checkout"
    assert kept.is_dir()
    (kept / "result.json").write_text('{"not": "a run"}', encoding="utf-8")
    assert len(load_results(config.workdir)) == 2


def test_nested_context_md_is_a_human_page_even_when_folding_case(tmp_path: Path) -> None:
    # MCP python-sdk ships docs/handlers/context.md; only a root CONTEXT.md is loaded as instructions.
    for rel in ("CONTEXT.md", "docs/handlers/context.md", "i18n/de/pages/handlers/context.md"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    assert find_agent_docs(tmp_path, fold_case=True) == ["CONTEXT.md"]


class _LimitedAgent:
    name = "limited"

    def __init__(self, limited_calls: int) -> None:
        self.model = "m"
        self.limited_calls = limited_calls
        self.calls = 0

    def run(self, prompt: str, cwd: Path, *, timeout_s: float, log_dir: Path | None = None) -> AgentRun:
        self.calls += 1
        if self.calls <= self.limited_calls:
            return AgentRun(final_text="", error="rate_limited", rate_limited=True, input_tokens=5)
        return AgentRun(final_text='```json\n{"commands": ["make test"]}\n```', input_tokens=5, rate_limit_hits=0)


def test_matrix_waits_then_retries_a_rate_limited_run(sample_repo: RepoPin, tmp_path: Path) -> None:
    sleeps: list[float] = []
    config = MatrixConfig(workdir=tmp_path / "work", repeats=1, rate_limit_backoff=(5.0,), sleep=sleeps.append)
    agent = _LimitedAgent(limited_calls=1)
    results = run_matrix([TASK], {"sample": sample_repo}, agent, config, Budget())
    assert sleeps == [5.0] and agent.calls == 3 and len(results) == 2
    log = (config.workdir / "ratelimit.log").read_text(encoding="utf-8")
    assert "waiting 5s before retry 1" in log
    assert (run_dir(config.workdir, "sample", "sample-t1", "with_docs", 1) / "result.json").is_file()
    assert not (run_dir(config.workdir, "sample", "sample-t1", "with_docs", 1) / "rate_limited.json").exists()


def test_matrix_stops_when_rate_limit_waits_run_out(sample_repo: RepoPin, tmp_path: Path) -> None:
    from docdelta.budget import RateLimited

    sleeps: list[float] = []
    config = MatrixConfig(workdir=tmp_path / "work", repeats=1, rate_limit_backoff=(1.0, 2.0), sleep=sleeps.append)
    agent = _LimitedAgent(limited_calls=99)
    with pytest.raises(RateLimited):
        run_matrix([TASK], {"sample": sample_repo}, agent, config, Budget())
    assert sleeps == [1.0, 2.0] and agent.calls == 3
    assert "giving up; matrix stops" in (config.workdir / "ratelimit.log").read_text(encoding="utf-8")


def test_default_config_never_waits_on_a_rate_limit(sample_repo: RepoPin, tmp_path: Path) -> None:
    from docdelta.budget import RateLimited

    sleeps: list[float] = []
    config = MatrixConfig(workdir=tmp_path / "work", repeats=1, sleep=sleeps.append)
    with pytest.raises(RateLimited):
        run_matrix([TASK], {"sample": sample_repo}, _LimitedAgent(limited_calls=1), config, Budget())
    assert sleeps == []
