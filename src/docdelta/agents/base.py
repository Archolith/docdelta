"""The seam between the matrix and a coding agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class AgentRun:
    final_text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    error: str = ""
    #: The provider answered 429 / rate limit. The matrix stops and never retries.
    rate_limited: bool = False
    #: A reason the matrix must stop after this run: "over_reserve" (killed at its spend
    #: reserve), "no_usage" or "no_cost" (spend cannot be accounted). Empty otherwise.
    stop: str = ""
    tool_calls: int = 0
    resumes: int = 0


class AgentAdapter(Protocol):
    """Runs one prompt in one checkout and reports the final reply and its usage.

    An adapter must isolate the agent from the operator's machine: no global instruction
    files (``~/.claude/CLAUDE.md``, a global AGENTS.md), no plugins or MCP servers the
    condition did not ask for, and *cwd* as the only project. Without that, a
    ``without_docs`` run can still read agent docs and the A/B measures nothing.
    """

    name: str

    def run(
        self, prompt: str, cwd: Path, *, timeout_s: float, log_dir: Path | None = None
    ) -> AgentRun:
        """*log_dir* receives raw logs (outside *cwd*, so the agent never reads them)."""
        ...
