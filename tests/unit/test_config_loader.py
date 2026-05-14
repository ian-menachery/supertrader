"""Tests for `config.loader.load_run_config` and its inheritance machinery."""

from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest

from supertrader.config import ConfigCycleError, RunConfig, deep_merge, load_run_config


def _write(path: Path, content: str) -> Path:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return path


class TestDeepMerge:
    def test_disjoint_keys(self) -> None:
        assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_child_overrides_scalar(self) -> None:
        assert deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested_dict_recurses(self) -> None:
        parent = {"costs": {"commission_bps": 1.0, "borrow_bps_annual": 50.0}}
        child = {"costs": {"commission_bps": 0.5}}
        merged = deep_merge(parent, child)
        assert merged == {"costs": {"commission_bps": 0.5, "borrow_bps_annual": 50.0}}

    def test_list_replaced_not_concatenated(self) -> None:
        # Lists must be replaced — concatenation would silently double-add things.
        parent = {"signals": [{"name": "a"}, {"name": "b"}]}
        child = {"signals": [{"name": "c"}]}
        merged = deep_merge(parent, child)
        assert merged == {"signals": [{"name": "c"}]}

    def test_dict_overrides_scalar(self) -> None:
        # Type mismatch: child wins, even if the shape is incompatible.
        assert deep_merge({"a": 1}, {"a": {"b": 2}}) == {"a": {"b": 2}}


class TestLoaderBasics:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Run config not found"):
            load_run_config(tmp_path / "nope.yaml")

    def test_top_level_must_be_mapping(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "bad.yaml", "- just-a-list\n- of-items\n")
        with pytest.raises(TypeError, match="must be a mapping"):
            load_run_config(path)

    def test_extends_target_missing_raises(self, tmp_path: Path) -> None:
        child = _write(
            tmp_path / "child.yaml",
            "extends: ./missing.yaml\nrun_id: c\n",
        )
        with pytest.raises(FileNotFoundError, match=r"missing\.yaml"):
            load_run_config(child)


class TestExtendsChain:
    def _write_base(self, dir_: Path) -> Path:
        return _write(
            dir_ / "base.yaml",
            """
            universe:
              type: static
              max_market_cap_usd: 10000000000.0
            backtest:
              initial_capital: 1000000.0
              rebalance_frequency: 1d
              costs:
                commission_bps: 1.0
                slippage_bps_base: 3.0
            """,
        )

    def _write_child(self, dir_: Path) -> Path:
        return _write(
            dir_ / "child.yaml",
            """
            extends: ./base.yaml
            run_id: test-run
            data_sources:
              - type: yfinance.prices.daily
            signals:
              - type: reddit_sentiment
                name: sent
            strategy:
              type: mean_reversion
              signals: [sent]
            backtest:
              start: 2024-01-01
              end: 2024-12-31
              train_end: 2024-06-30
              test_end: 2024-09-30
              costs:
                commission_bps: 0.5
            execution:
              type: backtest
            """,
        )

    def test_full_chain_validates_to_runconfig(self, tmp_path: Path) -> None:
        self._write_base(tmp_path)
        child = self._write_child(tmp_path)
        cfg = load_run_config(child)
        assert isinstance(cfg, RunConfig)
        assert cfg.run_id == "test-run"
        # Child overrides commission_bps but inherits slippage_bps_base from base.
        assert cfg.backtest.costs.commission_bps == 0.5
        assert cfg.backtest.costs.slippage_bps_base == 3.0
        # Dates land
        assert cfg.backtest.start == date(2024, 1, 1)
        assert cfg.backtest.train_end == date(2024, 6, 30)

    def test_two_level_chain(self, tmp_path: Path) -> None:
        grand = _write(
            tmp_path / "grand.yaml",
            """
            universe: {type: static}
            backtest:
              initial_capital: 500000.0
              costs: {commission_bps: 2.0}
            """,
        )
        parent = _write(
            tmp_path / "parent.yaml",
            """
            extends: ./grand.yaml
            backtest:
              rebalance_frequency: 1d
              costs: {commission_bps: 1.0, slippage_bps_base: 3.0}
            """,
        )
        child = _write(
            tmp_path / "child.yaml",
            """
            extends: ./parent.yaml
            run_id: c
            data_sources: [{type: yfinance.prices.daily}]
            signals: [{type: reddit_sentiment, name: s}]
            strategy: {type: mean_reversion, signals: [s]}
            backtest:
              start: 2024-01-01
              end: 2024-12-31
              train_end: 2024-06-30
              test_end: 2024-09-30
            execution: {type: backtest}
            """,
        )
        _ = grand, parent  # silence unused
        cfg = load_run_config(child)
        # Grand: commission 2.0; Parent overrides 1.0; child stays at 1.0
        assert cfg.backtest.costs.commission_bps == 1.0
        # Grand: initial_capital 500k; parent doesn't change; child doesn't change
        assert cfg.backtest.initial_capital == 500000.0
        # Parent: slippage 3.0; survives untouched
        assert cfg.backtest.costs.slippage_bps_base == 3.0


class TestCycleDetection:
    def test_self_cycle_raises(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "self.yaml", "extends: ./self.yaml\nrun_id: x\n")
        with pytest.raises(ConfigCycleError, match="cycle"):
            load_run_config(path)

    def test_two_node_cycle_raises(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.yaml", "extends: ./b.yaml\n")
        _write(tmp_path / "b.yaml", "extends: ./a.yaml\nrun_id: y\n")
        with pytest.raises(ConfigCycleError, match="cycle"):
            load_run_config(tmp_path / "a.yaml")


class TestSmokeConfigOnDisk:
    """Loads the actual configs/runs/smoke.yaml from the repo."""

    def test_smoke_yaml_loads(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        smoke = repo_root / "configs" / "runs" / "smoke.yaml"
        cfg = load_run_config(smoke)
        assert cfg.run_id == "smoke-v1"
        assert cfg.universe.type == "static"
        assert len(cfg.data_sources) == 2
        assert cfg.strategy.signals == ["reddit_sentiment_v1"]
        # base.yaml provided the universe filters
        assert cfg.universe.max_market_cap_usd == 10_000_000_000.0
