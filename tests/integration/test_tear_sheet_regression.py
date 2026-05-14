"""Golden-snapshot regression test for the tear sheet renderer.

Renders the tear sheet for a deterministic synthetic fixture, normalizes the
HTML output into a stable JSON shape (metrics rows, section markers, monthly
returns, plot-shape sanity checks), and diffs against the on-disk golden
under `tests/golden/tear_sheets/`.

PNG byte streams are not snapshot-compared directly — matplotlib's PNG output
varies subtly across versions/platforms. Instead the snapshot records that
each plot section exists and that the embedded base64 string exceeds a
sanity-floor in length, so a broken plot pipeline (e.g., empty figure) still
trips the regression.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from deepdiff import DeepDiff

from supertrader.backtest.engine import BacktestResult
from supertrader.backtest.report import render_tear_sheet
from supertrader.observability.run_manifest import RunManifest

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden" / "tear_sheets"
GOLDEN_PATH = GOLDEN_DIR / "rsm_v1_smoke_synthetic.json"


def _build_fixture_result(start: str, drift: float, seed: int) -> BacktestResult:
    """Build a deterministic BacktestResult from a fixed RNG seed."""
    import numpy as np

    rng = np.random.default_rng(seed)
    idx = pd.date_range(start=start, periods=60, freq="B", tz="UTC")
    daily = rng.normal(loc=drift, scale=0.005, size=len(idx))
    returns = pd.Series(daily, index=idx)
    equity = (1.0 + returns).cumprod() * 1_000_000.0
    weights = pd.DataFrame(
        {"AAPL": [0.4] * len(idx), "MSFT": [-0.2] * len(idx), "NVDA": [0.3] * len(idx)},
        index=idx,
    )
    metrics = {
        "sharpe": 1.10,
        "sortino": 1.30,
        "calmar": 0.85,
        "max_drawdown": -0.045,
        "hit_rate": 0.52,
        "profit_factor": 1.25,
        "turnover_annual": 5.2,
        "gross_exposure": 0.9,
        "net_exposure": 0.5,
    }
    return BacktestResult(
        equity_curve=equity,
        returns=returns,
        weights=weights,
        metrics=metrics,
        initial_capital=1_000_000.0,
    )


def _fixture_manifest() -> RunManifest:
    return RunManifest(
        run_id="golden-fixture",
        config_path=Path("configs/runs/x.yaml"),
        config_hash="cafebabe" * 4,
        git_sha="deadbeef" * 5,
        git_dirty=False,
        python_version="3.12.4",
        supertrader_version="0.1.0",
        started_at=datetime(2026, 5, 14, 10, 0, tzinfo=UTC),
        ended_at=datetime(2026, 5, 14, 10, 5, tzinfo=UTC),
        status="ok",
        data_hashes={},
    )


def _normalize_html(html: str) -> dict[str, Any]:
    """Convert rendered HTML into a stable JSON shape for deepdiff comparison.

    Captures:
      * Section presence (which <h2> blocks landed in the output).
      * The metrics table rows (label + train/test/holdout cells).
      * The monthly-returns table rows.
      * Sanity floor on the three embedded PNG base64 strings.
    """
    out: dict[str, Any] = {}

    # Sections
    headings = re.findall(r"<h2>([^<]+)</h2>", html)
    out["sections"] = headings

    # Metrics table: first table after "Metrics" heading
    metrics_tbody = re.search(r"<h2>Metrics</h2>.*?<tbody>(.*?)</tbody>", html, re.DOTALL)
    assert metrics_tbody, "metrics table missing from rendered HTML"
    rows: list[dict[str, str]] = []
    for tr in re.finditer(r"<tr>(.*?)</tr>", metrics_tbody.group(1), re.DOTALL):
        cells = re.findall(r"<td>([^<]*)</td>", tr.group(1))
        if len(cells) == 4:
            rows.append(
                {"label": cells[0], "train": cells[1], "test": cells[2], "holdout": cells[3]}
            )
    out["metrics_rows"] = rows

    # Monthly returns: tbody after "Monthly returns" heading
    monthly_tbody = re.search(r"<h2>Monthly returns</h2>.*?<tbody>(.*?)</tbody>", html, re.DOTALL)
    assert monthly_tbody, "monthly returns table missing from rendered HTML"
    monthly: list[dict[str, str]] = []
    for tr in re.finditer(r"<tr>(.*?)</tr>", monthly_tbody.group(1), re.DOTALL):
        cells = re.findall(r"<td>([^<]*)</td>", tr.group(1))
        if len(cells) == 2:
            monthly.append({"month": cells[0], "value": cells[1]})
    out["monthly_returns"] = monthly

    # Plot images: sanity floor (each base64 string should decode to a real PNG)
    images = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", html)
    plot_info: list[dict[str, int]] = []
    for blob in images:
        decoded = base64.b64decode(blob)
        assert decoded.startswith(b"\x89PNG\r\n"), "embedded image is not a valid PNG"
        plot_info.append({"png_size_floor": (len(decoded) // 1000) * 1000})
    out["plots"] = plot_info

    return out


@pytest.fixture
def rendered_normalized(tmp_path: Path) -> dict[str, Any]:
    train = _build_fixture_result("2024-01-02", drift=0.0005, seed=1)
    test = _build_fixture_result("2024-04-01", drift=0.0008, seed=2)
    holdout = _build_fixture_result("2024-07-01", drift=-0.0002, seed=3)
    manifest = _fixture_manifest()
    out_path = tmp_path / "tear_sheet.html"
    render_tear_sheet(train=train, test=test, holdout=holdout, manifest=manifest, out_path=out_path)
    return _normalize_html(out_path.read_text(encoding="utf-8"))


def test_tear_sheet_matches_golden(
    rendered_normalized: dict[str, Any], pytestconfig: pytest.Config
) -> None:
    """The normalized tear sheet must match the on-disk golden, modulo nothing.

    To regenerate the golden after an intentional change:
        UPDATE_GOLDEN=1 uv run pytest tests/integration/test_tear_sheet_regression.py
    """
    import os

    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(
            json.dumps(rendered_normalized, indent=2, sort_keys=True), encoding="utf-8"
        )
        pytest.skip("regenerated golden; rerun without UPDATE_GOLDEN to enforce")

    assert GOLDEN_PATH.exists(), (
        f"golden snapshot missing at {GOLDEN_PATH}; regenerate with UPDATE_GOLDEN=1"
    )
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    diff = DeepDiff(golden, rendered_normalized, ignore_order=False)
    assert not diff, f"tear sheet diverged from golden:\n{diff}"
