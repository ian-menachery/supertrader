"""Tests for `data.universe.StaticUniverse` — loading, filtering, ADR 0004 invariants."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from supertrader.config.schemas import UniverseConfig
from supertrader.data.universe import StaticUniverse, UniverseEntry


def _write_csv(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    return _write_csv(
        tmp_path / "u.csv",
        """
        ticker,name,sector,market_cap_usd,adv_usd
        AAPL,Apple,Technology,3500000000000,15000000000
        F,Ford,Consumer Cyclical,48000000000,800000000
        PLTR,Palantir,Technology,55000000000,1800000000
        GME,GameStop,Consumer Cyclical,7000000000,400000000
        SMOL,Small Cap,Healthcare,400000000,3000000
        """,
    )


class TestLoading:
    def test_from_csv_loads_all_rows(self, sample_csv: Path) -> None:
        u = StaticUniverse.from_csv(sample_csv)
        assert len(u) == 5
        assert "AAPL" in u
        assert "DOES_NOT_EXIST" not in u

    def test_from_csv_uppercases_tickers(self, tmp_path: Path) -> None:
        path = _write_csv(
            tmp_path / "u.csv",
            "ticker,name,sector,market_cap_usd,adv_usd\naapl,Apple,Tech,1e12,1e9\n",
        )
        u = StaticUniverse.from_csv(path)
        assert u.tickers() == ["AAPL"]

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            StaticUniverse.from_csv(tmp_path / "nope.csv")

    def test_missing_columns_raise(self, tmp_path: Path) -> None:
        path = _write_csv(tmp_path / "u.csv", "ticker,name\nAAPL,Apple\n")
        with pytest.raises(ValueError, match="missing columns"):
            StaticUniverse.from_csv(path)

    def test_empty_constructor_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            StaticUniverse([])

    def test_duplicate_ticker_raises(self) -> None:
        e1 = UniverseEntry("AAPL", "Apple", "Tech", 1e12, 1e9)
        e2 = UniverseEntry("AAPL", "Apple Two", "Tech", 1e12, 1e9)
        with pytest.raises(ValueError, match="Duplicate ticker"):
            StaticUniverse([e1, e2])


class TestFiltering:
    def test_max_market_cap_excludes_mega(self, sample_csv: Path) -> None:
        u = StaticUniverse.from_csv(sample_csv).filter(max_market_cap_usd=100_000_000_000)
        assert "AAPL" not in u.tickers()  # 3.5T excluded
        assert "F" in u.tickers()
        assert "PLTR" in u.tickers()
        assert "GME" in u.tickers()

    def test_min_market_cap_excludes_small(self, sample_csv: Path) -> None:
        u = StaticUniverse.from_csv(sample_csv).filter(min_market_cap_usd=1_000_000_000)
        assert "SMOL" not in u.tickers()

    def test_min_adv_excludes_illiquid(self, sample_csv: Path) -> None:
        # GME has ADV 4e8, threshold 5e8 → excluded. SMOL ADV 3e6 → excluded.
        u = StaticUniverse.from_csv(sample_csv).filter(min_adv_usd=500_000_000)
        assert "SMOL" not in u.tickers()
        assert "GME" not in u.tickers()
        assert "F" in u.tickers()  # 800M ADV survives

    def test_exclude_list(self, sample_csv: Path) -> None:
        u = StaticUniverse.from_csv(sample_csv).filter(exclude={"AAPL", "PLTR"})
        assert "AAPL" not in u.tickers()
        assert "PLTR" not in u.tickers()
        assert "F" in u.tickers()

    def test_sector_filter(self, sample_csv: Path) -> None:
        u = StaticUniverse.from_csv(sample_csv).filter(sectors={"Technology"})
        assert set(u.tickers()) == {"AAPL", "PLTR"}

    def test_empty_filter_result_raises(self, sample_csv: Path) -> None:
        with pytest.raises(ValueError, match="empty set"):
            StaticUniverse.from_csv(sample_csv).filter(min_market_cap_usd=1e20)


class TestFromConfig:
    def test_uses_default_path_when_config_omits_it(self, sample_csv: Path) -> None:
        cfg = UniverseConfig(
            type="static",
            max_market_cap_usd=100_000_000_000,
            min_market_cap_usd=1_000_000_000,
        )
        u = StaticUniverse.from_config(cfg, default_path=sample_csv)
        assert set(u.tickers()) == {"F", "PLTR", "GME"}

    def test_unsupported_type_raises(self) -> None:
        cfg = UniverseConfig(type="pit")
        with pytest.raises(ValueError, match="only supports type='static'"):
            StaticUniverse.from_config(cfg, default_path=Path("x.csv"))

    def test_no_path_raises(self) -> None:
        cfg = UniverseConfig(type="static")
        with pytest.raises(ValueError, match="snapshot_path or default_path"):
            StaticUniverse.from_config(cfg)


class TestRealSnapshot:
    """Loads the actual configs/universe/snapshot_2026_05_14.csv."""

    def test_repo_snapshot_loads(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / "configs" / "universe" / "snapshot_2026_05_14.csv"
        u = StaticUniverse.from_csv(path)
        assert len(u) >= 30
        # Spot-check: AAPL is in, has a multi-trillion market cap
        assert "AAPL" in u
        aapl = next(e for e in u.entries() if e.ticker == "AAPL")
        assert aapl.market_cap_usd > 1e12
