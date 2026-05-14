"""Tests for `data.calendar.TradingCalendar` against known NYSE dates."""

from __future__ import annotations

from datetime import date

import pytest

from supertrader.data.calendar import TradingCalendar


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    return TradingCalendar()


class TestSessions:
    def test_january_2024_full_month_session_count(self, cal: TradingCalendar) -> None:
        # January 2024: 23 weekdays minus New Year's Day (Jan 1) and MLK Day (Jan 15)
        # = 21 trading sessions.
        sessions = cal.sessions(date(2024, 1, 1), date(2024, 1, 31))
        assert len(sessions) == 21

    def test_end_before_start_raises(self, cal: TradingCalendar) -> None:
        with pytest.raises(ValueError, match="before start"):
            cal.sessions(date(2024, 6, 10), date(2024, 6, 5))


class TestIsSession:
    @pytest.mark.parametrize(
        ("d", "expected"),
        [
            (date(2024, 1, 1), False),  # New Year's Day
            (date(2024, 1, 2), True),  # Tuesday after NYD
            (date(2024, 1, 15), False),  # MLK Day
            (date(2024, 7, 4), False),  # Independence Day
            (date(2024, 11, 28), False),  # Thanksgiving
            (date(2024, 12, 25), False),  # Christmas Day
            (date(2024, 6, 19), False),  # Juneteenth
            (date(2024, 6, 18), True),  # Tuesday before Juneteenth
            (date(2024, 1, 6), False),  # Saturday
            (date(2024, 1, 7), False),  # Sunday
            (date(2024, 3, 15), True),  # Random Friday
        ],
    )
    def test_known_holidays_and_weekends(
        self, cal: TradingCalendar, d: date, expected: bool
    ) -> None:
        assert cal.is_session(d) is expected


class TestNavigation:
    def test_next_session_skips_weekend(self, cal: TradingCalendar) -> None:
        # Friday March 15, 2024 → Monday March 18
        assert cal.next_session(date(2024, 3, 15)) == date(2024, 3, 18)

    def test_next_session_skips_holiday(self, cal: TradingCalendar) -> None:
        # Friday before MLK weekend → following Tuesday (skip Sat, Sun, MLK Mon)
        assert cal.next_session(date(2024, 1, 12)) == date(2024, 1, 16)

    def test_next_session_from_holiday(self, cal: TradingCalendar) -> None:
        # MLK Day → next trading session
        assert cal.next_session(date(2024, 1, 15)) == date(2024, 1, 16)

    def test_previous_session_skips_weekend(self, cal: TradingCalendar) -> None:
        # Monday March 18, 2024 → previous Friday
        assert cal.previous_session(date(2024, 3, 18)) == date(2024, 3, 15)

    def test_previous_session_skips_holiday(self, cal: TradingCalendar) -> None:
        # Tuesday after MLK → previous Friday
        assert cal.previous_session(date(2024, 1, 16)) == date(2024, 1, 12)

    def test_previous_session_is_strict(self, cal: TradingCalendar) -> None:
        # Strictly before, so a session day returns the prior session.
        assert cal.previous_session(date(2024, 3, 15)) == date(2024, 3, 14)
