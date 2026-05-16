from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from ..core.framework import StrategyOutput, WalkForwardStrategy
from ..research.decision_history import DecisionHistoryStore


class CommitteeSignalFollowerPipeline(WalkForwardStrategy):
    VALID_POSITION_SIZING = {"confidence_weighted", "fixed"}

    def __init__(
        self,
        symbols: list[str],
        decision_store: DecisionHistoryStore,
        *,
        position_sizing: str = "confidence_weighted",
        max_position_pct: float = 0.25,
        confidence_threshold: int = 30,
        scale_in_confidence_delta: int = 10,
        scale_out_on_opposite: bool = True,
        flat_on_avoid: bool = True,
        name: str = "committee_signal_follower",
    ) -> None:
        if position_sizing not in self.VALID_POSITION_SIZING:
            raise ValueError(f"Unsupported position_sizing: {position_sizing}")
        self.symbols = [str(s).upper() for s in symbols]
        self.decision_store = decision_store
        self.position_sizing = position_sizing
        self.max_position_pct = max_position_pct
        self.confidence_threshold = confidence_threshold
        self.scale_in_confidence_delta = scale_in_confidence_delta
        self.scale_out_on_opposite = scale_out_on_opposite
        self.flat_on_avoid = flat_on_avoid
        self.name = name

    def _flat_output(self, index: pd.Index, reason: str) -> StrategyOutput:
        frame = pd.DataFrame(index=index)
        frame["signal"] = 0.0
        frame["forecast"] = 0.0
        frame["position"] = 0.0
        frame["cost_estimate"] = 0.0
        frame["gross_return"] = 0.0
        frame["unit_return"] = 0.0
        frame["turnover"] = 0.0
        frame["short_exposure"] = 0.0
        frame["gross_exposure"] = 0.0
        return StrategyOutput(
            name=self.name,
            frame=frame,
            diagnostics={"status": reason, "symbols": []},
        ).validate(extra_columns=("gross_return",))

    def _target_pct(self, confidence: int) -> float:
        if self.position_sizing == "fixed":
            return self.max_position_pct
        return self.max_position_pct * (max(0, min(confidence, 100)) / 100.0)

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        if test_data.empty:
            return self._flat_output(test_data.index, "no_test_data")

        available = [s for s in self.symbols if s in test_data.columns]
        if not available:
            return self._flat_output(test_data.index, "no_available_symbols")

        combined = pd.concat([train_data[available], test_data[available]], axis=0)
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        if combined.empty:
            return self._flat_output(test_data.index, "no_price_data")

        test_end = pd.Timestamp(test_data.index[-1])
        combined_index = pd.DatetimeIndex(combined.index)

        events: list[dict[str, Any]] = []
        skipped_after_window = 0
        for symbol in available:
            decisions = self.decision_store.decisions_by_ticker(symbol, limit=2000)
            for d in decisions:
                if d.confidence < self.confidence_threshold:
                    continue
                d_date_raw = pd.to_datetime(d.analysis_date, errors="coerce")
                if pd.isna(d_date_raw):
                    continue
                d_date = pd.Timestamp(d_date_raw).tz_localize(None).normalize()
                if d_date > test_end:
                    skipped_after_window += 1
                    continue
                bar_position = combined_index.searchsorted(d_date, side="right")
                if bar_position >= len(combined_index):
                    skipped_after_window += 1
                    continue
                event_timestamp = pd.to_datetime(d.timestamp, errors="coerce")
                if pd.isna(event_timestamp):
                    event_timestamp = d_date
                events.append({
                    "ticker": symbol,
                    "date": combined_index[bar_position],
                    "decision_date": d_date,
                    "timestamp": pd.Timestamp(event_timestamp).tz_localize(None),
                    "decision": str(d.decision).upper() if d.decision else "HOLD",
                    "confidence": d.confidence,
                })

        events.sort(key=lambda e: (e["date"], e["timestamp"]))
        events_by_date: dict[pd.Timestamp, list[dict[str, Any]]] = defaultdict(list)
        for e in events:
            events_by_date[e["date"]].append(e)

        index = combined.index
        frame = pd.DataFrame(index=index)
        for symbol in available:
            frame[f"target_weight_{symbol}"] = 0.0

        current_weight: dict[str, float] = {s: 0.0 for s in available}
        entry_conf: dict[str, int] = {s: 0 for s in available}

        for date in index:
            if date in events_by_date:
                for e in events_by_date[date]:
                    t = e["ticker"]
                    dec = e["decision"]
                    conf = e["confidence"]

                    if dec == "BUY":
                        if current_weight[t] == 0.0:
                            current_weight[t] = self._target_pct(conf)
                            entry_conf[t] = conf
                        elif current_weight[t] > 0.0:
                            if conf > entry_conf[t] + self.scale_in_confidence_delta:
                                current_weight[t] = self._target_pct(conf)
                                entry_conf[t] = conf
                        else:
                            if self.scale_out_on_opposite:
                                current_weight[t] = self._target_pct(conf)
                                entry_conf[t] = conf

                    elif dec == "SELL":
                        if current_weight[t] == 0.0:
                            current_weight[t] = -self._target_pct(conf)
                            entry_conf[t] = conf
                        elif current_weight[t] < 0.0:
                            if conf > entry_conf[t] + self.scale_in_confidence_delta:
                                current_weight[t] = -self._target_pct(conf)
                                entry_conf[t] = conf
                        else:
                            if self.scale_out_on_opposite:
                                current_weight[t] = -self._target_pct(conf)
                                entry_conf[t] = conf

                    elif dec == "AVOID" and self.flat_on_avoid:
                        current_weight[t] = 0.0
                        entry_conf[t] = 0

            for s in available:
                frame.at[date, f"target_weight_{s}"] = current_weight[s]

        price_data = combined[available].astype(float)
        price_returns = price_data.pct_change().fillna(0.0)

        gross_exposure_series = pd.Series(0.0, index=index)
        net_exposure_series = pd.Series(0.0, index=index)
        short_exposure_series = pd.Series(0.0, index=index)

        prev_weights: dict[str, float] = {s: 0.0 for s in available}
        turnover_series = pd.Series(0.0, index=index)
        gross_return_series = pd.Series(0.0, index=index)

        for i, date in enumerate(index):
            ge = 0.0
            ne = 0.0
            to = 0.0
            gr = 0.0
            se = 0.0
            for s in available:
                w = frame.at[date, f"target_weight_{s}"]
                ge += abs(w)
                ne += w
                se += abs(min(w, 0.0))
                to += abs(w - prev_weights[s])
                if i > 0:
                    ret = price_returns[s].iloc[i]
                    gr += prev_weights[s] * ret
                prev_weights[s] = w
            gross_exposure_series.at[date] = ge
            net_exposure_series.at[date] = ne
            short_exposure_series.at[date] = se
            turnover_series.at[date] = to
            gross_return_series.at[date] = gr

        frame["signal"] = np.sign(net_exposure_series).replace({-0.0: 0.0}).fillna(0.0)
        frame["forecast"] = net_exposure_series
        frame["position"] = gross_exposure_series
        frame["short_exposure"] = short_exposure_series
        frame["gross_exposure"] = gross_exposure_series
        frame["turnover"] = turnover_series
        frame["gross_return"] = gross_return_series
        frame["unit_return"] = frame.apply(
            lambda row: row["gross_return"] / max(abs(row["position"]), 1e-12), axis=1
        )
        cost_bps = 3.0
        frame["cost_estimate"] = frame["turnover"] * cost_bps / 10000.0
        test_frame = frame.reindex(test_data.index).copy()

        return StrategyOutput(
            name=self.name,
            frame=test_frame,
            diagnostics={
                "symbols": available,
                "event_count": len(events),
                "skipped_after_window": skipped_after_window,
                "pipeline_type": "committee_signal_follower",
                "position_sizing": self.position_sizing,
                "max_position_pct": self.max_position_pct,
                "scale_in_confidence_delta": self.scale_in_confidence_delta,
                "scale_out_on_opposite": self.scale_out_on_opposite,
                "flat_on_avoid": self.flat_on_avoid,
            },
        ).validate(extra_columns=("gross_return",))
