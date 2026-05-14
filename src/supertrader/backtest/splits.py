"""Train/test/holdout split + the HoldoutGuard discipline mechanism.

The split is purely a function of `BacktestConfig` dates and a
`TradingCalendar` instance. The guard is what prevents repeated holdout
touches for the same config hash — the central discipline of this project.

The forcing function:
  1. Every backtest run computes a `config_hash` from its `RunConfig`.
  2. Evaluating against the holdout window inserts a row into
     `holdout_touches` keyed `UNIQUE (config_hash)`.
  3. A second attempt with the same hash raises `HoldoutTouchedError`.
  4. To re-touch deliberately, the operator runs `scripts/reset_holdout_lock.py`
     which logs to `data/runs/holdout_overrides.log` (tamper-evident append-only).

This is discipline, not cryptographic guarantee. A determined developer can
always edit the SQLite file. The goal is to make the *act* of touching the
holdout twice deliberate and visible.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pandas as pd

    from supertrader.config.schemas import BacktestConfig
    from supertrader.data.calendar import TradingCalendar


class HoldoutTouchedError(Exception):
    """Raised when a config_hash has already evaluated against the holdout."""


@dataclass(frozen=True)
class TrainTestHoldoutSplit:
    """Trading-day index for each of the three windows."""

    train: pd.DatetimeIndex
    test: pd.DatetimeIndex
    holdout: pd.DatetimeIndex

    @classmethod
    def from_config(
        cls, config: BacktestConfig, calendar: TradingCalendar
    ) -> TrainTestHoldoutSplit:
        """Derive train/test/holdout from `BacktestConfig` dates via the calendar.

        Schema-validated boundaries:
          train: [config.start, config.train_end]
          test:  (config.train_end, config.test_end]
          holdout: (config.test_end, config.end]

        Each window contains only sessions returned by `calendar.sessions`.
        """
        all_sessions = calendar.sessions(config.start, config.end)
        # Index by date for slicing; pd.DatetimeIndex supports date-based filter.
        train = all_sessions[
            (all_sessions.date >= config.start) & (all_sessions.date <= config.train_end)
        ]
        test = all_sessions[
            (all_sessions.date > config.train_end) & (all_sessions.date <= config.test_end)
        ]
        holdout = all_sessions[
            (all_sessions.date > config.test_end) & (all_sessions.date <= config.end)
        ]
        return cls(train=train, test=test, holdout=holdout)


_SCHEMA_INIT_SQL = """
CREATE TABLE IF NOT EXISTS holdout_touches (
  run_id      TEXT NOT NULL,
  config_hash TEXT NOT NULL UNIQUE,
  touched_at  TEXT NOT NULL
);
"""


class HoldoutGuard:
    """SQLite-backed touch ledger enforcing one-shot holdout evaluation per config."""

    def __init__(self, meta_db_path: Path) -> None:
        self.meta_db_path = meta_db_path
        # Idempotent: a fresh DB gets the table; an existing one is unchanged.
        with sqlite3.connect(self.meta_db_path) as conn:
            conn.executescript(_SCHEMA_INIT_SQL)
            conn.commit()

    def evaluate(self, run_id: str, config_hash: str) -> None:
        """Record an intent to evaluate the holdout for this config.

        Raises:
            HoldoutTouchedError: this `config_hash` has already touched.

        """
        if not run_id:
            msg = "run_id must be non-empty"
            raise ValueError(msg)
        if not config_hash:
            msg = "config_hash must be non-empty"
            raise ValueError(msg)
        now = datetime.now(tz=UTC).isoformat()
        try:
            with sqlite3.connect(self.meta_db_path) as conn:
                conn.execute(
                    "INSERT INTO holdout_touches (run_id, config_hash, touched_at) "
                    "VALUES (?, ?, ?)",
                    (run_id, config_hash, now),
                )
                conn.commit()
        except sqlite3.IntegrityError as e:
            existing = self._existing_touch(config_hash)
            when = existing[1] if existing else "<unknown>"
            who = existing[0] if existing else "<unknown>"
            msg = (
                f"Holdout already evaluated for config_hash={config_hash[:16]}... "
                f"on {when} by run_id={who}. "
                "To re-evaluate intentionally, run scripts/reset_holdout_lock.py "
                "(logs override to data/runs/holdout_overrides.log)."
            )
            raise HoldoutTouchedError(msg) from e

    def has_touched(self, config_hash: str) -> bool:
        """Return True iff this config_hash has already touched the holdout."""
        return self._existing_touch(config_hash) is not None

    def _existing_touch(self, config_hash: str) -> tuple[str, str] | None:
        with sqlite3.connect(self.meta_db_path) as conn:
            row = conn.execute(
                "SELECT run_id, touched_at FROM holdout_touches WHERE config_hash = ?",
                (config_hash,),
            ).fetchone()
        if row is None:
            return None
        return (str(row[0]), str(row[1]))
