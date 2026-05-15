# ADR 0010 — Cost model v2

**Status**: Accepted
**Date**: 2026-05-14

## Context

`docs/known-limitations.md` #3 documented that the v1 cost model
likely understates real round-trip costs by 50-200%. The model uses
a flat `slippage_bps_base` with a `slippage_impact_coeff_bps`
multiplier that's only loosely tied to actual order/ADV impact, and
applies no per-ticker spread information at all. For small-cap
strategies (which is most of what the pivot targets) this systematically
makes the strategy look more tradeable than it would be in production.

The RSM v1 cost-sensitivity sweep (1× / 2× / 3× slippage) showed that
the strategy's apparent edge collapsed as costs scaled, suggesting the
1× number was probably already too generous. With the trading-system
pivot, the cost model needs to converge toward "what would actually
trade" — calibrated when possible, conservative otherwise.

## Decision

Replace `slippage_bps_base` + `slippage_impact_coeff_bps` with a
two-component model:

```
total_slippage_bps(order, ticker, date) =
    half_spread_bps(ticker, date)
  + impact_coeff_bps * sqrt(order_notional / ADV(ticker, date))
```

Where:

- `half_spread_bps(ticker, date)` is the per-ticker, per-day half-spread
  pulled from a spread data panel if available. Polygon's Stocks Starter
  doesn't directly expose end-of-day spread, but the daily high/low and
  volume let us approximate it; pull the formal spread later if a higher
  Polygon tier is acquired. Fallback when no spread data: a flat default
  of 5 bps for liquid names, 15 bps for illiquid (parameterized via
  `CostsConfig`).
- `impact_coeff_bps` is a single parameter (default 10 bps) representing
  the cost of trading 100% of ADV in one bar. The square-root model is
  industry-standard for small-to-mid orders.
- `ADV(ticker, date)` is the trailing 20-day average daily dollar volume,
  computed from the same OHLCV store the strategy uses.

Commission stays as a separate, additive cost (currently 1 bps; that
covers wire-house retail and is realistic for Alpaca).

## Implementation outline

1. **`CostsConfig` schema additions:**
   ```python
   class CostsConfig(StrictModel):
       commission_bps: float = 1.0
       # v2 fields (new):
       half_spread_bps_liquid: float = 5.0
       half_spread_bps_illiquid: float = 15.0
       impact_coeff_bps: float = 10.0
       # v1 fields kept for backwards compat with rsm_v1 configs:
       slippage_bps_base: float = 3.0
       slippage_impact_coeff_bps: float = 10.0
   ```

2. **`src/supertrader/backtest/costs.py`** gains:
   ```python
   def half_spread_bps(ticker: str, as_of: date, costs: CostsConfig,
                      spread_panel: Optional[pd.DataFrame] = None) -> float
   def impact_bps(order_notional: float, adv: float, costs: CostsConfig) -> float
   def total_slippage_bps(order: Order, costs: CostsConfig, ...) -> float
   ```

3. **Engine wiring:** vectorbt's `slippage` parameter accepts a per-cell
   matrix. Today we pass a scalar. The change is to build the slippage
   matrix per-(date, ticker) from the new helpers, sized by
   target-weight deltas.

4. **Per-ticker liquidity classification** (liquid vs illiquid for the
   half-spread fallback): a one-line heuristic based on 20-day ADV.
   Names with ADV > $20M USD are "liquid." Justified by Alpaca's own
   marketability cutoff.

## Calibration

The model defaults are intentionally conservative. Once paper trading
runs (post-positive-holdout), every realized fill is logged with both
the simulated total-cost and the realized fill price. After 50 trades,
fit `impact_coeff_bps` to the empirical slippage distribution and
update `CostsConfig` defaults. Documented as a follow-up plan; not
gating any v2 strategy decision.

## Backwards compatibility

- The v1 cost fields (`slippage_bps_base`, `slippage_impact_coeff_bps`)
  remain on `CostsConfig` and are still honored by the v1 cost-model
  code path. This is the only place we keep legacy fields — required so
  rsm_v1 configs continue loading unchanged.
- A new field `costs.model_version: "v1" | "v2"` defaults to `"v2"`.
  rsm_v1 configs pin `"v1"` explicitly to keep their historical numbers
  reproducible.

## Migration path

1. Land the v2 model code with `model_version` defaulting to `"v2"`.
2. Re-run rsm_v1's three cost variants (1× / 2× / 3×) under
   `model_version: "v1"` to confirm bit-for-bit reproducibility. Pin
   `"v1"` in `configs/runs/rsm_v1_backtest*.yaml`.
3. Re-run the same configs under `model_version: "v2"` and document the
   delta in `docs/postmortem/rsm-v1.md` as a follow-up entry. Expectation:
   the v1's "1× cost" Sharpe lands somewhere between today's v1 1× and 2×
   numbers.
4. New configs (v2a Form 4, v2b PEAD) default to `model_version: "v2"`.

## Out of scope

- Per-ticker borrow rates beyond the existing `borrow_bps_annual` and
  `hard_to_borrow_bps_annual`. The v1 model handles borrow correctly;
  no change needed.
- Market-impact for orders > 1% of ADV. The square-root model
  understates extreme-size impact; cap order sizing in the strategy
  layer (`scale_to_gross` already provides this) rather than complicating
  the cost model.
- Asymmetric buy/sell slippage. Real markets are asymmetric (you eat the
  offer, lift the bid); modeled cost is symmetric. Tradeoff: cleaner
  math, slight overestimate on sells, slight underestimate on buys.
  Documented limitation.

## References

- `src/supertrader/backtest/costs.py` — module being upgraded.
- `src/supertrader/config/schemas.py:CostsConfig` — schema being
  extended.
- `docs/known-limitations.md` #3 — the gap being closed.
- ADR 0008 — Polygon subscription that funds the spread data.
- Almgren et al. (2005), "Direct Estimation of Equity Market Impact" —
  the canonical source for the square-root impact model.
