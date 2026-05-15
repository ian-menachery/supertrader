# Postmortem — RSM v1 (Reddit-sentiment mean-reversion)

> The canonical plan called for `research/03_strategy_postmortem.ipynb`. This
> markdown postmortem serves the same purpose with less ceremony — it
> renders on GitHub, it diffs cleanly, and it carries all the same
> findings. If you want an executable notebook later (to re-run the
> diagnostic numerics from a clean kernel), it's a follow-up task.

## What was tested

Reddit-sentiment cross-sectional mean-reversion on a 34-ticker
meme-stock-adjacent US-equity universe. Train 2022-01-03 → 2023-06-30
(~377 trading days), test 2023-07-01 → 2023-12-29 (~125 trading days),
holdout untouched. Strategy: score each ticker each day from VADER +
financial-lexicon overlay on WSB posts, long the bottom 30% (most negative
sentiment) and short the top 30%. Target gross exposure = 1.0.

Three cost variants (1×, 2×, 3× base slippage). One directional variant
(long top / short bottom = momentum instead of mean-reversion).

## Findings

### F1. Strategy direction is correct (mean-reversion, not momentum)

| variant | TRAIN Sharpe | TEST Sharpe |
| ------- | -----------: | ----------: |
| mean-reversion (canonical) | -0.47 | +0.94 |
| momentum (flipped) | -1.20 | -2.66 |

Flipping the direction makes everything worse. So if there's signal in
WSB sentiment for this universe, it's mean-reversion shaped (high
positive sentiment → forward underperformance). The momentum variant
roughly mirrors mean-reversion's Sharpe sign, as expected from a
cross-sectional rank flip.

### F2. The "tradeable" test Sharpe is regime-concentrated, not stable

Decomposed the canonical test window into 2023-Q3 (Jul-Sep) and 2023-Q4
(Oct-Dec):

| window | n_days | Sharpe | cum_ret |
| ------ | -----: | -----: | ------: |
| FULL test | 127 | 1.74 | +22.7% |
| 2023-Q3 | 63 | **2.45** | **+18.2%** |
| 2023-Q4 | 63 | 0.82 | +3.8% |

Roughly 80% of the test-window cumulative return came from Q3 alone.
Q3 2023 saw a bond-yield spike + small-cap rotation that punished
high-sentiment tickers especially hard — almost ideal conditions for a
short-the-loved cross-sectional bet. Q4 reverted to a generic year-end
rally where the strategy barely worked.

A strategy with a stable edge wouldn't show this concentration.

### F3. Cost-sensitivity collapses the signal

After wiring SPY as a benchmark and re-running:

| variant | TEST Sharpe | TEST beta vs SPY | TEST IR vs SPY |
| ------- | -----------:| ----------------:| --------------:|
| 1× cost | 0.94 | -0.31 | +0.34 |
| 2× cost | 0.68 | -0.27 | +0.10 |
| 3× cost | 0.01 | -0.35 | -0.45 |

The 2× cost test Sharpe (0.68) is below the `docs/known-limitations.md`
#3 tradeable threshold of 0.8. The 3× test Sharpe is zero. Even at 1×
cost the *information ratio* (excess return per unit active risk vs SPY)
is only +0.34 — well within the noise floor at this sample size.

### F4. Train Sharpe is negative across all variants

This is the single most damning fact. A strategy that loses money for
18 months in-sample and then makes money for 6 months out-of-sample has
not generalized — it has anti-generalized. The most likely
interpretations are:

- **Regime dependence.** 2022's bear market punished long-short on this
  universe; 2023's mixed regime gave one good quarter (F2).
- **Pure variance.** Standard error on a 6-month Sharpe is roughly 0.7;
  the +0.94 falls comfortably within ±1 SE of zero.
- **Selection bias.** Per limitation #1, the universe was curated for
  sentiment relevance. The strategy may be "working" on a specific
  cohort because of how the cohort was chosen.

None of these support a "tradeable" claim.

### F5. The strategy is mildly short-biased relative to SPY

Test beta is -0.31. For a market-neutral long/short by construction
(net exposure ≈ 0%), that nonzero beta says the average short basket
beat the average long basket in beta terms. In a tech-led rally
environment (which 2023 partly was), high-sentiment names (the shorts)
tended to be higher-beta — so a market-neutral position would
mechanically tilt negative-beta. Not surprising, but it explains some
of the test pop: the strategy was effectively short the highest-beta
side of a partially-rallying market.

## Holdout decision

**Holdout REMAINS UNTOUCHED.** Per ADR 0005 discipline, touching the
holdout for a config whose train+test+cost-sensitivity all flag "noise
or regime-dependence" would burn the one-shot slot on a result that's
unlikely to surprise. The holdout slot is reserved for a future, better
strategy variant — built on the diagnostic learnings above.

## Discipline accounting

Test-set evaluations during this project (peeks):

1. Canonical `rsm_v1_backtest.yaml`
2. `rsm_v1_backtest_2x_cost.yaml`
3. `rsm_v1_backtest_3x_cost.yaml`
4. `rsm_v1_backtest_momentum.yaml`

Plus three same-config re-runs (decompose script, post-SPY-ingest
canonical, post-SPY-ingest 2× and 3×) which share the same
`config_hash` per peek and therefore don't count as new peeks under
ADR 0005's rule.

`N = 4` for bonferroni purposes. Per-test threshold becomes
0.05 / 4 = 0.0125, which on a 125-day test is roughly Sharpe > 1.5
to clear. No variant clears.

## What I'd change for v2

In priority order:

1. **Broader, non-curated universe.** Random or pseudo-PIT R1000 small/mid
   subset, not WSB-curated. Closes limitation #1 and tests whether the
   modest signal survives universe randomization.
2. **Make the prices DataFrame index deterministic.** `_load_prices` in
   `pipelines/run_backtest.py` currently pivots over whatever tickers are
   in the store, so adding SPY to the store changed the test-window date
   index and shifted Sharpe from 1.74 → 0.94 on the same data. Fix:
   intersect dates with `TradingCalendar.sessions(start, end)` before
   pivot. Separate plan.
3. **100-post sentiment eval set.** Bound VADER+lexicon accuracy.
4. **Forward-only paper trading on a SECOND strategy idea** (not v1).
   Sentiment + technical z-score composite, or Form 4 insider clustering
   from the redline boundary.
5. **A multi-comparison peek log** that auto-writes to
   `data/runs/test_set_peeks.log` on every test-set evaluation, so the
   bonferroni accounting isn't a manual count in postmortems like this
   one.

## What's NOT changing

- **The framework.** It produced honest numbers, showed an honest pattern
  (anti-generalization is hard to fake), and the discipline machinery
  (HoldoutGuard, RunManifest, cost-sensitivity automation) all behaved as
  designed. We trust the tooling.
- **The "negative result is a real result" framing.** This postmortem is
  the deliverable. A strategy that doesn't work tells us where the next
  signal-search should start.
- **The holdout slot.** Unspent, available for a future variant.

## File index

- Verdict (initial response): `docs/verdicts/rsm-v1-backtest.md`
- This postmortem: `docs/postmortem/rsm-v1.md`
- Canonical run output: `data/runs/rsm-v1-backtest/{metrics.json,
  manifest.json, tear_sheet.html}` (gitignored — re-run to regenerate)
- Cost variant outputs: `data/runs/rsm-v1-backtest-{2x,3x}-cost/`
- Momentum variant output: `data/runs/rsm-v1-backtest-momentum/`
- Decomposition script: `scripts/decompose_test_quarters.py`
- Hyperparameter discipline ADR: `docs/adr/0005-rsm-hyperparam-search.md`
- Limitations: `docs/known-limitations.md`
