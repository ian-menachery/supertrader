"""Typer entry point. Stub; full CLI is wired in Week 4."""

from __future__ import annotations

import typer

from supertrader._version import __version__

app = typer.Typer(
    name="supertrader",
    help="Personal quantitative research platform.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the installed supertrader version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
