from __future__ import annotations

import pandas as pd
import pytest

from pairs_trading.core.framework import StrategyOutput
from pairs_trading.features.regime_overlay import (
    RegimeOverlayConfig,
    apply_regime_overlay,
    build_regime_overlay,
    classify_fred_regime,
)


def _make_fred_events(scores: dict[str, list[float]], dates: list[str] | None = None) -> pd.DataFrame:
    if dates is None:
        dates = ["2020-01-01", "2020-06-01", "2020-12-01"]
    rows: list[dict] = []
    base_date = pd.Timestamp(dates[0])
    for i, d in enumerate(dates):
        for name, vals in scores.items():
            rows.append({
                "timestamp": pd.Timestamp(d),
                "ticker": "MACRO",
                "event_score": vals[i] if i < len(vals) else 0.0,
                "confidence": 0.8,
                "event_type": f"fred_{name.lower()}",
                "source": "fred",
                "form": "",
                "series_name": name,
                "raw_value": vals[i] if i < len(vals) else 0.0,
            })
    return pd.DataFrame(rows)


def _make_strategy_output(index: pd.DatetimeIndex | None = None) -> StrategyOutput:
    if index is None:
        index = pd.DatetimeIndex(["2020-01-01", "2020-06-01", "2020-12-01"])
    n = len(index)
    zeros = [0.0] * n
    ones = [1.0] * n
    neg_ones = [-1.0] * n
    frame = pd.DataFrame({
        "signal": ones if n >= 1 else [],
        "forecast": [1.5 if i == 0 else (-1.5 if i == n - 1 and n > 2 else 0.0) for i in range(n)],
        "position": [1000.0 if i == 0 else (-1000.0 if i == n - 1 and n > 2 else 0.0) for i in range(n)],
        "cost_estimate": [0.5 if i == 0 or (i == n - 1 and n > 2) else 0.0 for i in range(n)],
        "unit_return": [0.01 if i == 0 else (-0.01 if i == n - 1 and n > 2 else 0.0) for i in range(n)],
        "gross_return": [10.0 if i == 0 else (-10.0 if i == n - 1 and n > 2 else 0.0) for i in range(n)],
        "turnover": [0.1 if i == 0 else (0.1 if i == n - 1 and n > 2 else 0.0) for i in range(n)],
        "short_exposure": zeros,
        "gross_exposure": [1000.0] * n,
    }, index=index)
    return StrategyOutput(
        name="test",
        frame=frame,
        diagnostics={"test": True},
    ).validate(extra_columns=("unit_return", "gross_return"))


class TestClassifyFredRegime:
    def test_empty_events(self):
        events = pd.DataFrame()
        result = classify_fred_regime(events)
        assert result.empty

    def test_recession_detected(self):
        events = _make_fred_events({
            "RECESSION_PROB": [0.5, 0.0, 0.0],
            "GDP": [-0.6, 0.1, 0.2],
            "UNEMPLOYMENT": [-0.6, 0.0, 0.1],
        })
        result = classify_fred_regime(events)
        assert not result.empty
        assert result.iloc[0]["regime_label"] == "recession"

    def test_expansion_detected(self):
        events = _make_fred_events({
            "GDP": [0.5, 0.6, 0.7],
            "UNEMPLOYMENT": [0.3, 0.4, 0.5],
        })
        result = classify_fred_regime(events)
        assert not result.empty
        assert result.iloc[0]["regime_label"] == "expansion"
        assert result.iloc[0]["regime_score"] > 0

    def test_neutral_when_no_strong_signal(self):
        events = _make_fred_events({"GDP": [0.0, 0.0, 0.0]})
        result = classify_fred_regime(events)
        assert not result.empty
        assert result.iloc[0]["regime_label"] == "neutral"

    def test_recession_overrides_expansion(self):
        events = _make_fred_events({
            "RECESSION_PROB": [0.8, 0.0, 0.0],
            "GDP": [0.5, 0.5, 0.5],
        })
        result = classify_fred_regime(events)
        assert result.iloc[0]["regime_label"] == "recession"


class TestBuildRegimeOverlay:
    def test_empty_events_returns_neutral(self):
        events = pd.DataFrame()
        overlay = build_regime_overlay(
            fred_events=events,
            index=pd.DatetimeIndex(["2020-01-01"]),
            config=RegimeOverlayConfig(),
        )
        assert overlay.loc["2020-01-01", "regime_label"] == "neutral"
        assert overlay.loc["2020-01-01", "regime_score"] == 0.0

    def test_single_date_overlay(self):
        events = _make_fred_events({"GDP": [0.5, 0.6, 0.7]})
        overlay = build_regime_overlay(
            fred_events=events,
            index=pd.DatetimeIndex(["2020-06-01"]),
            config=RegimeOverlayConfig(),
        )
        assert not overlay.empty
        assert "regime_label" in overlay.columns

    def test_regime_confidence_is_bounded(self):
        events = _make_fred_events({"GDP": [0.5, 0.6, 0.7]})
        overlay = build_regime_overlay(
            fred_events=events,
            index=pd.DatetimeIndex(["2020-01-01", "2020-06-01", "2020-12-01"]),
            config=RegimeOverlayConfig(),
        )
        assert all(overlay["regime_confidence"] >= 0.0)
        assert all(overlay["regime_confidence"] <= 1.0)

    def test_forward_fills_regime(self):
        events = _make_fred_events(
            {"GDP": [0.5, 0.6, 0.7]},
            dates=["2020-01-01"],
        )
        overlay = build_regime_overlay(
            fred_events=events,
            index=pd.DatetimeIndex(["2020-06-01", "2020-12-01"]),
            config=RegimeOverlayConfig(),
        )
        assert overlay.loc["2020-12-01", "regime_label"] == "expansion"


class TestApplyRegimeOverlay:
    def test_overlay_adds_regime_columns(self):
        strategy = _make_strategy_output()
        events = _make_fred_events({"GDP": [0.5, 0.6, 0.7]})
        overlay = build_regime_overlay(events, strategy.frame.index, RegimeOverlayConfig())
        result = apply_regime_overlay(strategy, overlay, RegimeOverlayConfig())
        assert "regime_label" in result.frame.columns
        assert "regime_score" in result.frame.columns
        assert "regime_confidence" in result.frame.columns
        assert "regime_position_multiplier" in result.frame.columns

    def test_overlay_preserves_required_columns(self):
        strategy = _make_strategy_output()
        events = _make_fred_events({"GDP": [0.5, 0.6, 0.7]})
        overlay = build_regime_overlay(events, strategy.frame.index, RegimeOverlayConfig())
        result = apply_regime_overlay(strategy, overlay, RegimeOverlayConfig())
        for col in ["signal", "forecast", "position", "cost_estimate"]:
            assert col in result.frame.columns

    def test_overlay_preserves_diagnostics(self):
        strategy = _make_strategy_output()
        events = _make_fred_events({"GDP": [0.5, 0.6, 0.7]})
        overlay = build_regime_overlay(events, strategy.frame.index, RegimeOverlayConfig())
        result = apply_regime_overlay(strategy, overlay, RegimeOverlayConfig())
        assert result.diagnostics["test"] is True
        assert "regime_overlay" in result.diagnostics

    def test_recession_reduces_position(self):
        index = pd.DatetimeIndex(["2020-01-01", "2020-06-01", "2020-12-01"])
        strategy = _make_strategy_output(index)
        events = _make_fred_events({"RECESSION_PROB": [0.8, 0.0, 0.0]})
        overlay = build_regime_overlay(events, index, RegimeOverlayConfig())
        result = apply_regime_overlay(strategy, overlay, RegimeOverlayConfig())
        # First period is recession, position should be reduced
        entry_pos = result.frame["position"].iloc[0]
        base_pos = strategy.frame["position"].iloc[0]
        multiplier = result.frame["regime_position_multiplier"].iloc[0]
        assert multiplier < 1.0

    def test_expansion_increases_position(self):
        index = pd.DatetimeIndex(["2020-01-01", "2020-06-01", "2020-12-01"])
        strategy = _make_strategy_output(index)
        events = _make_fred_events({"GDP": [0.8, 0.8, 0.8], "UNEMPLOYMENT": [0.3, 0.3, 0.3]})
        overlay = build_regime_overlay(events, index, RegimeOverlayConfig())
        result = apply_regime_overlay(strategy, overlay, RegimeOverlayConfig())
        multiplier = result.frame["regime_position_multiplier"].iloc[0]
        assert multiplier > 1.0

    def test_empty_overlay_does_not_change_positions(self):
        strategy = _make_strategy_output()
        empty_overlay = pd.DataFrame(index=strategy.frame.index)
        result = apply_regime_overlay(strategy, empty_overlay, RegimeOverlayConfig())
        pd.testing.assert_series_equal(
            result.frame["position"],
            strategy.frame["position"],
        )

    def test_validates_extra_columns(self):
        bad_frame = pd.DataFrame({
            "signal": [1.0],
            "forecast": [1.5],
            "position": [1000.0],
            "cost_estimate": [0.5],
        })
        bad = StrategyOutput(name="bad", frame=bad_frame, diagnostics={})
        with pytest.raises(ValueError):
            apply_regime_overlay(bad, pd.DataFrame(), RegimeOverlayConfig())


class TestRegimeOverlayEdgeCases:
    def test_single_bar_output(self):
        index = pd.DatetimeIndex(["2020-01-01"])
        strategy = _make_strategy_output(index)
        events = _make_fred_events({"GDP": [0.5]})
        overlay = build_regime_overlay(events, index, RegimeOverlayConfig())
        result = apply_regime_overlay(strategy, overlay, RegimeOverlayConfig())
        assert len(result.frame) == 1

    def test_two_bar_output(self):
        index = pd.DatetimeIndex(["2020-01-01", "2020-06-01"])
        strategy = _make_strategy_output(index)
        events = _make_fred_events(
            {"GDP": [0.5, 0.6]},
            dates=["2020-01-01", "2020-06-01"],
        )
        overlay = build_regime_overlay(events, index, RegimeOverlayConfig())
        result = apply_regime_overlay(strategy, overlay, RegimeOverlayConfig())
        assert len(result.frame) == 2

    def test_overlay_reuses_name(self):
        strategy = _make_strategy_output()
        events = _make_fred_events({"GDP": [0.5, 0.6, 0.7]})
        overlay = build_regime_overlay(events, strategy.frame.index, RegimeOverlayConfig())
        result = apply_regime_overlay(strategy, overlay, RegimeOverlayConfig())
        assert result.name == "test"

    def test_yield_inversion_detected(self):
        events = _make_fred_events({"YIELD_CURVE": [-0.8, -0.8, -0.8]})
        result = classify_fred_regime(events)
        assert result.iloc[0]["regime_label"] == "yield_inversion"
