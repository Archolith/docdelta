"""OpenCode as the agent under test. NOT IMPLEMENTED in the scaffold.

Port plan (archolith-bench ``beacon_eval`` on origin/master 67d7fc9 already solves each part):

- Isolation: ``isolation.minimal_config`` -- a private ``XDG_CONFIG_HOME`` whose
  ``opencode.json`` holds only ``$schema``, ``model`` and that model's provider; the
  caller's ``OPENCODE_*`` variables dropped; Claude Code fallbacks disabled; OpenCode data
  and state in the same temp directory.
- Streaming and stops: ``runner.stream_opencode`` -- ``opencode run --format json`` with the
  prompt on stdin, token accounting per step, kill on per-run reserve, 429 detection on
  stdout and stderr (``_RATE_LIMIT``), process-tree kill on timeout.
- Resume: up to ``MAX_RESUMES`` continues when OpenCode exits after a tool-calls step.

Drop Beacon/Menhir conditions; docdelta's only condition difference is the checkout.
"""

from __future__ import annotations

from pathlib import Path

from docdelta.agents.base import AgentRun


class OpenCodeAgent:
    name = "opencode"

    def __init__(self, model: str) -> None:
        self.model = model

    def run(self, prompt: str, cwd: Path, *, timeout_s: float) -> AgentRun:
        raise NotImplementedError(
            "OpenCodeAgent is a scaffold stub; port isolation and streaming from "
            "archolith-bench beacon_eval (see module docstring)"
        )
