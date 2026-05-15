# Postmortem — v2 technical signals on SP500

Three signals, three documented negative results. Full numbers and
multi-comparison accounting in `docs/verdicts/v2-tech-comparison.md`;
this file captures *what we learned* and *what changes next time*.

## What was tested

Three cross-sectional technical signals on a broader SP500 universe
(~500 names), 2018-01-02 → 2024-12-30 with a 5-year train, 1-year
test, 1-year holdout split (holdout untouched).

- **Cross-sectional momentum** (Jegadeesh-Titman 12-1) → 12-month
  trailing return excluding the most recent month, long top decile /
  short bottom.
- **Z-score reversal** → 5-day return z-score against a 20-day rolling
  mean/std, long bottom decile / short top.
- **Volume surge** → event-driven score on days with abnormal volume +
  positive return, long top decile / short bottom (the latter mostly
  empty since events are sparse).

All three ran through the same `MeanReversionStrategy` cross-sectional
ranker with `direction` set appropriately. No data changes between
runs — same universe, same yfinance store, same v1 cost model.

## Findings

### F1. Momentum was not the historical strategy on this window

Train Sharpe -0.06 is anomalous against the historical 12-1 momentum
literature (Sharpe ~0.5-1.0 over multi-decade samples). The likely
explanation is the test window: 2018-2024 contains the March 2020
COVID crash, the January 2021 GameStop / meme-stock rally (which
inverted normal momentum/anti-momentum dynamics), and the late 2022
factor-rotation regime.

A longer history (e.g., 1970-2024) would smooth these out; the
practical implication is that "test on 6 years" is too narrow a window
for a slow-turnover momentum signal that depends on aggregate
risk-premium harvesting.

### F2. Z-score reversal is broken on large-cap SP500

Train Sharpe -2.05 and test Sharpe -3.01 are not "the strategy didn't
work" — they're "the strategy is systematically negative." Going long
today's biggest losers and short today's biggest gainers on SP500
large caps in a generally trending market is mechanically fighting the
trend.

Short-term reversal is a real effect in academic literature, but
mostly on small-caps and outside SP500's high-liquidity regime. The
documented effect of Lehmann (1990) and Lo & MacKinlay (1990) is in
the 1-5 bps/day range on average and is heavily concentrated in
specific subuniverses. Our SP500 universe is the wrong target for
this signal.

If revisited, the direction should probably flip (momentum on
short-term returns) AND/OR the universe should narrow to small/mid
caps where the effect is documented. Neither change is in scope here.

### F3. Volume surge looks best because of beta, not edge

Test Sharpe +0.89 looks promising at first glance. Five problems with
that read:

1. **Train Sharpe is negative** (-0.67). The same anti-generalization
   pattern from rsm_v1. Per CLAUDE.md's lessons-learned rule, that's
   not opportunity — that's noise.
2. **Test IR vs SPY is -0.38.** The strategy *underperforms* the market
   on a risk-adjusted basis even in the favorable test window. The
   raw +0.89 Sharpe is partly long-bias to a rallying 2023 market.
3. **Multi-comparison accounting (N=7) requires Sharpe > ~1.6** to
   clear the Bonferroni-adjusted threshold. +0.89 is well below.
4. **Train MaxDD is 49.5%.** Even if a future test happened to show
   real Sharpe, the drawdown profile is untradeable for any sized
   capital.
5. **Event-rate concentration risk.** Volume-surge events cluster in
   specific market regimes (earnings season, news-driven days). The
   +0.89 test Sharpe is plausibly a concentration artifact, similar
   to the Q3-2023 concentration in rsm_v1's test window. We didn't
   verify with quarterly decomposition — another test-set peek we
   didn't want to spend.

### F4. The platform's discipline machinery did its job

- All three runs produced complete `manifest.json` records with
  populated `universe_snapshot_hash` (per ADR 0012 — first runs to
  actually carry this field with real data).
- The shared lookahead regression test
  (`tests/unit/test_technical_lookahead.py`) caught zero violations
  across all three signals.
- SPY benchmark wiring (added in the rsm_v1 cycle) surfaced the
  "test Sharpe positive, IR negative" disconnect for volume_surge.
  Without IR, we'd have been more tempted to call it interesting.
- The static + import-linter gates remained green throughout.

### F5. Bonferroni is binding

Cumulative `N` of test-set peeks crossed 7 with this cycle. Per ADR
0005, that pushes the per-test threshold to `0.05 / 7 ≈ 0.0071`. For a
1-year test, this translates roughly to Sharpe > 1.6. Future cycles
have to clear that bar — or the project needs a fresh independent
data window (e.g., 2025+ live data once it's available; or a different
universe entirely).

The practical effect: hyperparameter sweeps on this universe and this
window are now extremely expensive in discipline-cost. Each variant
peeked adds to N, raising the threshold further. This is the cost of
having actually run multiple variants honestly — paid in advance, not
in retrospect.

## What's different from the rsm_v1 postmortem

The rsm_v1 negative verdict pointed at three issues: universe
selection bias, regime-dependence, cost-sensitivity. The v2 cycle
**addressed two of those**:

- Universe is now broader (~500 names) and not curated for any
  specific signal type — addresses limitation #1.
- Cost-sensitivity wasn't the issue (low-turnover momentum failed too).
- Regime-dependence still bites (volume_surge looks anti-generalized).

The remaining big limitation — survivorship bias (limitation #2) — is
not fully closed by a static current snapshot. A future EODHD-backed
PIT universe would close it, but that's a paid-data path that hasn't
earned the spend.

## Decision space for the next cycle

Per the trading-system pivot's open-ended frame:

1. **Try a different technical signal class.** Relative-strength
   (RSI-style), MACD divergence, moving-average cross,
   volatility-targeting. Each costs one peek, raising the bonferroni
   threshold. Probability-weighted EV is low at this point.

2. **Narrow the universe to small/mid-caps.** Some of the signals
   (especially short-term reversal) have stronger documented effects
   outside large-caps. Requires building/finding a Russell 2000-style
   constituent list.

3. **Accept the platform as the deliverable.** Four documented null
   results across two cycles is a strong honest-research story. The
   framework, ADRs, postmortems, and verdicts collectively demonstrate
   what the platform is for. Step back from active strategy search.

4. **Switch problem class.** The platform's discipline machinery
   transfers cleanly to adjacent problems — portfolio optimization on
   fixed signals, factor-model attribution, risk-decomposition. These
   might have lower base-rate-of-failure and don't burn test-set
   peeks the same way.

No single right answer here. The pivot plan committed to "find
something real, not ship by a date" — three documented null results
strengthen the platform's claim to honest discipline, which is itself
a deliverable.

## What's NOT changing

- The framework — produced reviewable numbers across three new
  strategy types with no architectural changes.
- The data layer — yfinance + ParquetStore + static universe handled
  500 tickers × 8 years without incident.
- The discipline rules — they correctly produced "do not touch
  holdout" decisions for all three configs.
- The platform's public-facing artifact — README will be updated to
  reflect v2 results but no rewrite is needed.

## File index

- Comparative verdict: `docs/verdicts/v2-tech-comparison.md`
- This postmortem: `docs/postmortem/v2-tech.md`
- v1 postmortem: `docs/postmortem/rsm-v1.md`
- v1 verdict: `docs/verdicts/rsm-v1-backtest.md`
- Signal modules: `src/supertrader/signals/technical/{momentum,reversal,volume_surge}.py`
- Configs: `configs/runs/v2_tech_{momentum,reversal,volume_surge}.yaml`
- Universe: `configs/universe/snapshot_sp500_2026_05_14.csv`
- Run outputs: `data/runs/v2-tech-{momentum,reversal,volume-surge}/`
- Discipline ADR: `docs/adr/0005-rsm-hyperparam-search.md`
- Limitations: `docs/known-limitations.md`
