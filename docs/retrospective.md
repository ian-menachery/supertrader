# Supertrader — Project Retrospective

A meta-postmortem across both research cycles + the platform-honesty
pass that closed them. If you read one document on this project, read
this one — the verdicts + postmortems below cover the specifics, this
covers the arc.

## Project goal vs delivered

**Original goal:** a personal quantitative research platform whose
first deliverable was a Reddit-sentiment mean-reversion signal on US
equities, with the framework designed to support arbitrary future
strategies (Form 4 insider clustering, options flow, technicals,
multi-factor combos) by config only.

**Delivered:** the framework. Strict-typed, layered, fully reproducible,
with HoldoutGuard discipline, RunManifest provenance, content-hashed
inputs, SPY-benchmarked tear sheets, cost-sensitivity automation, and a
public GitHub artifact at `github.com/ian-menachery/supertrader`.

**Strategies tested:** four. **Strategies that worked:** zero. **Honest
results documented:** all four.

That gap — between "platform finished" and "strategy works" — is the
project. The platform's job was to make the gap honest and reviewable.
It did.

## What worked

### Discipline machinery did its job

- **HoldoutGuard** prevented every one of four candidate strategies
  from touching its holdout slot on first contact. All four slots
  remain unspent, available for a future strategy that earns them.
- **RunManifest** produced reproducible records for every run with
  git SHA, config hash, python version, universe-snapshot hash, and
  data-partition hashes. Re-runs verified the cost-model dispatch
  produces identical slippage rates under v1.
- **Dirty-tree refusal** caught one accidental "let me just check
  something while the repo is uncommitted" moment per cycle on
  average. Each was a real save.
- **Test-set peek accounting (ADR 0005)** produced N = 7 cumulative
  peeks with a bonferroni-adjusted Sharpe threshold of ~1.6.
  Critically: no result cleared the threshold, so the platform's
  output across all four cycles was correctly read as "noise" rather
  than tuned toward a false positive.

### Engineering quality stayed under control

- **Layered architecture** held: `data → signals → strategies →
  execution`, enforced by `import-linter` on every commit. Zero
  violations across 400+ tests and ~50 commits.
- **mypy --strict** stayed clean throughout. Two type-narrowing
  workarounds were needed for matplotlib + pandas-stubs unions; both
  are documented inline.
- **90%+ coverage** held across the entire pivot sequence (Reddit
  sentiment → technical signals → platform-honesty refactor).
- **Static gates** ran in <1 minute end-to-end; pytest under 1.5 min.
  The fast feedback loop made discipline cheap.

### Public artifact tells a coherent story

The README links to verdicts + postmortems for both cycles, the
ADRs cover the load-bearing decisions, and the known-limitations doc
bounds every result with explicit caveats. Anyone reviewing the repo
sees four documented negative results, the methodology that produced
them, and the lessons learned — not a buried strategy and a polished
front page.

## What didn't work

### rsm_v1 — Reddit-sentiment mean-reversion (cycle 1)

Train Sharpe -0.47, test Sharpe +0.94. Anti-generalization. Test
result was 80% concentrated in Q3 2023. IR vs SPY +0.34 (weak). At
2× cost, test Sharpe collapsed to +0.57 (below the 0.8 tradeable
threshold). Universe was 34 tickers curated for sentiment relevance,
which amplified selection bias. Cost-sensitivity sweep confirmed
the result was noise-grade.

See `docs/verdicts/rsm-v1-backtest.md` + `docs/postmortem/rsm-v1.md`.

### v2 cross-sectional momentum (Jegadeesh-Titman 12-1, cycle 2)

Train Sharpe -0.06, test Sharpe -0.89. The strategy didn't make
money even in-sample. The 2018-2024 window has too many momentum
crashes (Mar 2020, Jan 2021, late 2022) to support a slow-turnover
momentum signal that depends on aggregate risk-premium harvesting.

### v2 z-score reversal (Lehmann/Lo-MacKinlay, cycle 2)

Train Sharpe -2.05, test Sharpe -3.01. The worst result of any
backtest the platform produced. Short-term reversal is mostly
arbitraged out on SP500 large-caps; the strategy mechanically fights
the trend in a generally rallying market. Documented effect lives
on small-caps where we didn't test.

### v2 volume surge (cycle 2)

Train Sharpe -0.67, test Sharpe +0.89. Same anti-generalization
shape as rsm_v1, plus an IR vs SPY of -0.38 (underperforms the
market). Best-looking headline of the v2 cycle, weakest underneath.

See `docs/verdicts/v2-tech-comparison.md` + `docs/postmortem/v2-tech.md`.

## What I'd do differently from the start

### Pick the universe BEFORE the signal

The 34-ticker meme-stock-adjacent universe was chosen to match the
Reddit-sentiment signal. That's selection bias by construction —
the signal was being tested on the names where it should have its
biggest effect. A neutral universe (random R1000 small/mid cap,
or full SP500) would have produced a cleaner first result.

The v2 cycle corrected this — SP500 broader universe, signal-
agnostic — but by then the bonferroni budget was already half-spent
on the rsm_v1 universe.

### Pick the strategy CLASS by data first, not by interest

Reddit sentiment was always supposed to be an *idea source*, not an
*alpha source*. The actual signal layer should have started with
something the platform's data + cost model could honestly measure —
technical signals, factor signals — and Reddit should have been a
hypothesis-generation tool. Conflating "Reddit as inspiration" with
"Reddit as signal" cost two months of cycle time.

The same lesson applies to Form 4 — the redline backfill was
nowhere near deep enough for a cross-sectional study, but we
planned around it assuming it was. Verify data sufficiency before
committing to a strategy class.

### Build the realistic cost model before the first backtest

The v1 cost model used a flat 3 bps slippage. Realistic small-cap
spreads are 20-50 bps. The cost-sensitivity sweeps caught the
direction (strategies look worse at 2-3× cost) but not the
magnitude (the right multiplier on meme stocks is more like 5-10×,
not 2-3×). The platform-honesty pass added a v2 cost model
(ADR 0010) but it landed after all four strategy verdicts. Future
strategies should default to v2 from the start.

### Don't separate "framework cycle" from "strategy cycle"

The original 8-week plan split weeks 1-4 (framework) from weeks 5-8
(strategy + paper trading). In practice the strategy work surfaced
framework gaps that needed fixing (turnover constraint, weight
smoothing, universe-guard leakage check) that should have been
in the v1 framework. The pivot's "platform-honesty pass" was where
these landed — better late than never, but the strategy results
they would have changed are already in the books.

### Plan for the bonferroni cost up front

By cycle 2 the cumulative test-set peek count was already 4 (one
per cost variant + one momentum diagnostic in cycle 1). Cycle 2
added 3 more. The implied Sharpe threshold to clear noise rose to
~1.6 by cycle 2 — beyond what any of these strategies were ever
plausibly going to deliver on this data. A future project should
budget peeks like budget money: each cycle gets ~3 peeks max, and
once you've used them on this universe + window, you need either a
fresh window (live data) or a structurally different strategy
before resuming.

## What this platform is for going forward

Three honest framings, ordered by ambition:

1. **Strategy-research playground (current state).** Test new ideas
   here. Don't pretend the verdicts are pre-paid backtests of viable
   strategies — they're cheap, honest, statistically-disciplined
   prototypes. Use the discipline to *avoid* fooling yourself, not
   to claim you've found something.

2. **Reference implementation for "how to write an honest
   backtest."** The discipline machinery (HoldoutGuard, RunManifest,
   N-peek accounting, cost-sensitivity, universe-guard, SPY
   benchmarking) is publicly visible and well-documented. Anyone
   building something similar has a working example. The four
   negative results aren't a bug — they're the proof that the
   discipline produces honest "no"s.

3. **Foundation for an event-driven strategy class.** Once data
   activates (paid Polygon for PEAD, or a real redline backfill for
   Form 4 clustering, or a Russell 2000 universe for small-cap
   reversal), the platform is ready. None of those is in scope of
   this project's current state; each is a separate future plan
   with its own data-gating decision.

## Carry-over rules for the next research cycle

- **N = 7** test-set peeks already taken on this data. Treat each
  new peek as a tax on the bonferroni budget.
- **All four holdouts untouched.** Future strategies get fresh
  holdout slots (different config_hash), but the SP500-window
  bonferroni cost carries.
- **`costs.model_version: "v2"` is the default** for any new config.
  Pin v1 explicitly only when reproducing a historical result.
- **`max_turnover_annual` and `smoothing_alpha`** are available on
  every cross-sectional strategy. Use them. 219× annualized turnover
  is a config bug, not a strategy verdict.
- **PIT universe** (`PITUniverse`) skeleton exists but
  `from_eodhd_store` is unimplemented. First paid-data subscription
  decision should activate it.

## File index

- README.md — public-facing project state.
- CLAUDE.md — conventions + lessons learned.
- docs/known-limitations.md — eight ranked caveats that bound any
  result.
- docs/adr/ — twelve ADRs covering load-bearing decisions.
- docs/verdicts/rsm-v1-backtest.md + docs/postmortem/rsm-v1.md
  (cycle 1).
- docs/verdicts/v2-tech-comparison.md + docs/postmortem/v2-tech.md
  (cycle 2).
- docs/dev-notes.md — chronological engineering journal.
- docs/retrospective.md — this file.
- src/supertrader/ — the framework.
- tests/ — 400+ tests, mypy strict, 90%+ coverage.
- configs/runs/ — every run config; v1 historical runs pinned to
  `model_version: v1`.

## Closing

Four documented null results is not a failed research project. It's
a research project where the discipline worked — every "promising"
number got the honest analysis it deserved and was correctly read
as noise. The platform is in better shape now (post the
platform-honesty pass) than it was when the strategies ran on it,
which is the right order if you're going to keep using it.

The framework is the deliverable. Use it for the next idea, with
the rules from this retrospective in mind.
