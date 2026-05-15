# ADR 0011 — Backtest split config flexibility

**Status**: Accepted
**Date**: 2026-05-14

## Context

`BacktestConfig` (`src/supertrader/config/schemas.py`) already supports
arbitrary train/test/holdout windows via four explicit date fields
(`start`, `train_end`, `test_end`, `end`). The 18/6/3-month split that
showed up in `rsm_v1_backtest.yaml` was a config-file choice, not a
schema constraint.

The trading-system pivot needs longer windows for v2 strategies (PEAD
typically wants 5+ years of train data to characterize SUE × forward-
return distributions; Form 4 wants a similarly long window for cluster
density to stabilize). Nothing structurally prevents this today, but
two contract issues worth codifying:

1. The existing validator (`_check_split_ordering`) uses
   `train_end < test_end <= end`. The `<=` allows a zero-length holdout
   window, which is sometimes intentional (the `rsm_v1_q1_2024.yaml`
   smoke pins `end == test_end` to skip the holdout). For the pivot's
   v2 strategies, we want to require a non-empty holdout.
2. There's no validation that each window contains enough trading days
   to be statistically meaningful. A 5-day "training" window is
   technically valid but practically useless.

## Decision

Keep the four-date schema (no breaking changes). Tighten the validator
in two ways:

1. Allow `test_end == end` only when `BacktestConfig.allow_empty_holdout
   = True`. Default is `False`. Smoke configs that want to disable the
   holdout opt in explicitly.
2. Enforce a minimum window length of 5 trading days per window
   (counted by calendar days as a proxy, since the schema doesn't have
   calendar access — the existing `data/calendar.py` check happens at
   pipeline level). Below 5 calendar days per window raises a clear
   error rather than silently producing degenerate metrics.

The 18/6/3-month rsm_v1 layout, the 5/1/1-year v2a/v2b layout, and the
short Q1-2024 smoke layout all continue to load — the smoke just adds
`allow_empty_holdout: true` to its config.

## Decision against syntactic sugar

A tempting alternative was a relative-time syntax in YAML:

```yaml
backtest:
  start: 2018-01-01
  train_length: 5y
  test_length: 1y
  holdout_length: 1y
```

This was rejected because:

- It introduces a parsing layer between YAML and the validated schema.
- It hides what dates are actually computed (which would silently shift
  if the parser changes).
- The current explicit-dates form is grep-able: `grep test_end
  configs/runs/*` returns exactly the configs you'd expect.
- The verbosity cost is 4 lines of YAML per config. Acceptable.

If a configurer wants to compute dates from durations, they can write
a one-off script that emits the YAML. The schema stays explicit.

## Implementation

```python
class BacktestConfig(StrictModel):
    start: date
    end: date
    train_end: date
    test_end: date
    allow_empty_holdout: bool = False
    # ... other fields ...

    @model_validator(mode="after")
    def _check_split_ordering(self) -> BacktestConfig:
        # train must end strictly before test starts; test must end at-or-before run end
        if not (self.start <= self.train_end < self.test_end <= self.end):
            raise ValueError(...)
        if self.test_end == self.end and not self.allow_empty_holdout:
            raise ValueError(
                "test_end == end implies an empty holdout window. "
                "If intentional (smoke test), set allow_empty_holdout=true."
            )
        # min-window-length check (calendar days, not trading days)
        if (self.train_end - self.start).days < 5:
            raise ValueError("Train window must span at least 5 calendar days")
        if (self.test_end - self.train_end).days < 5:
            raise ValueError("Test window must span at least 5 calendar days")
        if not self.allow_empty_holdout and (self.end - self.test_end).days < 5:
            raise ValueError("Holdout window must span at least 5 calendar days")
        return self
```

## Migration

- `configs/runs/rsm_v1_q1_2024.yaml` gains `allow_empty_holdout: true`
  if it currently has `end == test_end`. Check at implementation time.
- All other existing configs satisfy the new rules unchanged.
- The unit test in `tests/unit/test_config_schemas.py` gets two new
  cases for the new validation paths.

## References

- `src/supertrader/config/schemas.py:BacktestConfig` — module being
  extended.
- `tests/unit/test_config_schemas.py` — tests to extend.
- Pivot plan, Phase A.
