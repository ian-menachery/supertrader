"""Universe loaders. Static CSV snapshot OR PIT panel from a constituents source.

`StaticUniverse` (the original) is a hand-curated snapshot CSV with explicit
survivorship-bias liability — see ADR 0004 + `docs/known-limitations.md` #1+#2.

`PITUniverse` (added per ADR 0012) consumes a `(date, ticker, included)` panel
written by `EODHDUniverseSource` and exposes per-date constituent membership.
The pivot to a trading-system optimization target requires this — every v2
backtest uses a PIT universe.

Both implement the minimal `Universe` protocol:

  * `tickers(as_of: date | None = None) -> list[str]` — sorted ticker list
  * `__contains__(ticker: str) -> bool`
  * `snapshot_hash() -> str` — Blake2b digest of the panel contents, fed
    into `RunManifest.universe_snapshot_hash` so reproducibility checks
    can detect a vendor-side panel revision.

The CSV schema is::

    ticker, name, sector, market_cap_usd, adv_usd

`StaticUniverse` provides filtering by market cap, average daily dollar
volume, sector exclusion, and an explicit exclude list. The filtered
ticker list is deterministic for a given config — the same filter on
the same snapshot returns the same set, sorted.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import polars as pl

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


@runtime_checkable
class Universe(Protocol):
    """Read-only API for what tickers were tradeable on a given date.

    Implementations:
      * `StaticUniverse` — ignores `as_of`; returns the snapshot set.
      * `PITUniverse` — returns the constituents at `as_of` from a panel.
    """

    def tickers(self, as_of: _date | None = None) -> list[str]: ...

    def __contains__(self, ticker: object) -> bool: ...

    def snapshot_hash(self) -> str:
        """Blake2b digest of the universe content. Fed into RunManifest."""
        ...


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

    def tickers(self, as_of: _date | None = None) -> list[str]:
        """Return tickers sorted lexicographically.

        `as_of` is accepted for `Universe` protocol compatibility but ignored —
        a static snapshot returns the same set on every date. Per ADR 0004,
        this is the survivorship-biased path; use `PITUniverse` for honest
        backtests.
        """
        del as_of  # ignored; static universe is not date-aware by design
        return sorted(e.ticker for e in self._entries)

    def entries(self) -> list[UniverseEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, ticker: object) -> bool:
        return isinstance(ticker, str) and any(e.ticker == ticker for e in self._entries)

    def snapshot_hash(self) -> str:
        """Blake2b hex digest of the sorted ticker list. Stable across runs."""
        payload = json.dumps(self.tickers(), sort_keys=True).encode()
        return hashlib.blake2b(payload, digest_size=16).hexdigest()


class PITUniverse:
    """Point-in-time universe backed by a `(date, ticker, included)` panel.

    Per ADR 0012, this is the structural fix for `docs/known-limitations.md`
    #1 + #2. A PIT panel records which tickers were constituents on every
    date; `tickers(as_of)` returns the membership set for that date.

    Phase B of the trading-system pivot will load the panel from
    `EODHDUniverseSource` output. This skeleton accepts a polars DataFrame
    directly so test fixtures can build small panels in-memory without
    hitting a store.

    Expected panel schema (columns in any order, sorted internally):

      * `date` (Date) — calendar date the membership row applies to
      * `ticker` (Utf8) — ticker symbol
      * `included` (Boolean) — True iff the ticker was a constituent on that day

    Universe panel can also encode adds/deletes sparsely (only when
    membership changes); densification is the caller's responsibility for
    now — Phase B may move it into the source.
    """

    def __init__(self, panel: pl.DataFrame, *, label: str | None = None) -> None:
        # Local import to keep the data-layer namespace clean of polars at
        # module load time (matches the pattern elsewhere).
        import polars as pl  # noqa: PLC0415

        required = {"date", "ticker", "included"}
        missing = required - set(panel.columns)
        if missing:
            msg = f"PITUniverse panel missing required columns: {sorted(missing)}"
            raise ValueError(msg)
        if panel.is_empty():
            msg = "PITUniverse panel is empty; cannot construct a universe with no rows"
            raise ValueError(msg)
        # Normalize to a sorted, schema-coerced frame for determinism.
        self._panel: pl.DataFrame = panel.select(["date", "ticker", "included"]).sort(
            ["date", "ticker"]
        )
        self.label: str = label or "pit-universe"

    @classmethod
    def from_eodhd_store(cls, store_root: Path, index: str) -> PITUniverse:
        """Load a PIT panel from `eodhd.universe` partitions under store_root.

        Phase-B stub: validates inputs but raises NotImplementedError until
        the actual EODHD source has been backfilled.
        """
        if index not in {"sp500", "russell1000", "russell3000"}:
            msg = f"unknown index {index!r}; expected sp500 / russell1000 / russell3000"
            raise ValueError(msg)
        del store_root
        msg = (
            "PITUniverse.from_eodhd_store is not yet implemented. Phase B of "
            "the trading-system pivot wires this against EODHDUniverseSource."
        )
        raise NotImplementedError(msg)

    def tickers(self, as_of: _date | None = None) -> list[str]:
        """Return constituents as of `as_of` (default: latest panel date)."""
        import polars as pl  # noqa: PLC0415

        target = as_of if as_of is not None else self._panel["date"].max()
        if target is None:
            return []
        included = (
            self._panel.filter(pl.col("date") == target)
            .filter(pl.col("included"))
            .select("ticker")
            .to_series()
            .to_list()
        )
        return sorted(included)

    def __contains__(self, ticker: object) -> bool:
        if not isinstance(ticker, str):
            return False
        return ticker in set(self._panel["ticker"].to_list())

    def __len__(self) -> int:
        return int(self._panel["ticker"].n_unique())

    def snapshot_hash(self) -> str:
        """Blake2b hex of a deterministic serialization of the panel.

        Stability requires (date, ticker)-sorted rows. The hash captures
        any vendor-side revision to historical constituents.
        """
        payload = self._panel.write_csv().encode()
        return hashlib.blake2b(payload, digest_size=16).hexdigest()
