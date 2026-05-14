# ADR 0006 — SentimentScorer abstraction

**Status**: Accepted
**Date**: 2026-05-14

## Context

Reddit sentiment scoring spans a quality/cost frontier: VADER (fast, dumb, free),
FinBERT (medium, finance-tuned, free + compute), LLM (slow, smart, $$). For v1
we backfill ~10M Reddit posts; VADER is the only option that finishes in hours
on a laptop. But we want the option to swap in better scorers later without
rewriting the signal layer.

## Decision

Introduce `SentimentScorer` ABC with `score(texts) -> NDArray[float64]` and a
`model_version` field. Three concrete implementations:

- `VaderScorer` — v1 default, fully validated.
- `FinBertScorer` — stub in v1 (raises `NotImplementedError`).
- `LLMScorer` — stub in v1 (raises `NotImplementedError`).

`RedditSentimentSignal` accepts a scorer instance configured via YAML:

```yaml
params:
  scorer:
    type: vader        # change to "finbert" or "llm" in v2
    params: { lexicon_path: configs/sentiment_lexicon.yaml }
```

Model version contributes to signal fingerprint → cache invalidation is automatic.

## Upgrade criterion

Move to `FinBertScorer` if VADER + finance lexicon AUC < 0.55 on a hand-labeled
holdout set of 500 posts (committed at `tests/golden/sentiment_eval_500.csv` in
Week 3).

## Consequences

- One ABC, three subclasses. Trivial overhead.
- Cache invalidation is automatic when `model_version` is bumped.
- The 500-post hand-labeled set becomes a permanent benchmark, used to compare
  future scorers.
