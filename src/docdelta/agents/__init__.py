"""Agent adapters: the harness under test, plus deterministic fakes."""

from __future__ import annotations

from docdelta.agents.base import AgentAdapter, AgentRun
from docdelta.agents.fake import DocReadingAgent, ScriptedAgent
from pathlib import Path

from docdelta.agents.opencode import DEFAULT_MODEL, OpenCodeAgent

__all__ = ["AgentAdapter", "AgentRun", "DocReadingAgent", "OpenCodeAgent", "ScriptedAgent", "make_agent"]


def make_agent(
    name: str,
    model: str = "",
    *,
    env_file: Path | None = None,
    config_source: Path | None = None,
    reserve_tokens: int | None = None,
    reserve_usd: float | None = None,
) -> AgentAdapter:
    if name == "doc-reader":
        return DocReadingAgent()
    if name == "opencode":
        return OpenCodeAgent(
            model or DEFAULT_MODEL,
            env_file=env_file,
            config_source=config_source,
            reserve_tokens=reserve_tokens,
            reserve_usd=reserve_usd,
        )
    raise ValueError(f"unknown agent {name!r}; expected doc-reader or opencode")
