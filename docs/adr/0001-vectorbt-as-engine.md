# ADR 0001 — vectorbt as the backtest engine

**Status**: Accepted
**Date**: 2026-05-14

## Context

We need a backtest engine for a single-user, daily-bar US equities platform. Goals:
clear cost/slippage modeling, deterministic outputs, ergonomic API, no full-blown
event-driven simulator overhead.

## Options considered

1. **vectorbt** — vectorized, pandas-native, good cost models, mature.
2. **backtrader** — event-driven, slow for daily-bar universe sweeps, opinionated.
3. **zipline** — abandoned (Quantopian dead), maintenance burden.
4. **Custom DIY** — too much yak-shaving for 8 weeks; reinventing portfolio
   accounting is not the point of this project.

## Decision

vectorbt (free version, not pro). We use `Portfolio.from_orders` only — the
indicator helpers and signal helpers are bypassed in favor of our own `Signal`
abstraction.

## Consequences

- Strategy → backtest boundary uses pandas DataFrames (vectorbt requires it).
  Polars elsewhere; convert at this boundary.
- Survivorship-bias and PIT-universe concerns remain ours to handle (engine
  doesn't know about delistings).
- vectorbt-pro features (multi-asset constraints, integer position sizing) are
  off-limits unless we pay; this is fine for v1.
