"""Decompose a backtest's test-window returns by sector.

Re-executes the given config in-process (same config_hash → counts as
the same test-set peek, NOT a new one) and slices the per-position
returns by sector via the universe metadata.

Use case: "When v2 volume_surge showed test Sharpe +0.89, was the gain
concentrated in Technology or distributed across sectors?"

Usage::

    uv run python scripts/decompose_by_sector.py \\
        --config configs/runs/v2_tech_volume_surge.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from supertrader.backtest.sector_decomp import (
    decompose_by_sector,
    format_table,
    sector_lookup,
)
from supertrader.data.universe import StaticUniverse
from supertrader.pipelines.run_backtest import run_backtest

REPO_ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the run config YAML to decompose.",
    )
    parser.add_argument(
        "--universe",
        type=Path,
        default=None,
        help="Universe snapshot CSV. If omitted, uses the config's universe.snapshot_path "
        "or the repo default.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=REPO_ROOT / "data",
        help="ParquetStore root (default: repo/data/)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger("decompose_by_sector")

    log.info("re-executing config (same config_hash — single peek)")
    out = run_backtest(
        args.config,
        include_holdout=False,
        data_dir=args.data_dir,
        universe_path=args.universe,
        allow_dirty=True,
    )

    # Resolve the universe path the pipeline used.
    universe_path = args.universe
    if universe_path is None:
        if out.config.universe.snapshot_path is not None:
            cfg_path = out.config.universe.snapshot_path
            universe_path = cfg_path if cfg_path.is_absolute() else REPO_ROOT / cfg_path
        else:
            universe_path = REPO_ROOT / "configs" / "universe" / "snapshot_2026_05_14.csv"

    universe = StaticUniverse.from_csv(universe_path)
    ticker_to_sector = sector_lookup(universe)

    # Reconstruct the test-window prices (the pipeline already does this
    # internally but doesn't expose it; we re-load via the same logic).
    from supertrader.data.store import ParquetStore  # noqa: PLC0415
    from supertrader.pipelines.run_backtest import _load_prices  # noqa: PLC0415

    store = ParquetStore(args.data_dir)
    test_prices = _load_prices(store, out.config.backtest.train_end, out.config.backtest.test_end)

    contribs = decompose_by_sector(
        weights=out.test_result.weights,
        prices=test_prices,
        ticker_to_sector=ticker_to_sector,
        execution_delay_bars=out.config.backtest.execution_delay_bars,
    )

    headline_sharpe = out.test_result.metrics.get("sharpe", float("nan"))
    print(f"\n--- TEST window sector decomposition for run_id={out.config.run_id} ---")
    print(f"full-window Sharpe (from manifest): {headline_sharpe:.4f}")
    print()
    print(format_table(contribs))
    print()

    # Optionally write to the run directory.
    run_dir = args.data_dir / "runs" / out.config.run_id
    if run_dir.exists():
        out_md = run_dir / "sector_decomp.md"
        out_md.write_text(
            "# Sector decomposition\n\n"
            f"Run: `{out.config.run_id}`  ·  config_hash: `{out.config_hash[:16]}`\n\n"
            f"```\n{format_table(contribs)}\n```\n",
            encoding="utf-8",
        )
        log.info("wrote sector decomposition to %s", out_md)

    return 0


if __name__ == "__main__":
    sys.exit(main())
