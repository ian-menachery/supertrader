"""Unit tests for the HTML tear sheet renderer."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from supertrader.backtest.engine import BacktestResult
from supertrader.backtest.report import render_tear_sheet
from supertrader.observability.run_manifest import RunManifest


def _synthetic_result(start: str, n_days: int = 60, drift: float = 0.0005) -> BacktestResult:
    """A small but plausible BacktestResult fixture."""
    idx = pd.date_range(start=start, periods=n_days, freq="B", tz="UTC")
    returns = pd.Series([drift] * n_days, index=idx)
    equity = (1.0 + returns).cumprod() * 1_000_000.0
    weights = pd.DataFrame({"AAPL": [0.5] * n_days, "MSFT": [-0.3] * n_days}, index=idx)
    metrics = {
        "sharpe": 1.23,
        "sortino": 1.45,
        "calmar": 0.78,
        "max_drawdown": -0.0405,
        "hit_rate": 0.55,
        "profit_factor": 1.32,
        "turnover_annual": 8.4,
        "gross_exposure": 0.8,
        "net_exposure": 0.0,
    }
    return BacktestResult(
        equity_curve=equity,
        returns=returns,
        weights=weights,
        metrics=metrics,
        initial_capital=1_000_000.0,
    )


@pytest.fixture
def manifest() -> RunManifest:
    return RunManifest(
        run_id="test-run-1",
        config_path=Path("configs/runs/x.yaml"),
        config_hash="0123456789abcdef" * 2,
        git_sha="abcdef" * 7 + "ab",  # 40 chars
        git_dirty=False,
        python_version="3.12.4",
        supertrader_version="0.1.0",
        started_at=datetime(2026, 5, 14, 10, 0, tzinfo=UTC),
        ended_at=datetime(2026, 5, 14, 10, 5, tzinfo=UTC),
        status="ok",
        data_hashes={},
    )


def test_render_writes_html_file(tmp_path: Path, manifest: RunManifest) -> None:
    train = _synthetic_result("2024-01-02")
    test = _synthetic_result("2024-04-01", drift=0.001)
    holdout = _synthetic_result("2024-07-01", drift=-0.0002)
    out = tmp_path / "tear_sheet.html"
    written = render_tear_sheet(
        train=train, test=test, holdout=holdout, manifest=manifest, out_path=out
    )
    assert written == out
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "test-run-1" in content
    assert manifest.config_hash[:16] in content
    assert "WARNING: Universe is a post-hoc" in content


def test_render_embeds_three_plot_images(tmp_path: Path, manifest: RunManifest) -> None:
    train = _synthetic_result("2024-01-02")
    test = _synthetic_result("2024-04-01")
    holdout = _synthetic_result("2024-07-01")
    out = tmp_path / "tear_sheet.html"
    render_tear_sheet(train=train, test=test, holdout=holdout, manifest=manifest, out_path=out)
    content = out.read_text(encoding="utf-8")
    images = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", content)
    assert len(images) == 3
    # All three should be non-empty PNG payloads
    for blob in images:
        assert len(blob) > 1000  # PNGs of plots, even tiny ones, exceed 1KB


def test_render_handles_no_holdout(tmp_path: Path, manifest: RunManifest) -> None:
    train = _synthetic_result("2024-01-02")
    test = _synthetic_result("2024-04-01")
    out = tmp_path / "tear_sheet.html"
    render_tear_sheet(train=train, test=test, holdout=None, manifest=manifest, out_path=out)
    content = out.read_text(encoding="utf-8")
    # The holdout column should be present in the table header but with — placeholders
    assert "HOLDOUT" in content
    # At least one em-dash placeholder in the metrics table for the holdout column
    assert "—" in content


def test_render_marks_dirty_git_in_header(tmp_path: Path, manifest: RunManifest) -> None:
    dirty_manifest = manifest.model_copy(update={"git_dirty": True})
    train = _synthetic_result("2024-01-02")
    test = _synthetic_result("2024-04-01")
    out = tmp_path / "tear_sheet.html"
    render_tear_sheet(train=train, test=test, holdout=None, manifest=dirty_manifest, out_path=out)
    content = out.read_text(encoding="utf-8")
    assert "DIRTY" in content


def test_render_includes_monthly_returns(tmp_path: Path, manifest: RunManifest) -> None:
    train = _synthetic_result("2024-01-02", n_days=120)
    test = _synthetic_result("2024-06-15", n_days=40)
    out = tmp_path / "tear_sheet.html"
    render_tear_sheet(train=train, test=test, holdout=None, manifest=manifest, out_path=out)
    content = out.read_text(encoding="utf-8")
    # Should contain at least 2024-01 through 2024-07
    months_found = re.findall(r"20\d\d-\d\d", content)
    assert "2024-01" in months_found
    assert "2024-07" in months_found
