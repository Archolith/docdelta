"""Agent adapters: the harness under test, plus deterministic fakes."""

from __future__ import annotations

from docdelta.agents.base import AgentAdapter, AgentRun
from docdelta.agents.fake import DocReadingAgent, ScriptedAgent
from docdelta.agents.opencode import OpenCodeAgent

__all__ = ["AgentAdapter", "AgentRun", "DocReadingAgent", "OpenCodeAgent", "ScriptedAgent", "make_agent"]


def make_agent(name: str, model: str = "") -> AgentAdapter:
    if name == "doc-reader":
        return DocReadingAgent()
    if name == "opencode":
        if not model:
            raise ValueError("--model is required for the opencode agent")
        return OpenCodeAgent(model)
    raise ValueError(f"unknown agent {name!r}; expected doc-reader or opencode")
