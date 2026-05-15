# ADR 0008 — Paid data sources (Polygon + EODHD)

**Status**: Accepted; execution deferred per 2026-05-14 cost-consciousness review
**Date**: 2026-05-14

## Context

The RSM v1 cycle exposed how much of the result depended on data choices we
hadn't paid for: a 34-ticker static snapshot with known survivorship bias
(ADR 0004 limitation #1, #2), yfinance corporate-action errors that we
patched manually (`scripts/verify_corp_actions.py`), and no
analyst-estimate / earnings-calendar source at all. The pivot to a
trading-system optimization target (open-ended timeline) means data
quality is now the binding constraint, not budget.

Two strategies are in scope per the pivot plan:

- **Form 4 insider clustering** — needs SEC EDGAR (free), no paid data
  required.
- **PEAD** — needs a clean earnings calendar with consensus estimates
  (paid).

Both strategies need a survivorship-aware PIT universe over a long
history (~5+ years). No free source delivers this at acceptable quality.

## Decision

Subscribe to **two** paid data providers:

| provider | plan | ~cost | what it provides |
| -------- | ---- | -----:| ---------------- |
| Polygon  | Stocks Starter | ~$30/mo | US equity OHLCV (5+ yr history), corporate actions, reference data, earnings calendar with consensus estimates |
| EODHD    | All-World basic / fundamentals add-on | ~$20/mo | PIT historical constituents for S&P 500 / Russell 1000 / Russell 3000 |

Total recurring cost: **~$50/mo**.

Justification for paying *both* despite overlap:

- Polygon doesn't expose historical-index-constituents membership at
  Starter tier. EODHD is the cheapest source that does. The
  survivorship-aware universe (limitation #1, #2) is the single
  highest-EV data-quality fix; not buying it would force a multi-week
  EDGAR-build alternative and still leave gaps.
- Polygon's earnings data is closer to broker-grade than EODHD's;
  PEAD is sensitive to this.
- Two providers reduce single-vendor risk if either becomes unreliable.

## Out of scope (deliberately NOT bought)

- **Intraday data.** Daily frequency is the platform target.
- **Options data.** No options strategies in the current roadmap.
- **Earnings transcripts.** NLP on transcripts is a future expansion if
  PEAD warrants enrichment.
- **Fundamentals beyond earnings** (P/E ratios, segment data, etc.).
  Future strategies might want fundamentals; revisit then.
- **Alternative data** (Glassdoor, satellite, credit-card panels). No
  budget signal that any of these would change a backtest verdict.

## Kill-switch criteria

Each subscription is reviewed quarterly. Cancel if:

- After 6 months: no v2 strategy has shipped a positive holdout result
  AND no v3 strategy is queued that requires the data.
- Provider data quality regresses: documented data-error rate > 1% on
  spot checks, or an outage > 1 week.
- Cheaper equivalent emerges (CRSP academic access via a future
  affiliation; Polygon ships PIT constituents; etc.).

## Operational

- API keys stored in `.env` (gitignored) as `POLYGON_API_KEY` and
  `EODHD_API_KEY`. Never committed, never logged.
- Both sources implement the `DataSource` protocol so the pipeline
  doesn't depend on the vendor — switching out either is a config
  change.
- Rate limits are vendor-specific; the streaming-ingest pattern from
  `reddit_arctic_shift.py:fetch_months` is the model for resumable
  per-ticker / per-month backfills.

## Consequences

- The "research-only" license (`LICENSE`) is unaffected; the paid data
  has its own terms which restrict redistribution. The data itself is
  not committed, only derived parquet partitions under
  `data/store/{polygon,eodhd}/` (already gitignored under `/data/`).
- README "Status" section will name Polygon + EODHD as the v2 data
  baseline once subscriptions are active.
- Future ADRs may extend the stack (e.g., spread data for the cost model
  upgrade in ADR 0010) but those are scope additions, not provider swaps.

## References

- ADR 0001 — vectorbt engine choice (no change).
- ADR 0004 — static universe v1 (now superseded for backtests by ADR 0012).
- ADR 0007 — universe upgrade path (this ADR fulfills trigger A's "spend
  $20/mo on EODHD" branch and supersedes the open question).
- `docs/known-limitations.md` #1, #2 — closed by this ADR + ADR 0012.

## Execution deferred — 2026-05-14 cost-consciousness review

The provider choices above stay accepted in principle. Neither
subscription has been activated.

### Why deferred

Re-examination after the rsm_v1 negative verdict + the Form 4 data
discovery (`~/projects/redline` had 170 rows / 5 issuers, insufficient
for a cross-sectional study): every plausible next-strategy faces a
data-quality vs cash tradeoff *before* any v2 verdict has earned the
spend. The disciplined move is to gate paid-data activation on a
strategy result that says "the data quality is now the binding
constraint."

### Trigger criteria

Subscribe to **Polygon (~$30/mo)** if all true:
- A v2 strategy produces test Sharpe ≥ 0.5 at 1× cost AND ≥ 0.3 at 2×
  cost on free yfinance data.
- A second strategy idea (PEAD, or a composite) is queued and needs
  the data Polygon provides (earnings calendar with consensus
  estimates, level-1 spread for cost-model v2, etc.).

Subscribe to **EODHD (~$20/mo)** if all true:
- A v2 strategy has produced a positive holdout result OR shown a
  consistent positive Sharpe across multiple test-window slices on
  free data.
- The intent is to re-run on a survivorship-bias-free PIT universe
  before any paper-trading commitment.

Either subscription cancels at any quarterly review if the gating
condition reverses.

### Current state

- `Form4RedlineSource`, `PolygonPricesSource`, `EODHDUniverseSource`,
  `PolygonEarningsSource` stubs all stay as `NotImplementedError`.
- `PITUniverse.from_eodhd_store` raises `NotImplementedError` until
  EODHD is active.
- The platform runs entirely on free data (yfinance + WSB historical
  on disk + static universe).
- ADR 0010's cost-model v2 work likewise stays deferred until a paid
  data path activates.
