# ADR 0003 — Redline integration via Parquet export

**Status**: Superseded — redline not used by supertrader (2026-05-14 pivot)
**Date**: 2026-05-14

## Context

The redline project (`~/projects/redline`) already extracts Form 4 insider
transactions and computes anomaly signals. Supertrader needs Form 4 data to feed
a future insider-clustering strategy.

Three options were considered: (a) Parquet export via a CLI contract, (b) read-only
SQLite access to redline's database, (c) install redline as a Python package and
import internals.

## Decision

**Parquet export.** Redline will gain an `export_form4 --since DATE --to PATH` CLI
that writes a versioned Parquet file. Supertrader's `Form4DataSource` reads that
Parquet.

## Consequences

- Clean decoupling — schema changes in redline are explicit (Parquet schema version),
  not silent.
- Two-step refresh: run redline export, then run supertrader ingest.
- Action item: implement the redline CLI before Week 8.
- Both projects remain independently versioned, independently testable.

## Superseded — 2026-05-14 strategic pivot

Verified during the v2a Form 4 planning session that redline's
`form4_transactions` table holds **170 rows across 5 issuers** (date
range 2024-11-01 → 2026-05-08). That's enough for redline's own demo
purposes and nowhere near the cross-sectional breadth a Form 4 insider-
clustering study needs. Backfilling redline to a useful state is a
multi-week side project that delegates critical-path work to another
repo.

Simultaneously, supertrader's signal-source direction pivoted to
technical indicators (price action, volume) on US equities — Form 4 is
no longer on the active strategy roadmap.

**The boundary contract documented above remains valid** if a future
plan revisits insider-driven strategies. It is just not active work.
The `Form4RedlineSource` stub at
`src/supertrader/data/sources/form4_redline.py` stays as
`NotImplementedError` — it's a reserved interface, not a dead module.
