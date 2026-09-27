"""Deterministic agents for tests and dry runs. They spend nothing."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from docdelta.agents.base import AgentRun
from docdelta.conditions import find_agent_docs

_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BULLET = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
COMMAND_HEADS = frozenset({
    "make", "pytest", "pip", "uv", "npm", "npx", "pnpm", "yarn", "cargo", "go", "python",
    "python3", "ruff", "tox", "nox", "just", "gradle", "./gradlew", "mvn", "bun", "deno",
})


def _answer_text(answer: dict[str, Any]) -> str:
    return "Done.\n\n```json\n" + json.dumps(answer, indent=1) + "\n```\n"


class ScriptedAgent:
    """Returns a fixed answer per (task prompt, cwd) from a callable."""

    name = "scripted"

    def __init__(self, respond: Callable[[str, Path], dict[str, Any]]) -> None:
        self._respond = respond
        self.calls = 0

    def run(self, prompt: str, cwd: Path, *, timeout_s: float) -> AgentRun:
        self.calls += 1
        return AgentRun(final_text=_answer_text(self._respond(prompt, cwd)))


class DocReadingAgent:
    """A stand-in that knows only what the checkout's agent docs and README say.

    It lists backticked commands and bullet rules from those files. Useful to show the
    pipeline end to end: with the docs it finds their commands, without them it does not.
    """

    name = "doc-reader"

    def __init__(self) -> None:
        self.calls = 0

    def run(self, prompt: str, cwd: Path, *, timeout_s: float) -> AgentRun:
        self.calls += 1
        sources = find_agent_docs(cwd)
        if (cwd / "README.md").is_file():
            sources.append("README.md")
        commands: list[str] = []
        guardrails: list[str] = []
        read_chars = 0
        for rel in sources:
            text = (cwd / rel).read_text(encoding="utf-8", errors="replace")
            read_chars += len(text)
            for span in _INLINE_CODE.findall(text):
                head = span.split(maxsplit=1)[0] if span.split() else ""
                if head in COMMAND_HEADS and span not in commands:
                    commands.append(span)
            for line in text.splitlines():
                bullet = _BULLET.match(line)
                if bullet:
                    guardrails.append(bullet.group(1))
        answer = {
            "docs": sources,
            "files": [],
            "commands": commands,
            "guardrails": guardrails,
            "verdict": "",
            "findings": [],
            "plan": [],
            "citations": [],
        }
        return AgentRun(
            final_text=_answer_text(answer),
            input_tokens=len(prompt) // 4 + read_chars // 4,
            output_tokens=len(json.dumps(answer)) // 4,
        )
