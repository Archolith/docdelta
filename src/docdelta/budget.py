"""Spend caps. A run is admitted only while its reserve still fits under every cap."""

from __future__ import annotations

from dataclasses import dataclass


class BudgetExhausted(RuntimeError):
    """The next run's reserve would pass a cap."""


class AccountingError(RuntimeError):
    """A run reported no usage or no cost, so the caps cannot be kept."""


class RateLimited(RuntimeError):
    """The provider rate-limited a run. The matrix stops; nothing is retried."""


@dataclass
class Budget:
    cap_tokens: int | None = None
    reserve_tokens: int = 400_000
    cap_usd: float | None = None
    reserve_usd: float = 0.0
    used_tokens: int = 0
    used_usd: float = 0.0

    def check(self) -> None:
        if self.cap_tokens is not None and self.used_tokens + self.reserve_tokens > self.cap_tokens:
            raise BudgetExhausted(
                f"the next run's {self.reserve_tokens:,}-token reserve would pass the "
                f"{self.cap_tokens:,}-token cap ({self.used_tokens:,} used)"
            )
        if self.cap_usd is not None and self.used_usd + self.reserve_usd > self.cap_usd:
            raise BudgetExhausted(
                f"the next run's ${self.reserve_usd:.2f} reserve would pass the "
                f"${self.cap_usd:.2f} cap (${self.used_usd:.4f} spent)"
            )

    def spend(self, tokens: int, usd: float = 0.0) -> None:
        self.used_tokens += tokens
        self.used_usd += usd
