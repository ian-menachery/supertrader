# ADR 0003 — Redline integration via Parquet export

**Status**: Accepted
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
