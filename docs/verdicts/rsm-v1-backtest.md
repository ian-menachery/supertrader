# Verdict — RSM v1 canonical backtest (2026-05-14)

## Headline

- **Test Sharpe:** +1.34 (95% CI roughly ±1.0 on ~125 trading days; treat as 0.3-2.3)
- **Test MaxDD:** -9.4%
- **Hit rate (test):** 50.0%
- **Universe size:** 34 tickers (meme-stock-adjacent; see limitation #1)
- **Windows:** train 2022-01-03 → 2023-06-30 (~377 trading days), test 2023-07-01 → 2023-12-29 (~125), holdout UNTOUCHED
- **Train Sharpe:** **-0.48** (the headline test number does not reproduce in-sample)

One sentence: **Test Sharpe looks tradeable at face value, but train Sharpe is
negative and the 2× cost variant drops below the limitation-#3 threshold —
the strategy is most likely regime-dependent noise, not a stable edge.**

## Cost sensitivity

Same data, same windows, only `slippage_bps_base` and
`slippage_impact_coeff_bps` scaled. Commission held at 1bps.

| variant | train Sharpe | test Sharpe | test MaxDD | test gross |
| ------- | -----------: | ----------: | ---------: | ---------: |
| 1× (canonical) | -0.48 | **+1.34** | -9.4% | 97.6% |
| 2× slippage | -1.18 | **+0.57** | -12.8% | 97.6% |
| 3× slippage | -1.86 | **-0.16** | -17.5% | 97.6% |

Each additional 1× of slippage costs roughly 0.4-0.8 Sharpe points in test.
The cost gradient is steep — turnover is ~213% annualized, so a ~3 bps base
slippage error is ~6% of annual NAV.

## Verdict against the limitation-#3 rule

Decision rule from `docs/known-limitations.md` #3:
*"if 2× cost test Sharpe still > 0.8, claim tradeable; otherwise claim
interesting but cost-sensitive."*

- [ ] **Tradeable.** 2× cost test Sharpe = 0.57, NOT > 0.8. ✗
- [x] **Cost-sensitive but interesting.** 1× cost test Sharpe = 1.34 > 0
      but 2× cost test Sharpe = 0.57 < 0.8. ✓
- [ ] **Negative result.** 1× cost test Sharpe = 1.34 is positive, not ≤ 0.

**But — read the next section before drawing comfort from "interesting."**

## The honest read: this is probably regime-dependent noise

The decision rule above is necessary but not sufficient. Three things make
the test-Sharpe-of-1.34 less impressive than it looks:

1. **Train Sharpe is negative across all cost variants** (-0.48 / -1.18 /
   -1.86). A strategy that loses money over 18 months in-sample and then
   makes money over the next 6 months hasn't generalized — it has
   anti-generalized. The honest interpretation is regime-dependence
   (2022 bear market → strategy bleeds; 2023H2 rally → strategy works) or
   pure variance, not a stable edge.

2. **6-month test = high-variance window.** Standard error on a quarterly
   Sharpe is ~1.4 even at true SR=1. Our 125-day test SR=1.34 implies a
   95% CI that comfortably includes zero. Calling this "tradeable" on a
   single test-window peek would require ignoring the noise floor.

3. **Universe selection bias amplifies any apparent edge.** Limitation #1:
   the 34-ticker universe was curated for sentiment-relevance, so any
   sentiment-related signal pop is over-represented vs. a true PIT
   small/mid-cap basket.

A real working strategy on this framework would show: positive train
Sharpe, similar or slightly worse test Sharpe (small overfit gap), test
Sharpe still > 0.8 at 2× cost, and an honest universe. We have none of
those four.

## Next-action decision

Following limitation-#3's *cost-sensitive-but-interesting* branch:

- [x] **Do NOT touch the holdout yet.** Touching the holdout for this
  config burns the one-shot slot on a result that's almost certainly
  noise. The holdout slot is for the *next* honest attempt.

- [x] **Do NOT proceed to W7 paper trading on RSM v1 as-is.** Paper
  trading should validate a strategy that earned its keep on backtest;
  this one hasn't.

- [ ] **Pursue, in order:**
  1. **Broader universe** (per limitation #1). A second backtest on a
     non-curated Russell-1000 small/mid-cap snapshot would tell us
     whether the test-window pop survives universe randomization. If it
     doesn't, we have evidence of selection bias rather than signal. New
     plan: build the broader universe snapshot, re-run.
  2. **Universe-randomization test.** Cheaper than (1): shuffle the
     universe (random 34-name subset of Russell 1000) and run the same
     strategy. Repeat ~20 times. If our universe's test Sharpe sits in
     the middle of the shuffled distribution, the signal is real but
     weak; if it sits at the top, we have selection bias.
  3. **Calendar-shifted test.** Run the strategy on adjacent test
     windows (2023-Q3 vs 2023-Q4) separately. If the +1.34 Sharpe is
     concentrated in one quarter, that's regime-dependence; if it's
     stable across quarters, that's signal.
  4. **Postmortem notebook** at `research/03_strategy_postmortem.ipynb`
     capturing the above analysis + what we'd change for v2.

- [ ] Open a follow-up plan for whichever of (1)-(3) gets done first.

## Reproducibility footer

Pulled verbatim from `data/runs/rsm-v1-backtest/manifest.json`:

- `run_id`: `rsm-v1-backtest`
- `config_hash`: `db0e8a836d7fbb8d72cd34d6309414fd`
- `git_sha`: `9b5ed5fd8c5d07a5b51b348f390dc60241920df1`
- `git_dirty`: `false`
- `python_version`: `3.12.4`
- `supertrader_version`: `0.1.0`
- `started_at`: `2026-05-14T23:59:33.714967Z`
- `ended_at`: `2026-05-15T00:01:43.646304Z`
- `status`: `ok`
- `data_hashes`: 61 partitions (34 yfinance prices + 27 arctic_shift posts);
  see `data/runs/rsm-v1-backtest/manifest.json` for the full set.

Cost-sensitivity sibling runs:
- `rsm-v1-backtest-2x-cost`
- `rsm-v1-backtest-3x-cost`

## What this verdict does NOT say

- "Reddit sentiment is uninformative for US equities." We only tested on
  a curated meme-stock-adjacent universe. A null result here doesn't
  generalize.
- "Mean-reversion of sentiment is the wrong strategy idea." We tested
  one parameter setting. ADR 0005's sweep ranges are still un-explored
  on the train window.
- "VADER is the wrong scorer." Per limitation #5, scorer accuracy is
  unmeasured. The signal might be improvable with FinBERT, but that's a
  separate question from "does the strategy generalize across regimes."
- "The framework is wrong." The framework worked correctly — it
  produced an honest negative-train + positive-test pattern and the
  tooling (cost sensitivity, manifest, holdout discipline) all behaved
  as designed.
