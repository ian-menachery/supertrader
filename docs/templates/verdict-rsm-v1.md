# Verdict template — RSM v1 (Reddit-sentiment mean-reversion)

> Fill-in-the-blanks template for the first honest verdict on the canonical
> `rsm_v1_backtest.yaml` (18 mo train / 6 mo test / 3 mo holdout). Copy to
> `data/runs/rsm-v1-backtest/verdict.md` once metrics are in hand. Do not
> commit `data/`-rooted copies — `data/` is gitignored.
>
> The template's structure is the deliverable per the canonical 8-week plan
> Week 6: *"Look at results, write a tradeable / not-tradeable judgment."*

---

## Headline

- **Test Sharpe:** ___ (95% CI ± ___)
- **Test MaxDD:** ___
- **Hit rate (test):** ___
- **Universe size:** ___ tickers
- **Windows:** train ___ trading days, test ___, holdout ___ (UNTOUCHED at this stage)

One sentence: ___

## Cost sensitivity

Compare against `rsm_v1_backtest_2x_cost.yaml` and `rsm_v1_backtest_3x_cost.yaml`.

| variant | test Sharpe | test MaxDD | gross exposure |
| ------- | -----------:| ----------:| --------------:|
| 1× (canonical) | ___ | ___ | ___ |
| 2× slippage    | ___ | ___ | ___ |
| 3× slippage    | ___ | ___ | ___ |

## Verdict against the limitation-#3 rule

Decision rule (verbatim from `docs/known-limitations.md` #3):
*"if 2x-cost test Sharpe still > 0.8, claim tradeable; otherwise claim
interesting but cost-sensitive."*

- [ ] **Tradeable.** 2× cost test Sharpe ___ ≥ 0.8. Next: unlock holdout via
  one-shot `--include-holdout` evaluation, then proceed to W7 paper trading
  (Alpaca). Open follow-up plan.
- [ ] **Cost-sensitive but interesting.** 1× cost test Sharpe ___ > 0 but 2×
  cost test Sharpe ___ < 0.8. Next: log realized slippage during paper
  trading before drawing further conclusions. Hold off on the holdout
  evaluation; the cost model needs to be calibrated against ≥50 real fills
  first.
- [ ] **Negative result.** 1× cost test Sharpe ___ ≤ 0. Next: write
  postmortem at `research/03_strategy_postmortem.ipynb` covering (a) which
  hypothesis specifically failed, (b) whether the signal had information
  in-sample at all, (c) whether broader signal combinations are worth
  trying. Hold off on the holdout — touching it now wastes the one-shot.

## Honesty paragraph (mandatory)

Required reading: `docs/known-limitations.md`. The numbers above are
biased upward by at least the following factors:

- **Universe selection bias (limitation #1).** The 34-ticker universe was
  curated for sentiment-relevance. A positive verdict here says
  "sentiment mean-reversion pays on stocks WSB already talks about a
  lot," not "sentiment is informative on US equities."
- **Survivorship bias (limitation #2).** The snapshot is post-hoc as of
  2026-05-14. Every name still listed survived 2022-2024; near-bankruptcy
  scares (Lucid, Rivian) are over-represented as winners. Plausible
  upward bias on this small universe: 3–8% annualized.
- **Cost-model underestimate (limitation #3).** Even the 2× and 3× cost
  variants are guesses; only paper-trading-realized fills will calibrate
  the real cost. The cost-sensitivity table above is *sensitivity*, not
  *calibration*.
- **Holdout window too short to validate (limitation #4).** Even if the
  next step is to evaluate the holdout, "holdout passed" means "the
  result didn't fail catastrophically," not "confirmed."

The honest claim is bounded by the weakest of these caveats.

## Reproducibility footer

Pulled verbatim from `data/runs/rsm-v1-backtest/manifest.json`:

- `run_id`: ___
- `config_hash`: ___
- `git_sha`: ___
- `git_dirty`: ___
- `python_version`: ___
- `supertrader_version`: ___
- `started_at`: ___
- `ended_at`: ___
- `status`: ___
- `data_hashes`: ___ entries (see manifest.json)

Backfill window in use at run time: WSB posts 2022-01 → 2024-03;
yfinance prices for the 34-ticker universe over the same window.

## Next-action checklist

- [ ] Decision-rule selection committed via verdict file (this document).
- [ ] If tradeable: open new plan for W7 paper-trading + holdout one-shot.
- [ ] If cost-sensitive: open follow-up plan for slippage calibration.
- [ ] If negative: open follow-up plan for postmortem + signal-broadening.
- [ ] Push verdict (in `docs/` if it belongs there long-term, or attach to
  a final commit message if it's truly run-specific).
- [ ] Update `docs/dev-notes.md` with one bullet referencing the verdict
  and the decision taken.
