"""Universe loader. v1 is a static snapshot CSV with explicit survivorship-bias liability.

See `docs/adr/0004-static-universe-v1.md` for the rationale and upgrade triggers.

The CSV schema is::

    ticker, name, sector, market_cap_usd, adv_usd

`StaticUniverse` provides filtering by market cap, average daily dollar volume,
sector exclusion, and an explicit exclude list. The filtered ticker list is
deterministic for a given config — the same filter on the same snapshot returns
the same set, sorted.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supertrader.config.schemas import UniverseConfig


# Standard liability text printed atop every backtest tear sheet. Updating it
# here keeps the wording single-source — ADR 0004 mandates that survivorship
# bias is acknowledged in writing on every run output. See the upgrade path in
# ADR 0007 (EODHD subscription or EDGAR-built PIT universe).
SURVIVORSHIP_WARNING = (
    "WARNING: Universe is a post-hoc Russell 1000 snapshot. "
    "Results overstate returns by ~1-3% annually vs. true PIT. "
    "Acceptable for research; upgrade to EODHD or EDGAR-built PIT when "
    "test Sharpe > 0.8."
)


@dataclass(frozen=True, slots=True)
class UniverseEntry:
    """One row in the universe snapshot."""

    ticker: str
    name: str
    sector: str
    market_cap_usd: float
    adv_usd: float


class StaticUniverse:
    """A point-in-time snapshot of tradeable tickers, loaded from CSV.

    Survivorship bias is acknowledged — see ADR 0004. For v1, the snapshot is
    a single CSV checked into the repo. Future iterations may build PIT
    universes from SEC filings or subscribe to an external PIT provider.
    """

    def __init__(self, entries: list[UniverseEntry]) -> None:
        if not entries:
            msg = "StaticUniverse requires at least one entry"
            raise ValueError(msg)
        # Ensure tickers unique — duplicates indicate a corrupt snapshot.
        seen: set[str] = set()
        for e in entries:
            if e.ticker in seen:
                msg = f"Duplicate ticker '{e.ticker}' in universe snapshot"
                raise ValueError(msg)
            seen.add(e.ticker)
        self._entries: tuple[UniverseEntry, ...] = tuple(entries)

    @classmethod
    def from_csv(cls, path: Path | str) -> StaticUniverse:
        """Load a snapshot CSV. Required columns: ticker, name, sector, market_cap_usd, adv_usd."""
        p = Path(path)
        if not p.exists():
            msg = f"Universe snapshot not found at {p}"
            raise FileNotFoundError(msg)
        entries: list[UniverseEntry] = []
        with p.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            expected = {"ticker", "name", "sector", "market_cap_usd", "adv_usd"}
            actual = set(reader.fieldnames or ())
            missing = expected - actual
            if missing:
                msg = f"Universe CSV {p} missing columns: {sorted(missing)}"
                raise ValueError(msg)
            for row in reader:
                entries.append(
                    UniverseEntry(
                        ticker=row["ticker"].strip().upper(),
                        name=row["name"].strip(),
                        sector=row["sector"].strip(),
                        market_cap_usd=float(row["market_cap_usd"]),
                        adv_usd=float(row["adv_usd"]),
                    )
                )
        return cls(entries)

    @classmethod
    def from_config(cls, cfg: UniverseConfig, *, default_path: Path | None = None) -> StaticUniverse:
        """Build a `StaticUniverse` from a `UniverseConfig`, applying its filters.

        If `cfg.snapshot_path` is set, it's used. Otherwise `default_path` is
        required. The filters from the config (market-cap band, ADV floor,
        exclude list) are applied to the loaded snapshot.
        """
        if cfg.type != "static":
            msg = f"StaticUniverse only supports type='static', got '{cfg.type}'"
            raise ValueError(msg)
        path = cfg.snapshot_path or default_path
        if path is None:
            msg = "Either UniverseConfig.snapshot_path or default_path must be provided"
            raise ValueError(msg)
        full = cls.from_csv(path)
        return full.filter(
            min_market_cap_usd=cfg.min_market_cap_usd,
            max_market_cap_usd=cfg.max_market_cap_usd,
            min_adv_usd=cfg.min_adv_usd,
            exclude=set(cfg.exclude_tickers),
        )

    def filter(
        self,
        *,
        min_market_cap_usd: float | None = None,
        max_market_cap_usd: float | None = None,
        min_adv_usd: float | None = None,
        exclude: set[str] | None = None,
        sectors: set[str] | None = None,
    ) -> StaticUniverse:
        """Return a new `StaticUniverse` with the filter predicates applied."""
        excl = exclude or set()
        out: list[UniverseEntry] = []
        for e in self._entries:
            if e.ticker in excl:
                continue
            if min_market_cap_usd is not None and e.market_cap_usd < min_market_cap_usd:
                continue
            if max_market_cap_usd is not None and e.market_cap_usd > max_market_cap_usd:
                continue
            if min_adv_usd is not None and e.adv_usd < min_adv_usd:
                continue
            if sectors is not None and e.sector not in sectors:
                continue
            out.append(e)
        if not out:
            msg = (
                "Universe filter produced an empty set. Check market-cap band, "
                "ADV floor, and exclude list."
            )
            raise ValueError(msg)
        return StaticUniverse(out)

    def tickers(self) -> list[str]:
        """Return tickers sorted lexicographically."""
        return sorted(e.ticker for e in self._entries)

    def entries(self) -> list[UniverseEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, ticker: object) -> bool:
        return isinstance(ticker, str) and any(e.ticker == ticker for e in self._entries)
