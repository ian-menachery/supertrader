# ADR 0004 — Static universe snapshot (v1)

**Status**: Accepted
**Date**: 2026-05-14

## Context

Backtests need a list of tradeable tickers per as-of date. A *correct*
point-in-time (PIT) universe — which knows that BBBY was tradeable in 2021
but delisted in 2023, and reports BBBY's 2021 market cap when asked — is hard
to assemble from free data sources.

Free options surveyed:
- **yfinance** doesn't return delisted tickers; querying them silently yields
  empty data, which is survivorship bias by omission.
- **Wikipedia/iShares ETF holdings** give a current snapshot only.
- **SEC EDGAR** has the truth — every 10-K filing names the issuer with their
  CIK at that point in time — but assembling a PIT mapping is a project
  unto itself (~1 week of focused work).
- **EODHD** has true PIT data for $20/mo.

## Decision

**Ship a static snapshot CSV checked into the repo** (`configs/universe/snapshot_*.csv`).
Every backtest tear sheet prints the survivorship-bias warning prominently.
This is a research tool, not a portfolio-management tool.

CSV schema: `ticker, name, sector, market_cap_usd, adv_usd`.

The v1 snapshot covers 34 well-known liquid US equities chosen for sector
diversity and for being representative of the WSB/retail conversation universe.
It is not comprehensive. It is just enough to validate the framework end-to-end.

## Consequences

- Backtest results overstate true PIT returns by ~1–3% per year for medium
  horizons (empirical estimate from quant literature on small/mid-cap
  survivorship). Recorded on every tear sheet.
- Adding a ticker means editing the CSV and bumping the snapshot date in the
  filename. Old snapshots stay checked in for reproducibility.
- No ticker is ever "delisted" from this universe — once added, always present.

## Upgrade triggers (when to spend money or time)

**Trigger A — research signal**: Any v1 backtest produces a Sharpe > 0.8 on
the *test* period (pre-holdout). This is "interesting enough to take
seriously" — at that point spend $20/mo on EODHD or commit ~1 week to build
PIT from EDGAR. Re-run the backtest on the proper universe before touching
the holdout.

**Trigger B — universe drift**: The snapshot is more than 12 months old
and the live paper-trading positions drift materially from the universe.
Refresh the snapshot.

**Trigger C — recruiting**: This project is on a résumé and a question gets
asked about survivorship bias. Have a real PIT upgrade ready to discuss.

## Path to PIT

1. **EODHD subscription** — $20/mo, minimum disruption. Replace
   `StaticUniverse.from_csv` with `EODHDUniverse.from_api`. Same interface.
2. **Build from EDGAR** — script that reads 10-K filings from `~/projects/redline`,
   extracts issuer name + ticker + report date + reported market cap, builds
   `universe_snapshots` SQLite table indexed by `as_of_date`. Free but
   ~1 week of work.

Either path requires re-running the full backtest after the universe changes.
The holdout-touch counter (`HoldoutGuard`, Week 4) tracks this.
