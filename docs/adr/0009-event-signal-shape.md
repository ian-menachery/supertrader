# ADR 0009 — Event-driven signal shape

**Status**: Accepted
**Date**: 2026-05-14

## Context

The v2 strategies (Form 4 insider clustering, PEAD) are event-driven —
their inputs aren't a continuous per-day score but discrete events that
occur on specific dates (an insider transaction, an earnings
announcement). The existing `Signal.compute` contract returns a
`(date × ticker) → float` panel — one row per trading day, one column
per ticker.

Two ways to fit events into this contract:

- **(a) Widen the protocol** to support sparse event tuples directly.
  A new `EventSignal` base class would return something like
  `list[Event(date, ticker, payload)]`. The strategy layer learns to
  consume both panel and event signals; the engine layer learns to
  handle variable position counts driven by event timing.
- **(b) Project events onto the existing panel shape** with NaN
  everywhere except event dates. The `Signal` contract is unchanged;
  the engine and strategy layers see a panel like any other (just
  sparse).

## Decision

**Project events onto the existing `(date × ticker)` panel shape.**

Concretely: a Form 4 cluster signal computes, for each (date, ticker), a
"cluster score" if there was a relevant cluster of insider buys in the
trailing N days, else NaN. A PEAD SUE signal places the standardized
unexpected earnings value on the announcement date and NaN everywhere
else.

The downstream strategy is responsible for interpreting NaN as "no
event" and translating the score-on-event-date into a holding-period
trade. That logic lives in a new `EventDrivenStrategy` base class
(Phase D in the pivot plan).

## Justification

- **No protocol churn.** Every existing component (`Signal`,
  `PointInTimeStore`, `Strategy`, the vectorbt engine, the tear-sheet
  renderer, the cache fingerprinting) keeps working unchanged. The
  alternative would push protocol changes through 6+ modules and at
  least 50 tests.
- **Engine compatibility.** vectorbt operates on rectangular panels;
  pushing event tuples down to the engine would require a parallel
  code path or a translation layer. The panel projection IS that
  translation, done once at the signal output.
- **Cache and storage.** Signal output is already cached as Parquet
  partitions keyed by signal fingerprint + date range. Panels store
  trivially; event tuples would need a separate caching scheme.
- **Determinism.** NaN-vs-value is a well-defined contract. "Event
  tuple list ordering" is implicit and easy to break.

## Trade-offs (documented loudly)

- **Memory.** A 600-ticker universe × 1500 trading days × 8 bytes per
  float64 = ~7 MB per signal panel. Even with 99% NaN density that's
  unchanged — Polars stores nulls compactly but pandas doesn't. The
  fix if this becomes a real cost: use polars at the signal-to-
  strategy boundary instead of pandas. Not a blocker today.
- **Sparsity-aware aggregations are awkward.** Computing "average SUE
  over last 4 quarters" requires masking NaN explicitly. Strategy code
  that forgets to mask gets NaN propagation. Pin via a unit test per
  event strategy.
- **Event-day-only metrics need slicing.** The tear sheet currently
  computes returns/Sharpe over all panel days. For event strategies
  the relevant statistic is "Sharpe on days when *any* event-driven
  position was held" — but the existing per-day return series already
  reflects this naturally (zero-weight days contribute zero return),
  so we keep the existing metric definitions. Document in each event
  postmortem that "low gross exposure" days are normal, not a bug.

## Implementation outline

For each event-driven signal:

1. Compute per-day signal value (NaN where no event).
2. Optionally compute a "carry-forward score" that holds the most recent
   event value for the configured holding period — useful for cleaner
   ranking but optional.
3. Output a standard `(date × ticker)` DataFrame; pipe into the existing
   `Strategy.target_positions` flow.

For each event-driven strategy (new `EventDrivenStrategy`):

1. On each date, look at the signal value AND the lookback window for
   that ticker.
2. If event date == today AND signal > threshold: enter position.
3. If holding-period clock has reached `H` days since entry: exit.
4. If position cap exceeded: do not enter new positions, exit oldest.
5. `scale_to_gross` handles zero-position days as identity.

## Out of scope

- Intra-day events.
- Multi-event composite signals (e.g., earnings + insider together) —
  use `CompositeSignal` later if needed.
- Variable-holding-period exits driven by signal decay rather than the
  clock.

## References

- `src/supertrader/signals/base.py:Signal` — protocol that stays
  unchanged.
- `src/supertrader/strategies/mean_reversion.py` — the model for how
  panels feed strategies; the new `EventDrivenStrategy` follows the
  same pattern.
- Pivot plan, Phase C (event-driven strategy layer).
