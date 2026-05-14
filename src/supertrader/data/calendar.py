"""Trading-day calendar wrapper around `pandas_market_calendars`.

We default to NYSE (`XNYS`). Other calendars (CME for futures, etc.) can be
specified via the constructor argument.

All public methods return Python `date` objects or `pd.DatetimeIndex` of
timezone-naive midnight timestamps — the calendar is about *dates*, not times.
Intraday session times (open / close clock hours) come from
`pandas_market_calendars` directly when needed (Phase 3).
"""

from __future__ import annotations

from datetime import date
from typing import Final

import pandas as pd
import pandas_market_calendars as mcal

DEFAULT_CALENDAR: Final[str] = "XNYS"


class TradingCalendar:
    """Thin facade for date-only trading-calendar queries."""

    def __init__(self, name: str = DEFAULT_CALENDAR) -> None:
        self.name = name
        self._cal = mcal.get_calendar(name)

    def sessions(self, start: date, end: date) -> pd.DatetimeIndex:
        """Return all trading sessions in `[start, end]` (inclusive), as a tz-naive index."""
        if end < start:
            msg = f"end ({end}) is before start ({start})"
            raise ValueError(msg)
        schedule = self._cal.schedule(start_date=start.isoformat(), end_date=end.isoformat())
        # `schedule.index` is tz-naive Timestamp at midnight of each session date.
        return pd.DatetimeIndex(schedule.index)

    def is_session(self, d: date) -> bool:
        """`True` iff `d` is a trading session."""
        sessions = self.sessions(d, d)
        return len(sessions) == 1

    def next_session(self, d: date) -> date:
        """Return the trading session strictly after `d`."""
        # Look up to 10 calendar days ahead — handles holiday weeks.
        end = pd.Timestamp(d) + pd.Timedelta(days=10)
        sessions = self.sessions(d, end.date())
        for ts in sessions:
            if ts.date() > d:
                return ts.date()
        msg = f"No trading session found in 10 days after {d}"
        raise RuntimeError(msg)

    def previous_session(self, d: date) -> date:
        """Return the trading session strictly before `d`."""
        start = pd.Timestamp(d) - pd.Timedelta(days=10)
        sessions = self.sessions(start.date(), d)
        prior = [ts.date() for ts in sessions if ts.date() < d]
        if not prior:
            msg = f"No trading session found in 10 days before {d}"
            raise RuntimeError(msg)
        return prior[-1]
