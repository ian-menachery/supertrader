"""Unit tests for the corp-actions verifier with mocked yfinance data."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

# Scripts dir is not a package — put it on sys.path so we can import the verifier.
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
import verify_corp_actions as verifier  # noqa: E402


def _splits_series(entries: list[tuple[str, float]]) -> pd.Series:
    """Build a yfinance-shaped splits Series: tz-aware DatetimeIndex -> float."""
    if not entries:
        return pd.Series(dtype="float64")
    ts = pd.to_datetime([d for d, _ in entries], utc=True)
    return pd.Series([r for _, r in entries], index=ts)


@pytest.fixture
def golden_csv(tmp_path: Path) -> Path:
    p = tmp_path / "splits.csv"
    p.write_text(
        "ticker,ex_date,action_type,ratio,note\n"
        "AAPL,2020-08-31,split,4.0,test\n"
        "NVDA,2024-06-10,split,10.0,test\n"
    )
    return p


class TestCheckSplit:
    def test_exact_match(self) -> None:
        row = {"ticker": "AAPL", "ex_date": "2020-08-31", "ratio": "4.0"}
        splits = _splits_series([("2020-08-31", 4.0)])
        passed, detail = verifier.check_split(row, splits)
        assert passed is True
        assert "4.0" in detail

    def test_date_mismatch(self) -> None:
        row = {"ticker": "AAPL", "ex_date": "2020-08-31", "ratio": "4.0"}
        splits = _splits_series([("2021-01-01", 4.0)])
        passed, detail = verifier.check_split(row, splits)
        assert passed is False
        assert "no split" in detail

    def test_ratio_mismatch(self) -> None:
        row = {"ticker": "AAPL", "ex_date": "2020-08-31", "ratio": "4.0"}
        splits = _splits_series([("2020-08-31", 7.0)])
        passed, detail = verifier.check_split(row, splits)
        assert passed is False
        assert "mismatch" in detail

    def test_empty_splits(self) -> None:
        row = {"ticker": "AAPL", "ex_date": "2020-08-31", "ratio": "4.0"}
        passed, detail = verifier.check_split(row, pd.Series(dtype="float64"))
        assert passed is False
        assert "no splits" in detail


class TestVerifyAll:
    def test_all_pass(self, golden_csv: Path) -> None:
        def fake_fetch(ticker: str) -> pd.Series:
            return {
                "AAPL": _splits_series([("2020-08-31", 4.0)]),
                "NVDA": _splits_series([("2024-06-10", 10.0)]),
            }[ticker]

        passed, total, results = verifier.verify_all(golden_csv, fetch_fn=fake_fetch)
        assert passed == 2
        assert total == 2
        assert all(r["status"] == "PASS" for r in results)

    def test_one_fails(self, golden_csv: Path) -> None:
        def fake_fetch(ticker: str) -> pd.Series:
            return {
                "AAPL": _splits_series([("2020-08-31", 4.0)]),
                "NVDA": _splits_series([]),  # missing
            }[ticker]

        passed, total, results = verifier.verify_all(golden_csv, fetch_fn=fake_fetch)
        assert passed == 1
        assert total == 2
        statuses = {r["ticker"]: r["status"] for r in results}
        assert statuses == {"AAPL": "PASS", "NVDA": "FAIL"}


class TestMainCLI:
    def test_passes_at_threshold(
        self, golden_csv: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fake_fetch(ticker: str) -> pd.Series:
            return {
                "AAPL": _splits_series([("2020-08-31", 4.0)]),
                "NVDA": _splits_series([("2024-06-10", 10.0)]),
            }[ticker]

        monkeypatch.setattr(verifier, "fetch_yf_splits", fake_fetch)
        rc = verifier.main(["--golden", str(golden_csv), "--threshold", "1.0"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "HARD GATE: PASS" in captured.out

    def test_fails_below_threshold(
        self, golden_csv: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fake_fetch(_ticker: str) -> pd.Series:
            return _splits_series([])  # both fail

        monkeypatch.setattr(verifier, "fetch_yf_splits", fake_fetch)
        rc = verifier.main(["--golden", str(golden_csv), "--threshold", "0.8"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "HARD GATE: FAIL" in captured.out


@pytest.mark.needs_credentials
def test_live_yfinance_smoke() -> None:
    """Hits real yfinance. Run with RUN_NETWORK_TESTS=1."""
    if os.environ.get("RUN_NETWORK_TESTS") != "1":
        pytest.skip("set RUN_NETWORK_TESTS=1 to run live yfinance smoke")

    golden = Path(__file__).resolve().parents[1] / "golden" / "known_splits.csv"
    passed, total, _results = verifier.verify_all(golden)
    pass_rate = passed / total if total else 0
    assert pass_rate >= 0.8, f"corp-actions hard gate: {passed}/{total} = {pass_rate:.0%}"
