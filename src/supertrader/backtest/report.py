"""Render the HTML tear sheet for a `BacktestRunOutput`.

The tear sheet is the only human-facing output of a run. Goals:

  * Single file. No external JS, no external CSS, no separate PNGs — everything
    base64-embedded so `data/runs/<run_id>/tear_sheet.html` opens correctly on
    any machine that has the file.
  * Deterministic. Matplotlib figure size and dpi are fixed so a golden
    snapshot of the rendered byte stream is stable across runs (modulo the
    minor non-determinism in PNG encoding, which the golden test hashes around).
  * Loud about caveats. The survivorship-bias warning from `StaticUniverse`
    prints above the metrics — see ADR 0004.
"""

from __future__ import annotations

import base64
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from supertrader.data.universe import SURVIVORSHIP_WARNING

if TYPE_CHECKING:
    from supertrader.backtest.engine import BacktestResult
    from supertrader.observability.run_manifest import RunManifest


# Display order + label/format for each metric we know how to render. Metrics
# missing from a `BacktestResult.metrics` dict fall through as "—".
_METRIC_SPEC: list[tuple[str, str, str]] = [
    ("sharpe", "Sharpe", "{:.2f}"),
    ("sortino", "Sortino", "{:.2f}"),
    ("calmar", "Calmar", "{:.2f}"),
    ("max_drawdown", "Max drawdown", "{:.2%}"),
    ("hit_rate", "Hit rate", "{:.1%}"),
    ("profit_factor", "Profit factor", "{:.2f}"),
    ("turnover_annual", "Turnover (annualized)", "{:.2f}"),
    ("gross_exposure", "Gross exposure (mean)", "{:.2%}"),
    ("net_exposure", "Net exposure (mean)", "{:.2%}"),
]

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_FIG_W = 10.0
_FIG_H = 4.0
_FIG_DPI = 100


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        keep_trailing_newline=False,
    )


def _fig_to_b64(fig: Any) -> str:
    """Encode a matplotlib figure as a base64 PNG string."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=_FIG_DPI, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _format_metric(value: object, fmt: str) -> str:
    """Format a metric cell or return a dash when it's missing/non-numeric."""
    if value is None:
        return "—"
    if not isinstance(value, (int, float)):
        return str(value)
    try:
        return fmt.format(float(value))
    except (TypeError, ValueError):
        return str(value)


def _build_metrics_rows(
    train: BacktestResult,
    test: BacktestResult,
    holdout: BacktestResult | None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    holdout_metrics = holdout.metrics if holdout is not None else {}
    for key, label, fmt in _METRIC_SPEC:
        rows.append(
            {
                "label": label,
                "train": _format_metric(train.metrics.get(key), fmt),
                "test": _format_metric(test.metrics.get(key), fmt),
                "holdout": _format_metric(holdout_metrics.get(key), fmt)
                if holdout is not None
                else "—",
            }
        )
    return rows


def _concat_equity(
    train: BacktestResult,
    test: BacktestResult,
    holdout: BacktestResult | None,
) -> tuple[pd.Series, list[pd.Timestamp]]:
    """Concatenate equity curves across windows, returning (series, boundary_xs).

    Each window's equity curve is rescaled so it starts at the previous window's
    final value, producing a continuous capital trajectory across train → test
    → holdout. Boundary xs are the timestamps where the windows meet.
    """
    parts: list[pd.Series] = []
    boundaries: list[pd.Timestamp] = []
    base = 1.0
    for result in (train, test, holdout):
        if result is None:
            continue
        if result.equity_curve.empty:
            continue
        scaled = result.equity_curve / float(result.equity_curve.iloc[0]) * base
        parts.append(scaled)
        base = float(scaled.iloc[-1])
        boundaries.append(scaled.index[-1])
    if not parts:
        return pd.Series(dtype=float), []
    full = pd.concat(parts)
    return full, boundaries[:-1]  # last "boundary" is just the end of the series


def _render_equity_curve(
    train: BacktestResult,
    test: BacktestResult,
    holdout: BacktestResult | None,
) -> str:
    equity, boundaries = _concat_equity(train, test, holdout)
    fig, ax = plt.subplots(figsize=(_FIG_W, _FIG_H))
    if equity.empty:
        ax.text(0.5, 0.5, "no equity data", ha="center", va="center")
    else:
        ax.plot(equity.index, equity.to_numpy(), color="#1f77b4", linewidth=1.4)
        for b in boundaries:
            ax.axvline(b, color="#888", linestyle="--", linewidth=0.8)  # type: ignore[arg-type]
        ax.set_ylabel("equity (rebased)")
        ax.grid(True, alpha=0.3)
    ax.set_title("Equity curve")
    return _fig_to_b64(fig)


def _render_drawdown(
    train: BacktestResult,
    test: BacktestResult,
    holdout: BacktestResult | None,
) -> str:
    equity, _ = _concat_equity(train, test, holdout)
    fig, ax = plt.subplots(figsize=(_FIG_W, _FIG_H))
    if equity.empty:
        ax.text(0.5, 0.5, "no equity data", ha="center", va="center")
    else:
        peak = equity.cummax()
        drawdown = equity / peak - 1.0
        ax.fill_between(drawdown.index, drawdown.to_numpy(), 0, color="#d62728", alpha=0.4)
        ax.set_ylabel("drawdown")
        ax.grid(True, alpha=0.3)
    ax.set_title("Drawdown")
    return _fig_to_b64(fig)


def _render_exposure(
    train: BacktestResult,
    test: BacktestResult,
    holdout: BacktestResult | None,
) -> str:
    """Plot gross (sum |w|) and net (sum w) exposure across all windows."""
    frames: list[pd.DataFrame] = []
    for result in (train, test, holdout):
        if result is None or result.weights.empty:
            continue
        frames.append(result.weights)
    fig, ax = plt.subplots(figsize=(_FIG_W, _FIG_H))
    if not frames:
        ax.text(0.5, 0.5, "no weight data", ha="center", va="center")
    else:
        weights = pd.concat(frames)
        gross = weights.abs().sum(axis=1)
        net = weights.sum(axis=1)
        ax.plot(gross.index, gross.to_numpy(), label="gross", color="#2ca02c", linewidth=1.2)
        ax.plot(net.index, net.to_numpy(), label="net", color="#9467bd", linewidth=1.2)
        ax.axhline(0, color="#444", linewidth=0.6)
        ax.set_ylabel("exposure")
        ax.legend(loc="upper right")
        ax.grid(True, alpha=0.3)
    ax.set_title("Exposure")
    return _fig_to_b64(fig)


def _build_monthly_returns(
    train: BacktestResult,
    test: BacktestResult,
    holdout: BacktestResult | None,
) -> list[dict[str, str]]:
    parts: list[pd.Series] = []
    for result in (train, test, holdout):
        if result is None or result.returns.empty:
            continue
        parts.append(result.returns)
    if not parts:
        return []
    combined = pd.concat(parts).sort_index()
    monthly = (1.0 + combined).resample("ME").prod() - 1.0
    return [{"month": idx.strftime("%Y-%m"), "value": f"{val:.2%}"} for idx, val in monthly.items()]


def render_tear_sheet(
    *,
    train: BacktestResult,
    test: BacktestResult,
    holdout: BacktestResult | None,
    manifest: RunManifest,
    out_path: Path,
) -> Path:
    """Render the tear sheet for a backtest run and write to `out_path`.

    Returns the path that was written, for chaining and logging.
    """
    template = _env().get_template("tear_sheet.html.j2")
    html = template.render(
        run_id=manifest.run_id,
        config_hash_short=manifest.config_hash[:16],
        git_sha_short=manifest.git_sha[:12],
        git_dirty=manifest.git_dirty,
        python_version=manifest.python_version,
        supertrader_version=manifest.supertrader_version,
        started_at=manifest.started_at.isoformat(timespec="seconds"),
        ended_at=manifest.ended_at.isoformat(timespec="seconds")
        if manifest.ended_at is not None
        else None,
        status=manifest.status,
        universe_warning=SURVIVORSHIP_WARNING,
        metrics_rows=_build_metrics_rows(train, test, holdout),
        equity_png_b64=_render_equity_curve(train, test, holdout),
        drawdown_png_b64=_render_drawdown(train, test, holdout),
        exposure_png_b64=_render_exposure(train, test, holdout),
        monthly_returns=_build_monthly_returns(train, test, holdout),
        generated_at=datetime.now(tz=UTC).isoformat(timespec="seconds"),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
