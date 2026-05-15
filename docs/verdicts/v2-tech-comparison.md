# Verdict — v2 technical signals (comparative, 2026-05-14)

Three signals run on the same broader SP500 universe (~500 names),
same windows, same v1 cost model. The point was to land a *comparative*
first-round verdict: which (if any) of momentum / reversal / volume
surge generalizes on this universe.

## Headline

| signal | TRAIN Sharpe | TEST Sharpe | TEST IR vs SPY | TEST MaxDD | Turnover (ann) |
| ------ | -----------: | ----------: | -------------: | ---------: | -------------: |
| `cross_sectional_momentum` | -0.06 | **-0.89** | -1.76 | -13.0% | 13× |
| `zscore_reversal` | -2.05 | **-3.01** | -2.90 | -20.4% | 219× |
| `volume_surge` | -0.67 | **+0.89** | -0.38 | -8.0% | 165× |

**None tradeable per `docs/known-limitations.md` #3:** the trigger
requires test Sharpe ≥ 0.5 at 1× cost AND ≥ 0.3 at 2× cost. Volume
surge is the only candidate above 0.5 at 1×; we did not run its
cost-sensitivity sweep because of the deeper red flags below.

## Multiple-comparisons discipline (ADR 0005)

Running total test-set peeks across the project:

- rsm_v1 cycle: 4 (canonical + 2× cost + 3× cost + momentum diagnostic)
- v2 cycle: 3 (momentum + reversal + volume surge)
- **N = 7**

Per ADR 0005's bonferroni rule: per-test threshold drops to
`0.05 / 7 ≈ 0.0071`, which on a 252-day test corresponds roughly to
**Sharpe > 1.6** to clear "real signal" vs multi-comparison noise. None
of the three signals clears that bar.

## Per-signal honest read

### Cross-sectional momentum — failed

Train Sharpe -0.06 means the strategy didn't make money even
in-sample. Test was worse. This is unusual for 12-1 momentum on SP500,
which has historically shown Sharpe in the 0.5-1.0 range — but the
2018-2024 window includes several momentum crashes (Mar 2020, Jan
2021, late 2022) that dominate longer-horizon historical results.

The negative information ratio vs SPY (-1.76) means the strategy
actively destroyed value compared to just holding SPY. Combined with
the modest 13× annual turnover, this isn't a cost-model problem; it's
a "the signal didn't work in this window" problem.

### Z-score reversal — broken

Worst result of any backtest this project has produced. Train Sharpe
-2.05, test Sharpe -3.01, max drawdown 65% in train. The strategy
*systematically loses money*.

The plausible explanation: on SP500 large caps, short-term reversal is
arbitraged out, so the "long today's biggest losers" trade is just
selecting for genuinely deteriorating fundamentals or genuinely high-
beta names in down markets. In a trending market, the strategy
mechanically fights the trend.

Direction-flipped (a momentum interpretation: long today's biggest
gainers) would mirror this and likely show positive results — but
intra-day-to-next-day momentum on SP500 has its own crowdedness
problems. Either way: this specific implementation is not viable.

### Volume surge — looks best, but is the same shape as rsm_v1

Test Sharpe +0.89 is the headline. But:

- **Train Sharpe -0.67.** Same anti-generalization pattern as rsm_v1
  (negative train → positive test). Per CLAUDE.md lesson #1: "If train
  Sharpe is negative, do not get excited about a positive test Sharpe —
  that's anti-generalization, not edge."
- **Test IR vs SPY is -0.38.** The strategy *underperforms* the market
  on a risk-adjusted basis even in the favorable test window. The +0.89
  Sharpe is partly long-bias beta to a rallying 2023 market, not real
  alpha.
- **MaxDD 49.5% in train.** Even if a future test showed real Sharpe,
  the drawdown profile is untradeable.
- **N=7 bonferroni threshold ≈ 1.6.** +0.89 is well below.

The pattern, *again*, looks like a Q3-Q4 2023 concentration effect on
event-rare days, not a stable signal. Documenting this hypothesis
would require running the rsm_v1-style quarterly decomposition, but
that's another test-set peek and we've already concluded "not
tradeable."

## Decision

**Do not touch the holdout for any v2 signal.** All three configs
remain holdout-untouched. The HoldoutGuard sqlite row count for these
config_hashes is zero.

**Do not run cost-sensitivity sweeps.** None of the three signals
crossed the 1× cost threshold strongly enough to make a 2×/3× sweep
informative. We have three documented negative results, and the next
move is *not* "tune slippage assumptions on a noise-grade signal."

**Do not subscribe to paid data on the basis of these results.** ADR
0008's Polygon trigger required test Sharpe ≥ 0.5 at 1× *and* ≥ 0.3 at
2×. Even if volume_surge's 1× clears, the 2× isn't measured and the
deeper analysis (anti-generalization, weak IR) argues against the
spend.

## What this tells us about the platform

The framework operated correctly across three independent strategy
variants:

- All three runs produced reproducible manifests with universe-snapshot
  hashes.
- The look-ahead regression test (`test_technical_lookahead.py`) caught
  zero violations.
- The SPY benchmark wiring (per D3 of the rsm_v1 cycle) surfaced the
  "weak IR" problem that the headline Sharpe alone would have hidden.
- The signal-protocol decision in ADR 0009 (NaN-sparse panels for
  event-driven signals) handled volume_surge without protocol churn.
- The new SP500 universe (~500 names) flowed through `StaticUniverse`
  + the existing pipeline with only a config-side `snapshot_path`
  change.

The platform now has **four documented negative results** in its
history — and the discipline machinery produced an honest, reviewable
record of each.

## Next steps (open-ended)

Per the trading-system pivot's strategic frame: the platform has
demonstrated its value. The next research cycle either:

1. **Tries another technical signal** (e.g., relative-strength, MACD,
   moving-average cross). Each is a new configuration with no platform
   changes required. New peeks count toward N.
2. **Accepts the platform as the deliverable.** Ship the documented
   research artifact: framework + rsm_v1 + v2 negative results +
   honest postmortems. Step back from active strategy search until a
   genuinely new idea or data source becomes available.

A positive verdict at this point would have been the exception, not
the rule. The platform's job is to make negative verdicts visible and
trustworthy — which it has done.

## Reproducibility footers

Pulled from each `data/runs/<run_id>/manifest.json`:

- `v2-tech-momentum` — config_hash `56a17c2d6c68a17915218539dbd8fd50`
- `v2-tech-reversal` — config_hash `48805d6b78d0eb929f0ca231e3034eba`
- `v2-tech-volume-surge` — config_hash `2c9250fdbe92ecb66da417071f238bff`

All three share git_sha (recorded on each manifest), python 3.12.4,
supertrader 0.1.0, universe_snapshot_hash for the SP500 snapshot, and
data_hashes covering 518 yfinance partitions over 2018-2025.
