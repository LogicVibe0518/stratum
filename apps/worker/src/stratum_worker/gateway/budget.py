"""Per-agency spend caps.

OWASP LLM10 (unbounded consumption) in two dozen lines: every call is priced
before it is made, and a day's spend for one agency cannot pass its cap. This is
an in-memory implementation for M6; M1 moves the ledger into Postgres so it holds
across processes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from stratum_worker.gateway.errors import BudgetExceeded

DEFAULT_DAILY_CAP_USD = 5.0
WARN_AT = 0.5  # log a warning once an agency passes half its cap


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


@dataclass
class Budget:
    """Tracks spend per agency per UTC day."""

    daily_cap_usd: float = DEFAULT_DAILY_CAP_USD
    # A lower cap for one agency (the shared demo), in place of daily_cap_usd.
    caps: dict[str, float] = field(default_factory=dict)
    _spent: dict[tuple[str, str], float] = field(default_factory=lambda: defaultdict(float))

    def cap_for(self, agency_id: str) -> float:
        return self.caps.get(agency_id, self.daily_cap_usd)

    def spent_today(self, agency_id: str) -> float:
        return self._spent[(agency_id, _today())]

    def check(self, agency_id: str, estimated_usd: float) -> None:
        """Raise before spending money we should not spend."""
        cap = self.cap_for(agency_id)
        if self.spent_today(agency_id) + estimated_usd > cap:
            raise BudgetExceeded(
                f"agency {agency_id} would exceed its daily cap of "
                f"${cap:.2f} (spent ${self.spent_today(agency_id):.4f})"
            )

    def record(self, agency_id: str, actual_usd: float) -> float:
        """Record real spend after a call. Returns the new total for the day."""
        key = (agency_id, _today())
        self._spent[key] += actual_usd
        return self._spent[key]

    def warn_level(self, agency_id: str) -> float:
        """Fraction of the cap used today, for alerting."""
        cap = self.cap_for(agency_id)
        return self.spent_today(agency_id) / cap if cap else 0.0
