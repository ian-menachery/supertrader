# ADR 0012 — PIT universe and `universe_snapshot_hash` on RunManifest

**Status**: Accepted
**Date**: 2026-05-14

## Context

`docs/known-limitations.md` #1 and #2 — selection bias and survivorship
bias — are the largest credibility threats to any backtest verdict
produced by this platform. ADR 0004 acknowledged the static-snapshot
limitation; ADR 0007 listed an upgrade path; ADR 0008 funded the data
side of that path (EODHD historical constituents).

The remaining work is structural:

- A new `Universe` class that exposes a per-date constituent set rather
  than a fixed list.
- A hash field on `RunManifest` so any run records which universe it
  actually saw — independent of any config-side description.

Without the manifest hash, "we ran with universe X" is a claim grounded
in the config file at run time. Universes evolve (constituent
additions/deletions are common); a year-later re-run of the "same"
config could see a materially different universe. The manifest hash
turns this from a trust-the-config claim into a verifiable artifact.

## Decision

### Class hierarchy in `src/supertrader/data/universe.py`

```python
class Universe(Protocol):
    """Read-only API for what tickers were tradeable on a given date."""
    def tickers(self, as_of: date | None = None) -> list[str]: ...
    def __contains__(self, ticker: str) -> bool: ...
    # New: panel hash for reproducibility.
    def snapshot_hash(self) -> str: ...


class StaticUniverse(Universe):
    """Existing class. as_of is ignored; tickers() always returns the same set.
    snapshot_hash() = blake2b of the sorted ticker list."""


class PITUniverse(Universe):
    """New class. tickers(as_of) returns the set of constituents on as_of.
    snapshot_hash() = blake2b of the full (date, ticker, included) panel."""
```

`StaticUniverse` keeps its current behavior for backwards compat (smoke
tests, the rsm_v1 retrospective). `PITUniverse` is the new path that
every v2 strategy uses.

### Pipeline integration

`pipelines/run_backtest.py` learns to handle either Universe subtype:

- `cfg.universe.type == "static"` → load `StaticUniverse.from_csv(...)`
  (existing path, unchanged).
- `cfg.universe.type == "pit"` → load `PITUniverse.from_eodhd(...)`,
  which reads the partitioned `eodhd.universe.{index}` source from the
  ParquetStore.

When the pipeline computes the signal for a window, it asks the
universe what tickers were tradeable at each day; signals operate
per-day, so this slots in naturally.

### Manifest field

`observability/run_manifest.py:RunManifest` gains:

```python
class RunManifest(BaseModel):
    # ... existing fields ...
    universe_snapshot_hash: str = ""  # default for old rows
```

SQLite migration in `data/store.py`:

```sql
ALTER TABLE run_manifests ADD COLUMN universe_snapshot_hash TEXT NOT NULL DEFAULT '';
```

The hash is computed by `Universe.snapshot_hash()` at run start and
written into the manifest start record. Static-universe runs get the
hash of the ticker list (deterministic if the CSV doesn't change).
PIT-universe runs get the hash of the full constituents panel.

### Hash determinism

Both hash methods are content-derived (Blake2b, 16-byte digest):

- `StaticUniverse.snapshot_hash` = `blake2b(json.dumps(sorted(tickers)))`
- `PITUniverse.snapshot_hash` = `blake2b(panel.write_ipc())` or
  similar deterministic-serialization of the polars frame, sorted by
  (date, ticker) for stability.

A user adding a single ticker to a static snapshot CSV gets a different
hash. A vendor revising historical S&P 500 constituents (which has
happened) shows up as a different PIT panel hash.

## Migration

1. Implement `PITUniverse` and `snapshot_hash` on `Universe` skeletons
   (this slice, no data ingest yet).
2. Add `universe_snapshot_hash` to `RunManifest` + SQLite schema. Old
   manifests get empty-string default.
3. Pipeline computes and persists the hash at run start; tear sheet
   surfaces the short hash in the header (similar to git_sha[:12]).
4. After Phase B data ingest, every v2 backtest uses `PITUniverse` and
   gets a real panel hash.

## Out of scope

- A general `Universe` abstraction beyond static and PIT — sectors,
  factor-tilted, etc. Add when needed.
- Cross-universe joins (e.g., "Russell 1000 AND has WSB chatter"). The
  static + PIT pair covers what v2a/v2b need; composite universes can
  be a follow-up.
- Mutable-during-backtest universe (the universe panel is treated as
  static *data*; if EODHD revises history, that's a new data ingest,
  a new hash, and a new manifest).

## References

- `src/supertrader/data/universe.py` — module being extended.
- `src/supertrader/data/sources/universe_eodhd.py` — new source (this
  slice ships the stub; Phase B implements it).
- `src/supertrader/observability/run_manifest.py:RunManifest` — schema
  being extended.
- `src/supertrader/data/store.py:SCHEMA_SQL` — sqlite migration.
- ADR 0004 (static universe, kept for smokes).
- ADR 0007 (upgrade path, this ADR fulfills it).
- ADR 0008 (data subscription that funds the PIT panel).
- `docs/known-limitations.md` #1, #2.
