"""Typer entry point."""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from supertrader._version import __version__
from supertrader.pipelines.run_backtest import run_backtest

app = typer.Typer(
    name="supertrader",
    help="Personal quantitative research platform.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the installed supertrader version."""
    typer.echo(__version__)


@app.command()
def backtest(
    config: Path = typer.Option(..., "--config", help="Path to a RunConfig YAML"),
    include_holdout: bool = typer.Option(
        False,
        "--include-holdout",
        help="DANGER: evaluate the holdout window (one-shot per config_hash).",
    ),
    allow_dirty: bool = typer.Option(
        False,
        "--allow-dirty/--no-allow-dirty",
        help="Run even if the git tree has uncommitted changes (the dirty "
        "flag is still recorded on the manifest).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="DEBUG-level logs"),
) -> None:
    """Run a backtest end-to-end against the on-disk store."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    result = run_backtest(config, include_holdout=include_holdout, allow_dirty=allow_dirty)
    typer.echo("")
    typer.echo("=" * 60)
    typer.echo(f"run_id:      {result.config.run_id}")
    typer.echo(f"config_hash: {result.config_hash}")
    typer.echo(f"metrics:     {result.metrics_path}")
    typer.echo(f"tear_sheet:  {result.tear_sheet_path}")
    typer.echo("=" * 60)
    for window_name, window_result in [
        ("TRAIN", result.train_result),
        ("TEST", result.test_result),
        ("HOLDOUT", result.holdout_result),
    ]:
        if window_result is None:
            typer.echo(f"\n[{window_name}]  (not evaluated)")
            continue
        typer.echo(f"\n[{window_name}]")
        for key, value in window_result.metrics.items():
            try:
                typer.echo(f"  {key:24s} {float(value):>12.4f}")
            except (TypeError, ValueError):
                typer.echo(f"  {key:24s} {value!s:>12}")


if __name__ == "__main__":
    app()
