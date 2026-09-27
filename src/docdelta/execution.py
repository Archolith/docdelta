"""Running the commands an agent proposes, to check they actually work.

Scaffold: only :class:`NoExecution` exists. Proposed commands come from a model and can do
anything, so the real executor must run them in a disposable container with no network
and no host mounts beyond a copy of the checkout -- never on the host.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class CommandCheck:
    command: str
    exit_code: int | None = None
    seconds: float = 0.0
    output_tail: str = ""
    skipped_reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class CommandExecutor(Protocol):
    def check(self, command: str, checkout: Path) -> CommandCheck: ...


class NoExecution:
    """Records every command as skipped."""

    def check(self, command: str, checkout: Path) -> CommandCheck:
        return CommandCheck(command=command, skipped_reason="execution disabled")


class ContainerExecutor:  # pragma: no cover - stub
    """Runs one command in a throwaway, network-less container. NOT IMPLEMENTED."""

    def __init__(self, image: str) -> None:
        self.image = image

    def check(self, command: str, checkout: Path) -> CommandCheck:
        raise NotImplementedError("container execution is not built yet")


def command_success(checks: list[CommandCheck]) -> float | None:
    """Share of executed commands that exited 0; None when none ran."""
    ran = [check for check in checks if check.exit_code is not None]
    if not ran:
        return None
    return sum(1 for check in ran if check.exit_code == 0) / len(ran)
