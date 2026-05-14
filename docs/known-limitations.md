# Known limitations

A frank list of issues with the current research setup. Anything in this file
should be read before drawing conclusions from a tear sheet. Ranked roughly by
severity-of-impact-on-verdict-credibility.

The ADRs in `docs/adr/` cover deliberate architectural choices; this file is
where the *honesty about what the choices cost us* lives. Update it as we go.

---

## 1. Universe is selection-biased for sentiment-relevance

The 34-ticker snapshot at `configs/universe/snapshot_2026_05_14.csv` is
heavily weighted toward meme-stock-adjacent names (GME, AMC, MARA, COIN,
SOFI, BB, NOK, PLTR, LCID, RIVN, etc.) — exactly the tickers /r/wallstreetbets
talks about most.

**Why it matters.** A Reddit-sentiment strategy is being tested on the
universe pre-filtered for Reddit chatter. A positive result doesn't
generalize to "Reddit sentiment is informative on US equities" — only to
"on the names where WSB talks the most, sentiment mean-reversion pays."
A negative result is also weaker than it looks: it doesn't show sentiment is
uninformative, only that the obvious mean-reversion of the most-discussed
names doesn't work.

**Mitigation (deferred).** A second universe of Russell-1000 small/mid-cap
names *regardless* of WSB relevance, with the same strategy applied,
unlocks the broader question. Out of scope for the 8-week window.

## 2. Survivorship bias is plausibly 3-8%/yr, not 1-3%

ADR 0004 quotes a "~1-3% annual" upward bias from the post-hoc Russell-1000
snapshot. That number comes from Lo & Hasanhodzic on broad indexes (~1000
names). On a 34-ticker small/mid-cap subset that *excludes every delisting
from 2022-2024*, the effective bias is larger. The snapshot is dated
2026-05-14: every name currently on the list survived through to today.
Names that flirted with near-bankruptcy (Lucid, Rivian) but didn't fail
still over-represent the lucky-side of the distribution.

**Why it matters.** The backtest's reported alpha is biased upward by some
amount we cannot measure exactly without a true point-in-time universe.
On a meme-stock-adjacent universe, that bias is probably 3-8%/yr, not 1-3%.

**Mitigation.** ADR 0007 records the upgrade path (EODHD subscription or
EDGAR-built PIT). The trigger condition — test Sharpe > 0.8 — only fires
after an honest backtest, which is downstream of the data backfill in
progress. Until then, results carry the more conservative caveat in writing.

## 3. Cost model likely understates round-trip costs by 50-200%

`CostsConfig.slippage_bps_base` is a flat-rate model with a √(size/ADV)
impact coefficient. Real small-cap execution has wider spreads (15-40 bps
round-trip vs. our 5-10 bps base) and asymmetric impact (you eat the offer
when buying, lift the bid when selling).

**Why it matters.** Q1 2024 smoke turnover was 117% annualized. Even a
10 bps cost underestimate eats roughly 12% of annual return. A 30 bps
underestimate eats ~35%. The simulated tear sheet flatters trade-realistic
performance unless we are conservative.

**Mitigation.** A cost-sensitivity sweep — re-run the canonical backtest
with `slippage_bps_base` at 2× and 3× the current value, log the metric
deltas. Decision rule for verdicts: if 2× cost test Sharpe still > 0.8,
claim "tradeable"; otherwise claim "interesting but cost-sensitive."
The sweep itself is a few CLI runs; landing it as a documented procedure
in `docs/dev-notes.md` is the next step.

## 4. Holdout window (3 months) is too short to reject overfitting reliably

60 trading days × 30 positions ≈ small statistical power. Standard error of
a quarterly Sharpe estimate is roughly 1.4 even at true SR = 1. A
"good-looking" holdout SR of 1.5 could be a true SR anywhere from 0.0 to
3.0 by sampling noise alone.

**What the holdout can do.** Reject extreme overfitting (in-sample SR=4,
holdout SR=-1) — yes, the holdout catches this.

**What it cannot do.** Validate a moderate signal. A true SR=0.5 strategy
will look indistinguishable from a true SR=1.5 strategy on a 60-day window.

**Mitigation.** In every verdict write-up, the language for "holdout
passed" is: *"did not fail catastrophically."* Forward-only paper trading
is the only honest confirmation. Per the canonical plan, this is W7 work,
contingent on the canonical re-run looking interesting.

## 5. VADER + lexicon overlay accuracy is unmeasured

`configs/sentiment_lexicon.yaml` was hand-curated. We do not know the
scorer's accuracy on financial-domain sentiment. It could be 30% accurate,
60%, or 90%. ADR 0006 (`docs/adr/0006-sentiment-scorer-pluggable.md`)
defines the upgrade gate as a 500-post hand-labeled eval set at
`tests/golden/sentiment_eval_500.csv`. That file does not exist.

**Why it matters.** Without a measured baseline, we cannot defensibly
say *"VADER is good enough"* or *"we need FinBERT."* If the canonical
re-run shows a weak result, we cannot tell whether the strategy idea is
bad or the scorer is bad.

**Mitigation.** A one-day push to label 100-200 posts (not the full 500)
is enough to bound VADER's accuracy with a usable confidence interval.
Not a blocker for the canonical re-run itself, but a blocker for any
"upgrade the scorer" decision.

## 6. Simulated costs are not validated against live behavior

vectorbt fills at next-bar open with our slippage model. Real fills on
small-caps drift 5-30 bps from this. The intended check is W7 paper
trading: log every realized fill vs. the backtest's simulated fill,
compute the empirical slippage distribution after ~50 trades, and update
the cost model.

**Why it matters.** Until paper trading runs, the cost model is an
untested assumption. If the canonical re-run produces an unfavorable
verdict and paper trading is deprioritized, this gap stays unverified.

**Mitigation.** When (if) paper trading runs, instrument every order with
both simulated and realized fill prices. Even a small live sample
(~20 trades) significantly narrows the slippage uncertainty.

## 7. PIT view may have look-ahead at the day boundary

`PITStoreView.scan(source_id)` filters by timestamp ≤ `as_of`. For
`yfinance.prices.daily` the timestamp column is `date`, so `as_of = T`
includes T's close price — which is correct *for signal computation that
runs after close*. The risk is a signal that accidentally uses T's close
to score a trade that fills at T's open (T-relative): that would be a
real look-ahead.

**Why it matters.** Even tiny look-ahead biases compound into significant
backtest inflation over thousands of trades.

**Mitigation.** Add a unit test that asserts
`RedditSentimentSignal.compute(as_of=T)` only reads `arctic_shift.posts`
rows (i.e., never references the prices source). The current signal does
not appear to use prices at all, so the test is likely a no-op pin —
but worth landing before the canonical re-run as a regression guard.

## 8. Single-strategy framework with no fallback

The 8-week plan only evaluates one strategy. If Reddit-sentiment
mean-reversion fails, no other strategy is queued. ADR 0006's pluggable
scorer is a parameter tweak, not an alternative thesis. Form 4 insider
clustering is a Week-8 *sketch* (~50-line stub).

**Why it matters.** A research project that ends with "the only strategy
we tried didn't work" learns less per hour than one that tested two ideas
even imperfectly.

**Mitigation.** Acknowledged out of scope for the 8-week window. Post-W8,
the natural follow-up is to broaden — a second signal (Form 4 clustering
or rolling-z-score reversal) in addition to or instead of sentiment.

---

## Smaller concerns worth naming

- **WSB monoculture.** Universe of subreddits is just `wallstreetbets`.
  Multi-source ingest is in the source design but unused. r/stocks and
  r/investing have different sentiment-vs-price dynamics.
- **No multiple-comparisons discipline logging.** Hyperparameter sweep
  peeks aren't tracked. The intended discipline is "decide on train
  only" — but there's no enforcement. A peek at the test set during
  hyperparameter search is silent.
- **No partial-month resume.** If the streaming backfill crashes mid-month,
  that month restarts from page 1 on retry. ~25K rows is negligible, but
  worth noting if we ever ingest a denser subreddit.
- **Static-cost model has no asymmetry.** Buys and sells have the same
  base slippage in `costs.py`. Real markets cross asymmetrically.
- **VADER doesn't handle sarcasm.** WSB is sarcastic by default. "this
  stock is going to the moon" and "this stock is dead 💀" are both
  high-confidence VADER signals in opposite directions, but both can
  precede the same price move.

---

## How to update this file

When you discover a new limitation, add it. When a limitation is mitigated
(e.g., the eval set lands), strike it out with a date and a one-line note.
Don't delete entries: keeping the record of "we used to have this problem,
here's when we fixed it" is more useful than a clean-looking file.

When the canonical re-run completes and we have a verdict, link to it from
the relevant entries here ("see `data/runs/rsm-v1-backtest/verdict.md`
for the numbers under this caveat").
