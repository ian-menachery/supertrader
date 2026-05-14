"""Tests for TrainTestHoldoutSplit derivation and HoldoutGuard touch-counter."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from supertrader.backtest.splits import (
    HoldoutGuard,
    HoldoutOverrideLog,
    HoldoutTouchedError,
    TrainTestHoldoutSplit,
)
from supertrader.config.schemas import BacktestConfig, CostsConfig
from supertrader.data.calendar import TradingCalendar


@pytest.fixture(scope="module")
def calendar() -> TradingCalendar:
    return TradingCalendar()


@pytest.fixture
def config() -> BacktestConfig:
    return BacktestConfig(
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        train_end=date(2024, 6, 30),
        test_end=date(2024, 9, 30),
        costs=CostsConfig(),
    )


class TestTrainTestHoldoutSplit:
    def test_windows_are_disjoint(self, config: BacktestConfig, calendar: TradingCalendar) -> None:
        split = TrainTestHoldoutSplit.from_config(config, calendar)
        train_set = set(split.train.date)
        test_set = set(split.test.date)
        holdout_set = set(split.holdout.date)
        assert train_set.isdisjoint(test_set)
        assert train_set.isdisjoint(holdout_set)
        assert test_set.isdisjoint(holdout_set)

    def test_windows_cover_all_sessions(
        self, config: BacktestConfig, calendar: TradingCalendar
    ) -> None:
        all_sessions = calendar.sessions(config.start, config.end)
        split = TrainTestHoldoutSplit.from_config(config, calendar)
        combined = list(split.train.date) + list(split.test.date) + list(split.holdout.date)
        assert len(combined) == len(all_sessions)
        assert set(combined) == set(all_sessions.date)

    def test_train_ends_at_train_end(
        self, config: BacktestConfig, calendar: TradingCalendar
    ) -> None:
        split = TrainTestHoldoutSplit.from_config(config, calendar)
        # train_end is 2024-06-30 (Sunday); last train session must be <= that
        assert split.train.date.max() <= config.train_end

    def test_test_window_starts_after_train_end(
        self, config: BacktestConfig, calendar: TradingCalendar
    ) -> None:
        split = TrainTestHoldoutSplit.from_config(config, calendar)
        assert split.test.date.min() > config.train_end

    def test_holdout_starts_after_test_end(
        self, config: BacktestConfig, calendar: TradingCalendar
    ) -> None:
        split = TrainTestHoldoutSplit.from_config(config, calendar)
        assert split.holdout.date.min() > config.test_end


class TestHoldoutGuard:
    def test_first_touch_succeeds(self, tmp_path: Path) -> None:
        guard = HoldoutGuard(tmp_path / "meta.sqlite")
        guard.evaluate(run_id="run-1", config_hash="abc123")
        assert guard.has_touched("abc123") is True

    def test_second_touch_same_hash_raises(self, tmp_path: Path) -> None:
        guard = HoldoutGuard(tmp_path / "meta.sqlite")
        guard.evaluate("run-1", "abc123")
        with pytest.raises(HoldoutTouchedError, match="already evaluated"):
            guard.evaluate("run-2", "abc123")

    def test_different_hashes_coexist(self, tmp_path: Path) -> None:
        guard = HoldoutGuard(tmp_path / "meta.sqlite")
        guard.evaluate("run-1", "hash-a")
        guard.evaluate("run-2", "hash-b")
        assert guard.has_touched("hash-a")
        assert guard.has_touched("hash-b")

    def test_has_touched_false_for_unknown(self, tmp_path: Path) -> None:
        guard = HoldoutGuard(tmp_path / "meta.sqlite")
        assert guard.has_touched("never-evaluated") is False

    def test_error_message_includes_reset_instructions(self, tmp_path: Path) -> None:
        guard = HoldoutGuard(tmp_path / "meta.sqlite")
        guard.evaluate("run-1", "abc")
        with pytest.raises(HoldoutTouchedError, match=r"reset_holdout_lock\.py"):
            guard.evaluate("run-2", "abc")

    def test_empty_run_id_raises(self, tmp_path: Path) -> None:
        guard = HoldoutGuard(tmp_path / "meta.sqlite")
        with pytest.raises(ValueError, match="run_id"):
            guard.evaluate("", "abc")

    def test_empty_config_hash_raises(self, tmp_path: Path) -> None:
        guard = HoldoutGuard(tmp_path / "meta.sqlite")
        with pytest.raises(ValueError, match="config_hash"):
            guard.evaluate("run-1", "")

    def test_guard_initializes_table_on_fresh_db(self, tmp_path: Path) -> None:
        # The file doesn't exist before this; constructor creates it.
        db = tmp_path / "fresh.sqlite"
        HoldoutGuard(db)
        assert db.exists()

    def test_clear_removes_row_and_permits_retouch(self, tmp_path: Path) -> None:
        guard = HoldoutGuard(tmp_path / "meta.sqlite")
        guard.evaluate("run-1", "abc")
        cleared = guard.clear("abc")
        assert cleared is not None
        assert cleared[0] == "run-1"
        # second touch with same hash now succeeds
        guard.evaluate("run-2", "abc")
        assert guard.has_touched("abc")

    def test_clear_unknown_hash_is_idempotent(self, tmp_path: Path) -> None:
        guard = HoldoutGuard(tmp_path / "meta.sqlite")
        assert guard.clear("never-evaluated") is None

    def test_clear_empty_hash_raises(self, tmp_path: Path) -> None:
        guard = HoldoutGuard(tmp_path / "meta.sqlite")
        with pytest.raises(ValueError, match="config_hash"):
            guard.clear("")


class TestHoldoutOverrideLog:
    def test_append_writes_one_json_line(self, tmp_path: Path) -> None:
        log = HoldoutOverrideLog(tmp_path / "overrides.log")
        log.append(
            config_hash="abc123",
            original_run_id="run-1",
            original_touched_at="2026-05-14T10:00:00+00:00",
            reason="re-eval after sentiment fix",
            git_sha="deadbeef" * 5,
            operator="test",
        )
        lines = (tmp_path / "overrides.log").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        import json as _json

        record = _json.loads(lines[0])
        assert record["config_hash"] == "abc123"
        assert record["reason"] == "re-eval after sentiment fix"
        assert record["operator"] == "test"
        assert "timestamp_utc" in record

    def test_append_is_additive(self, tmp_path: Path) -> None:
        log = HoldoutOverrideLog(tmp_path / "overrides.log")
        log.append(
            config_hash="h1",
            original_run_id="r1",
            original_touched_at="2026-05-14T10:00:00+00:00",
            reason="first",
            git_sha="a" * 40,
        )
        log.append(
            config_hash="h2",
            original_run_id="r2",
            original_touched_at="2026-05-15T10:00:00+00:00",
            reason="second",
            git_sha="b" * 40,
        )
        records = log.read_all()
        assert len(records) == 2
        assert records[0]["reason"] == "first"
        assert records[1]["reason"] == "second"

    def test_append_preserves_unicode_reason(self, tmp_path: Path) -> None:
        log = HoldoutOverrideLog(tmp_path / "overrides.log")
        log.append(
            config_hash="h",
            original_run_id="r",
            original_touched_at="2026-05-14T10:00:00+00:00",
            reason="rerun — Sharpe was wrong by 𝛅",
            git_sha="c" * 40,
        )
        records = log.read_all()
        assert records[0]["reason"] == "rerun — Sharpe was wrong by 𝛅"

    def test_append_empty_reason_raises(self, tmp_path: Path) -> None:
        log = HoldoutOverrideLog(tmp_path / "overrides.log")
        with pytest.raises(ValueError, match="reason"):
            log.append(
                config_hash="h",
                original_run_id="r",
                original_touched_at="t",
                reason="",
                git_sha="a" * 40,
            )

    def test_read_all_returns_empty_when_no_log(self, tmp_path: Path) -> None:
        log = HoldoutOverrideLog(tmp_path / "missing.log")
        assert log.read_all() == []
