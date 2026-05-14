# ADR 0005 — RSM v1 hyperparameter search discipline

**Status**: Accepted
**Date**: 2026-05-14

## Context

The Reddit-sentiment mean-reversion strategy has four hyperparameters that
materially shape its output:

| param | current | type | knob |
| ----- | -------:| ---- | ---- |
| `signals[0].params.aggregation` | `score_weighted_mean` | enum | how multiple posts collapse to a per-day-per-ticker score |
| `signals[0].params.decay_halflife_hours` | `24` | float | how quickly older posts lose weight |
| `strategy.params.quantile` | `0.3` | float | bottom-decile-long / top-decile-short width |
| `strategy.params.min_signal_observations` | `5` | int | minimum posts per (date, ticker) before scoring |
| `strategy.params.target_gross` | `1.0` | float | sum-of-|weights| target |

Each parameter has a defensible plausible range; sweeping them over the
*test* window post-hoc would silently overfit and invalidate the test set
as an honest judge.

The canonical 8-week plan (W6) explicitly calls for this ADR — *"document
the search space, decide on hyperparameters via train period only, lock the
config, run the test period."* This is that document, written **before**
the canonical run produces numbers that might tempt fiddling.

## Decision

### Sweep ranges

For any sweep, the range is the table below. Anything outside the range
gets its own ADR (or its own honest postmortem entry).

| param | range | rationale |
| ----- | ----- | --------- |
| `aggregation` | `{mean, score_weighted_mean, time_decayed}` | `count_weighted` is degenerate when many posts per (date, ticker) |
| `decay_halflife_hours` | `[6, 72]` | shorter than 6h fights the post-volume noise; longer than 72h is multi-day, in which case mean-reversion is the wrong frame |
| `quantile` | `[0.2, 0.5]` | tighter than 0.2 gives a degenerate basket; wider than 0.5 is just "everything that ranked at all" |
| `min_signal_observations` | `[3, 20]` | below 3 is single-post noise; above 20 excludes most tickers and the basket shrinks |
| `target_gross` | `{1.0}` | leverage decisions deserve their own ADR |

### Discipline rules

1. **Train-only sweeping.** Every hyperparameter configuration is
   evaluated on the **train** window only. Pick the best by train Sharpe
   (or train Calmar — declare which metric in the dev-notes entry that
   triggered the sweep). Lock the config.

2. **One test-set evaluation per config_hash.** After the train-set pick,
   run the locked config on the test window. That evaluation **counts as a
   peek** for the purposes of multiple-comparisons accounting:

   - All test-set peeks are logged to `data/runs/test_set_peeks.log`
     (JSON-Lines, append-only, intended to grow over the life of the
     project).
   - If N peeks have been taken, the meaningful p-value threshold is
     `0.05 / N` per Bonferroni. The verdict template (
     `docs/templates/verdict-rsm-v1.md`) requires citing N at write time.

   *Implementation of the peek log is deferred.* For the first canonical
   run, N = 1 by construction (no prior peeks recorded). The infrastructure
   becomes load-bearing only when a second canonical-config variant gets
   evaluated on the same test window — at which point we add the writer
   in a follow-up plan.

3. **Holdout one-shot per config_hash.** Already enforced by
   `HoldoutGuard` (`src/supertrader/backtest/splits.py`). Touching the
   holdout for a previously-touched config_hash raises `HoldoutTouchedError`.
   The only sanctioned override path is `scripts/reset_holdout_lock.py`,
   which logs to the audit log (`data/runs/holdout_overrides.log`) and
   refuses on a dirty git tree.

4. **No sweeping after the test peek.** Once a config has been evaluated
   on the test set, sweeping its parameters and re-evaluating is a peek
   inflation — a deliberate data leak. The discipline is: lock → train →
   test → verdict → (if positive) holdout. No going back.

### What if the test-set peek shows a clear failure?

This is the common case in honest research. The discipline is:

- Record the result in the verdict file (per the template's "negative
  result" branch).
- Write a postmortem (`research/03_strategy_postmortem.ipynb`) covering
  what specifically failed and what the next signal/strategy to try would
  be.
- Do **not** sweep again with the same data window expecting a different
  outcome. A null result is a real result. Spending another peek on
  variants is what the bonferroni threshold guards against.

### What if the verdict template needs me to peek N more times?

You probably don't want to. The right move when test-Sharpe sits near zero
is usually:

- Broaden the signal (combine sentiment + technical, add Form 4),
  evaluating *the combined config* on a fresh future window — not the same
  test window.
- Build the 500-post sentiment eval set (limitation #5) and re-evaluate
  scorer quality before re-running the strategy.
- Get more data (extend the WSB backfill further back, or add other
  subreddits).

Each of those is a new config and thus a new `config_hash`, opening a
fresh holdout slot — but they cost real work (engineering or labeling).
That cost is the discipline backstop.

## Consequences

- Writing a credible verdict requires citing N (number of test peeks
  taken across all config variants). For now, N = 1 trivially.
- The test-set peek log is the single source of truth for N going
  forward. Implementing it is a small future plan, not a blocker.
- We can never "tune our way" to a winning strategy on this dataset
  without burning peeks. That's the point.
- Sweeping fully on the train window is encouraged — the train window
  exists exactly for that. Train-set overfitting is fine; test-set
  overfitting is not.

## Path to relaxing the discipline

A more permissive sweep regime becomes defensible when **any** of:

1. We have a much larger universe (Russell 1000 small/mid, not 34 names)
   — more cross-sectional samples, less per-config noise, peeks cost less
   information.
2. We have multiple uncorrelated signals running in parallel under
   `CompositeSignal`, each with its own test/holdout slot — the
   multi-comparison problem moves from "1 strategy × N param-tries" to
   "M signals × 1 try each."
3. We start logging realized live PnL (W7 paper trading) — at that point
   the test/holdout abstraction loses primacy and live-result Sharpe
   becomes the binding judge.

Until one of those is true, the discipline above stands.
