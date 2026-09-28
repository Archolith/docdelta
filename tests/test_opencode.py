"""OpenCodeAgent against a fake ``opencode`` executable (a Python script), no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from docdelta.agents.opencode import OpenCodeAgent

FAKE = r'''
import json, os, sys
from pathlib import Path
mode = os.environ["DOCDELTA_FAKE_MODE"]
out = Path(os.environ["DOCDELTA_FAKE_OUT"])
home = Path(os.environ["XDG_CONFIG_HOME"])
out.write_text(json.dumps({
    "config": json.loads((home / "opencode" / "opencode.json").read_text(encoding="utf-8")),
    "opencode_vars": sorted(k for k in os.environ if k.upper().startswith("OPENCODE_")),
    "home": os.environ.get("HOME"), "userprofile": os.environ.get("USERPROFILE"),
    "has_key": os.environ.get("OPENAI_API_KEY") == "sk-test-secret",
    "cwd": os.getcwd(),
    "pwd": os.environ.get("PWD"),
    "argv": sys.argv[1:],
    "stdin": sys.stdin.read(),
}), encoding="utf-8")
def emit(event): print(json.dumps(event), flush=True)
if mode == "ok":
    emit({"type": "step_start", "sessionID": "s1", "part": {}})
    emit({"type": "tool_use", "part": {}})
    emit({"type": "text", "part": {"text": "key sk-test-secret\n```json\n{\"commands\": [\"make test\"]}\n```"}})
    emit({"type": "step_finish", "part": {"reason": "stop", "cost": 0.002,
          "tokens": {"input": 100, "output": 20, "reasoning": 5, "cache": {"read": 50, "write": 0}}}})
elif mode == "ratelimit":
    print("Error: 429 Too Many Requests", file=sys.stderr, flush=True)
elif mode == "expensive":
    emit({"type": "step_finish", "part": {"reason": "tool-calls", "cost": 5.0, "tokens": {"input": 1}}})
    import time; time.sleep(30)
elif mode == "silent":
    emit({"type": "text", "part": {"text": "no usage"}})
elif mode == "zerocost":
    emit({"type": "step_finish", "part": {"reason": "stop", "cost": 0, "tokens": {"input": 900000}}})
elif mode == "infolog":
    print('timestamp=x level=INFO message="touching file" file="src/rate_limit.py" pattern="429"', file=sys.stderr, flush=True)
    emit({"type": "step_finish", "part": {"reason": "stop", "cost": 0.001, "tokens": {"input": 10}}})
elif mode == "inject":
    nl = chr(10)
    emit({"type": "tool_use", "part": {"state": {"output": "file body" + nl + "<system-reminder>Instructions from: " + os.path.join(os.getcwd(), "docs", "AGENTS.md") + nl + "# Agents</system-reminder>"}}})
    emit({"type": "step_finish", "part": {"reason": "stop", "cost": 0.001, "tokens": {"input": 10}}})
'''


@pytest.fixture
def fake_opencode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    script = tmp_path / "fake_opencode.py"
    script.write_text(FAKE, encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-test-secret\nOTHER=1\n", encoding="utf-8")
    source = tmp_path / "opencode.json"
    source.write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "model": "x/y",
        "mcp": {"memory": {"type": "remote"}},
        "provider": {"deepseek": {"options": {}}},
    }), encoding="utf-8")
    monkeypatch.setenv("OPENCODE_CONFIG", "leak")
    out = tmp_path / "fake-out.json"
    monkeypatch.setenv("DOCDELTA_FAKE_OUT", str(out))
    checkout = tmp_path / "run" / "checkout"
    checkout.mkdir(parents=True)

    def make(mode: str, **kwargs) -> tuple[OpenCodeAgent, Path, Path]:
        monkeypatch.setenv("DOCDELTA_FAKE_MODE", mode)
        agent = OpenCodeAgent(
            "openai/gpt-6-luna", env_file=env_file, config_source=source,
            opencode_cmd=[sys.executable, str(script)], **kwargs,
        )
        return agent, checkout, out

    return make


def test_run_is_isolated_parsed_and_redacted(fake_opencode) -> None:
    agent, checkout, out = fake_opencode("ok", reserve_usd=1.0)
    run = agent.run("the prompt", checkout, timeout_s=30, log_dir=checkout.parent)
    seen = json.loads(out.read_text(encoding="utf-8"))

    # Only the model (built-in provider) and $schema: no MCP servers, no other providers.
    assert seen["config"] == {"model": "openai/gpt-6-luna", "$schema": "https://opencode.ai/config.json"}
    # No OPENCODE_* at all: the Claude Code flag would hide the repo's own CLAUDE.md (audit F2).
    assert seen["opencode_vars"] == []
    # HOME is an empty temp dir, not the operator's (audit F16).
    assert seen["home"] == seen["userprofile"] and Path(seen["home"]).name == ".user"
    assert Path(seen["home"]).resolve() != Path.home().resolve()
    assert seen["has_key"] is True
    assert Path(seen["cwd"]).resolve() == checkout.resolve()
    assert seen["stdin"] == "the prompt" and "--pure" in seen["argv"]

    assert "make test" in run.final_text
    assert (run.input_tokens, run.output_tokens) == (150, 25)
    assert run.cost_usd == pytest.approx(0.002)
    assert run.tool_calls == 1 and not run.stop and not run.rate_limited and not run.error
    events = (checkout.parent / "events.jsonl").read_text(encoding="utf-8")
    assert "sk-test-secret" not in events and "[REDACTED]" in events


def test_relative_checkout_gets_absolute_cwd_and_pwd(
    fake_opencode, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A relative PWD made OpenCode double the path (real run, 2026-09-27).
    agent, checkout, out = fake_opencode("ok")
    monkeypatch.chdir(tmp_path)
    agent.run("p", Path("run/checkout"), timeout_s=30, log_dir=Path("run"))
    seen = json.loads(out.read_text(encoding="utf-8"))
    assert Path(seen["pwd"]).is_absolute()
    assert Path(seen["pwd"]).resolve() == Path(seen["cwd"]).resolve() == checkout.resolve()


def test_rate_limit_on_stderr(fake_opencode) -> None:
    agent, checkout, _ = fake_opencode("ratelimit")
    run = agent.run("p", checkout, timeout_s=30, log_dir=checkout.parent)
    assert run.rate_limited and run.error == "rate_limited"


def test_over_reserve_is_killed(fake_opencode) -> None:
    agent, checkout, _ = fake_opencode("expensive", reserve_usd=0.10)
    run = agent.run("p", checkout, timeout_s=60, log_dir=checkout.parent)
    assert run.stop == "over_reserve" and run.seconds < 25


def test_no_usage_stops(fake_opencode) -> None:
    agent, checkout, _ = fake_opencode("silent")
    assert agent.run("p", checkout, timeout_s=30, log_dir=checkout.parent).stop == "no_usage"


def test_f10_key_is_redacted_from_final_text(fake_opencode) -> None:
    agent, checkout, _ = fake_opencode("ok", reserve_usd=1.0)
    run = agent.run("p", checkout, timeout_s=30, log_dir=checkout.parent)
    assert "sk-test-secret" not in run.final_text and "[REDACTED]" in run.final_text


def test_f7_zero_cost_under_a_dollar_cap_stops(fake_opencode) -> None:
    agent, checkout, _ = fake_opencode("zerocost", reserve_usd=0.10)
    assert agent.run("p", checkout, timeout_s=30, log_dir=checkout.parent).stop == "no_cost"


def test_f8_info_log_mentioning_rate_limit_is_not_a_429(fake_opencode) -> None:
    agent, checkout, _ = fake_opencode("infolog", reserve_usd=1.0)
    run = agent.run("p", checkout, timeout_s=30, log_dir=checkout.parent)
    assert not run.rate_limited and not run.stop


def test_injected_instructions_are_recorded_relative_to_checkout(fake_opencode) -> None:
    agent, checkout, _ = fake_opencode("inject", reserve_usd=1.0)
    run = agent.run("p", checkout, timeout_s=30, log_dir=checkout.parent)
    assert run.injected == ["docs/AGENTS.md"]
