from __future__ import annotations

import numpy as np
import pandas as pd

from ..core.framework import StrategyOutput, WalkForwardStrategy


class EventDriftStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        events: pd.DataFrame,
        holding_period_bars: int = 5,
        entry_threshold: float = 0.15,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        self.symbol = symbol
        self.events = events.copy()
        self.holding_period_bars = holding_period_bars
        self.entry_threshold = entry_threshold
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        if self.symbol not in train_data.columns or self.symbol not in test_data.columns:
            raise KeyError(f"{self.symbol} must be present in both train and test data.")

        combined = pd.concat([train_data[[self.symbol]], test_data[[self.symbol]]], axis=0)
        combined = combined[~combined.index.duplicated(keep="last")]
        prices = combined[self.symbol].astype(float)
        analysis = pd.DataFrame(index=prices.index)
        analysis["price"] = prices
        analysis["unit_return"] = prices.pct_change().fillna(0.0)
        analysis["raw_signal"] = 0.0

        symbol_events = self.events.copy()
        if not symbol_events.empty:
            symbol_events["timestamp"] = pd.to_datetime(symbol_events["timestamp"]).dt.tz_localize(None)
            symbol_events["ticker"] = symbol_events["ticker"].astype(str).str.upper()
            symbol_events = symbol_events[symbol_events["ticker"] == self.symbol]
            symbol_events["event_score"] = pd.to_numeric(symbol_events["event_score"], errors="coerce").fillna(0.0)
            symbol_events["confidence"] = pd.to_numeric(symbol_events.get("confidence", 1.0), errors="coerce").fillna(1.0)

            for event in symbol_events.to_dict("records"):
                score = float(event["event_score"])
                if abs(score) < self.entry_threshold:
                    continue

                start_position = analysis.index.searchsorted(pd.Timestamp(event["timestamp"]), side="right")
                if start_position >= len(analysis.index):
                    continue
                end_position = min(len(analysis.index), start_position + self.holding_period_bars)
                signed_score = float(np.clip(score * float(event.get("confidence", 1.0)), -1.0, 1.0))
                analysis.iloc[start_position:end_position, analysis.columns.get_loc("raw_signal")] += signed_score

        analysis["position"] = analysis["raw_signal"].clip(-1.0, 1.0)
        analysis["forecast"] = (analysis["raw_signal"] * 2.0).clip(-2.0, 2.0)
        analysis["gross_return"] = analysis["position"].shift(1).fillna(0.0) * analysis["unit_return"]
        analysis["turnover"] = analysis["position"].diff().abs().fillna(analysis["position"].abs())
        analysis["cost_estimate"] = analysis["turnover"] * (self.transaction_cost_bps / 10_000.0)
        analysis["signal"] = np.sign(analysis["position"]).replace({-0.0: 0.0}).fillna(0.0)
        analysis["short_exposure_per_unit"] = (analysis["position"] < 0.0).astype(float)
        analysis["gross_exposure_per_unit"] = 1.0
        analysis[f"target_weight_{self.symbol}"] = analysis["position"].fillna(0.0)

        test_frame = analysis.reindex(test_data.index).copy()
        for column in (
            "signal",
            "forecast",
            "position",
            "cost_estimate",
            "unit_return",
            "gross_return",
            "turnover",
            "short_exposure_per_unit",
            "gross_exposure_per_unit",
            f"target_weight_{self.symbol}",
        ):
            test_frame[column] = test_frame[column].fillna(0.0)

        symbol_events = symbol_events[
            (symbol_events["timestamp"] >= pd.Timestamp(test_data.index[0]))
            & (symbol_events["timestamp"] <= pd.Timestamp(test_data.index[-1]))
        ]
        return StrategyOutput(
            name=f"{self.symbol}_event_drift",
            frame=test_frame,
            diagnostics={
                "symbol": self.symbol,
                "strategy_type": "event_drift",
                "event_count": int(len(symbol_events)),
                "holding_period_bars": float(self.holding_period_bars),
                "entry_threshold": float(self.entry_threshold),
            },
        ).validate(extra_columns=("unit_return", "gross_return"))


class PEADSentimentStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        events: pd.DataFrame,
        daily_sentiment: pd.DataFrame | None = None,
        holding_period_bars: int = 5,
        entry_threshold: float = 0.20,
        event_weight: float = 0.45,
        sentiment_weight: float = 0.55,
        sentiment_window_days: int = 2,
        min_event_confidence: float = 0.10,
        min_sentiment_confidence: float = 0.10,
        require_sentiment: bool = False,
        require_earnings_event: bool = True,
        transaction_cost_bps: float = 2.5,
    ) -> None:
        self.symbol = symbol
        self.events = events.copy()
        self.daily_sentiment = None if daily_sentiment is None else daily_sentiment.copy()
        self.holding_period_bars = holding_period_bars
        self.entry_threshold = entry_threshold
        self.event_weight = event_weight
        self.sentiment_weight = sentiment_weight
        self.sentiment_window_days = sentiment_window_days
        self.min_event_confidence = min_event_confidence
        self.min_sentiment_confidence = min_sentiment_confidence
        self.require_sentiment = require_sentiment
        self.require_earnings_event = require_earnings_event
        self.transaction_cost_bps = transaction_cost_bps

    @staticmethod
    def _is_earnings_event(event: dict[str, object]) -> bool:
        text = " ".join(
            str(event.get(column, ""))
            for column in ("event_type", "form", "description", "source")
        ).lower()
        return any(
            keyword in text
            for keyword in (
                "earnings",
                "quarterly",
                "annual",
                "10-q",
                "10-k",
                "company_facts",
                "company facts",
                "companyfacts",
                "results of operations",
            )
        )

    def _sentiment_for_event(self, event_timestamp: pd.Timestamp) -> tuple[float, float, float]:
        if self.daily_sentiment is None or self.daily_sentiment.empty:
            return 0.0, 0.0, 0.0

        sentiment = self.daily_sentiment.copy()
        sentiment["date"] = pd.to_datetime(sentiment["date"]).dt.tz_localize(None).dt.normalize()
        sentiment["ticker"] = sentiment["ticker"].astype(str).str.upper()
        sentiment = sentiment[sentiment["ticker"] == self.symbol]
        if sentiment.empty:
            return 0.0, 0.0, 0.0

        event_date = pd.Timestamp(event_timestamp).normalize()
        start_date = event_date - pd.Timedelta(days=max(0, self.sentiment_window_days))
        window = sentiment[(sentiment["date"] >= start_date) & (sentiment["date"] <= event_date)].copy()
        if window.empty:
            return 0.0, 0.0, 0.0

        window["sentiment_score"] = pd.to_numeric(window["sentiment_score"], errors="coerce").fillna(0.0)
        window["confidence"] = pd.to_numeric(window.get("confidence", 0.0), errors="coerce").fillna(0.0).clip(0.0, 1.0)
        window["article_count"] = pd.to_numeric(window.get("article_count", 0.0), errors="coerce").fillna(0.0)
        weights = (window["confidence"].clip(lower=0.05) * window["article_count"].clip(lower=1.0)).clip(lower=1e-6)
        score = float(np.average(window["sentiment_score"], weights=weights))
        confidence = float(np.average(window["confidence"], weights=weights))
        articles = float(window["article_count"].sum())
        return score, confidence, articles

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        if self.symbol not in train_data.columns or self.symbol not in test_data.columns:
            raise KeyError(f"{self.symbol} must be present in both train and test data.")

        combined = pd.concat([train_data[[self.symbol]], test_data[[self.symbol]]], axis=0)
        combined = combined[~combined.index.duplicated(keep="last")]
        prices = combined[self.symbol].astype(float)
        analysis = pd.DataFrame(index=prices.index)
        analysis["price"] = prices
        analysis["unit_return"] = prices.pct_change().fillna(0.0)
        analysis["raw_signal"] = 0.0
        analysis["event_component"] = 0.0
        analysis["sentiment_component"] = 0.0
        analysis["sentiment_confidence"] = 0.0
        analysis["sentiment_strength"] = 0.0

        events = self.events.copy()
        trade_events = 0
        considered_events = 0
        if not events.empty:
            events["timestamp"] = pd.to_datetime(events["timestamp"]).dt.tz_localize(None)
            events["ticker"] = events["ticker"].astype(str).str.upper()
            events = events[events["ticker"] == self.symbol]
            events["event_score"] = pd.to_numeric(events["event_score"], errors="coerce").fillna(0.0).clip(-1.0, 1.0)
            events["confidence"] = pd.to_numeric(events.get("confidence", 1.0), errors="coerce").fillna(1.0).clip(0.0, 1.0)

            for event in events.sort_values("timestamp").to_dict("records"):
                if self.require_earnings_event and not self._is_earnings_event(event):
                    continue
                if float(event.get("confidence", 0.0)) < self.min_event_confidence:
                    continue
                considered_events += 1

                sentiment_score, sentiment_confidence, article_count = self._sentiment_for_event(pd.Timestamp(event["timestamp"]))
                if self.require_sentiment and (
                    article_count <= 0.0 or sentiment_confidence < self.min_sentiment_confidence
                ):
                    continue

                sentiment_component = sentiment_score * sentiment_confidence
                event_component = float(event["event_score"]) * float(event.get("confidence", 1.0))
                combined_score = (
                    self.event_weight * event_component
                    + self.sentiment_weight * sentiment_component
                )
                if abs(combined_score) < self.entry_threshold:
                    continue

                start_position = analysis.index.searchsorted(pd.Timestamp(event["timestamp"]), side="right")
                if start_position >= len(analysis.index):
                    continue
                end_position = min(len(analysis.index), start_position + self.holding_period_bars)
                signed_score = float(np.clip(combined_score, -1.0, 1.0))
                analysis.iloc[start_position:end_position, analysis.columns.get_loc("raw_signal")] += signed_score
                analysis.iloc[start_position:end_position, analysis.columns.get_loc("event_component")] += event_component
                analysis.iloc[start_position:end_position, analysis.columns.get_loc("sentiment_component")] += sentiment_component
                analysis.iloc[start_position:end_position, analysis.columns.get_loc("sentiment_confidence")] = max(
                    float(analysis.iloc[start_position:end_position]["sentiment_confidence"].max()),
                    sentiment_confidence,
                )
                analysis.iloc[start_position:end_position, analysis.columns.get_loc("sentiment_strength")] += sentiment_component
                trade_events += 1

        analysis["position"] = analysis["raw_signal"].clip(-1.0, 1.0)
        analysis["forecast"] = (analysis["raw_signal"] * 2.0).clip(-2.0, 2.0)
        analysis["gross_return"] = analysis["position"].shift(1).fillna(0.0) * analysis["unit_return"]
        analysis["turnover"] = analysis["position"].diff().abs().fillna(analysis["position"].abs())
        analysis["cost_estimate"] = analysis["turnover"] * (self.transaction_cost_bps / 10_000.0)
        analysis["signal"] = np.sign(analysis["position"]).replace({-0.0: 0.0}).fillna(0.0)
        analysis["short_exposure_per_unit"] = (analysis["position"] < 0.0).astype(float)
        analysis["gross_exposure_per_unit"] = 1.0
        analysis[f"target_weight_{self.symbol}"] = analysis["position"].fillna(0.0)

        test_frame = analysis.reindex(test_data.index).copy()
        for column in (
            "signal",
            "forecast",
            "position",
            "cost_estimate",
            "unit_return",
            "gross_return",
            "turnover",
            "short_exposure_per_unit",
            "gross_exposure_per_unit",
            "sentiment_confidence",
            "sentiment_strength",
            "event_component",
            "sentiment_component",
            f"target_weight_{self.symbol}",
        ):
            test_frame[column] = test_frame[column].fillna(0.0)

        test_events = events[
            (events["timestamp"] >= pd.Timestamp(test_data.index[0]))
            & (events["timestamp"] <= pd.Timestamp(test_data.index[-1]))
        ] if not events.empty else pd.DataFrame()
        return StrategyOutput(
            name=f"{self.symbol}_pead_sentiment",
            frame=test_frame,
            diagnostics={
                "symbol": self.symbol,
                "strategy_type": "pead_sentiment",
                "event_count": int(len(test_events)),
                "considered_event_count": int(considered_events),
                "trade_event_count": int(trade_events),
                "holding_period_bars": float(self.holding_period_bars),
                "entry_threshold": float(self.entry_threshold),
                "event_weight": float(self.event_weight),
                "sentiment_weight": float(self.sentiment_weight),
                "require_sentiment": bool(self.require_sentiment),
                "require_earnings_event": bool(self.require_earnings_event),
                "surprise_proxy_note": "event_score is used as the v1 surprise/fundamental proxy unless analyst-estimate surprise data is supplied upstream.",
            },
        ).validate(extra_columns=("unit_return", "gross_return"))
