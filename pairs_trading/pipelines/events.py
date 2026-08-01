from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pandas as pd

from ..core.framework import StrategyOutput, WalkForwardStrategy
from ..core.portfolio import PortfolioManager
from ..strategies.events import EventDriftStrategy, PEADSentimentStrategy


@dataclass(frozen=True)
class EventDrivenConfig:
    symbols: tuple[str, ...]
    holding_period_bars: int = 5
    entry_threshold: float = 0.15
    min_events: int = 1
    transaction_cost_bps: float = 2.0

    @classmethod
    def from_symbols(
        cls,
        symbols: Sequence[str],
        *,
        holding_period_bars: int = 5,
        entry_threshold: float = 0.15,
        min_events: int = 1,
        transaction_cost_bps: float = 2.0,
    ) -> "EventDrivenConfig":
        return cls(
            symbols=tuple(dict.fromkeys(symbols)),
            holding_period_bars=holding_period_bars,
            entry_threshold=entry_threshold,
            min_events=min_events,
            transaction_cost_bps=transaction_cost_bps,
        )


class EventDrivenPipeline(WalkForwardStrategy):
    def __init__(
        self,
        events: pd.DataFrame,
        portfolio_manager: PortfolioManager | None = None,
        config: EventDrivenConfig | None = None,
        name: str = "edgar_event_drift",
    ) -> None:
        self.events = events.copy()
        self.portfolio_manager = portfolio_manager or PortfolioManager()
        self.config = config or EventDrivenConfig(symbols=tuple())
        self.name = name

    def _flat_output(self, index: pd.Index, reason: str) -> StrategyOutput:
        frame = pd.DataFrame(index=index)
        for column in ("signal", "forecast", "position", "cost_estimate", "gross_return", "unit_return", "turnover"):
            frame[column] = 0.0
        frame["short_exposure"] = 0.0
        frame["gross_exposure"] = 0.0
        return StrategyOutput(
            name=self.name,
            frame=frame,
            diagnostics={"status": reason, "selected_symbols": []},
        ).validate(extra_columns=("unit_return", "gross_return"))

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        candidate_symbols = [symbol for symbol in self.config.symbols if symbol in train_data.columns and symbol in test_data.columns]
        if not candidate_symbols:
            return self._flat_output(test_data.index, reason="no_symbols")

        events = self.events.copy()
        if events.empty:
            return self._flat_output(test_data.index, reason="no_events")
        events["timestamp"] = pd.to_datetime(events["timestamp"]).dt.tz_localize(None)
        events["ticker"] = events["ticker"].astype(str).str.upper()

        outputs: dict[str, StrategyOutput] = {}
        selected_symbols: list[str] = []
        cutoff_start = pd.Timestamp(train_data.index[0])
        cutoff_end = pd.Timestamp(test_data.index[-1])

        for symbol in candidate_symbols:
            symbol_events = events[
                (events["ticker"] == symbol)
                & (events["timestamp"] >= cutoff_start)
                & (events["timestamp"] <= cutoff_end)
            ]
            if len(symbol_events) < self.config.min_events:
                continue
            strategy = EventDriftStrategy(
                symbol=symbol,
                events=symbol_events,
                holding_period_bars=self.config.holding_period_bars,
                entry_threshold=self.config.entry_threshold,
                transaction_cost_bps=self.config.transaction_cost_bps,
            )
            outputs[symbol] = strategy.run_fold(train_data[[symbol]], test_data[[symbol]])
            selected_symbols.append(symbol)

        if not outputs:
            return self._flat_output(test_data.index, reason="no_tradeable_events")

        portfolio_output = self.portfolio_manager.allocate_capital(
            strategy_outputs=outputs,
            portfolio_name=self.name,
        )
        portfolio_output.diagnostics.update(
            {
                "selected_symbols": selected_symbols,
                "pipeline_type": "event_driven",
                "event_count": int(
                    len(
                        events[
                            (events["ticker"].isin(selected_symbols))
                            & (events["timestamp"] >= pd.Timestamp(test_data.index[0]))
                            & (events["timestamp"] <= pd.Timestamp(test_data.index[-1]))
                        ]
                    )
                ),
            }
        )
        return portfolio_output


@dataclass(frozen=True)
class PEADSentimentConfig:
    symbols: tuple[str, ...]
    holding_period_bars: int = 5
    entry_threshold: float = 0.20
    event_weight: float = 0.45
    sentiment_weight: float = 0.55
    sentiment_window_days: int = 2
    min_event_confidence: float = 0.10
    min_sentiment_confidence: float = 0.10
    require_sentiment: bool = False
    require_earnings_event: bool = True
    min_events: int = 1
    transaction_cost_bps: float = 2.5

    @classmethod
    def from_symbols(
        cls,
        symbols: Sequence[str],
        **kwargs,
    ) -> "PEADSentimentConfig":
        return cls(symbols=tuple(dict.fromkeys(symbols)), **kwargs)


class PEADSentimentPipeline(WalkForwardStrategy):
    def __init__(
        self,
        events: pd.DataFrame,
        daily_sentiment: pd.DataFrame | None = None,
        portfolio_manager: PortfolioManager | None = None,
        config: PEADSentimentConfig | None = None,
        name: str = "pead_sentiment",
    ) -> None:
        self.events = events.copy()
        self.daily_sentiment = None if daily_sentiment is None else daily_sentiment.copy()
        self.portfolio_manager = portfolio_manager or PortfolioManager()
        self.config = config or PEADSentimentConfig(symbols=tuple())
        self.name = name

    def _flat_output(self, index: pd.Index, reason: str) -> StrategyOutput:
        frame = pd.DataFrame(index=index)
        for column in ("signal", "forecast", "position", "cost_estimate", "gross_return", "unit_return", "turnover"):
            frame[column] = 0.0
        frame["short_exposure"] = 0.0
        frame["gross_exposure"] = 0.0
        frame["sentiment_strength"] = 0.0
        frame["sentiment_confidence"] = 0.0
        return StrategyOutput(
            name=self.name,
            frame=frame,
            diagnostics={"status": reason, "selected_symbols": [], "pipeline_type": "pead_sentiment"},
        ).validate(extra_columns=("unit_return", "gross_return"))

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        candidate_symbols = [symbol for symbol in self.config.symbols if symbol in train_data.columns and symbol in test_data.columns]
        if not candidate_symbols:
            return self._flat_output(test_data.index, reason="no_symbols")
        if self.events.empty:
            return self._flat_output(test_data.index, reason="no_events")

        events = self.events.copy()
        events["timestamp"] = pd.to_datetime(events["timestamp"]).dt.tz_localize(None)
        events["ticker"] = events["ticker"].astype(str).str.upper()

        outputs: dict[str, StrategyOutput] = {}
        selected_symbols: list[str] = []
        cutoff_start = pd.Timestamp(train_data.index[0])
        cutoff_end = pd.Timestamp(test_data.index[-1])

        for symbol in candidate_symbols:
            symbol_events = events[
                (events["ticker"] == symbol)
                & (events["timestamp"] >= cutoff_start)
                & (events["timestamp"] <= cutoff_end)
            ]
            if len(symbol_events) < self.config.min_events:
                continue

            strategy = PEADSentimentStrategy(
                symbol=symbol,
                events=symbol_events,
                daily_sentiment=self.daily_sentiment,
                holding_period_bars=self.config.holding_period_bars,
                entry_threshold=self.config.entry_threshold,
                event_weight=self.config.event_weight,
                sentiment_weight=self.config.sentiment_weight,
                sentiment_window_days=self.config.sentiment_window_days,
                min_event_confidence=self.config.min_event_confidence,
                min_sentiment_confidence=self.config.min_sentiment_confidence,
                require_sentiment=self.config.require_sentiment,
                require_earnings_event=self.config.require_earnings_event,
                transaction_cost_bps=self.config.transaction_cost_bps,
            )
            output = strategy.run_fold(train_data[[symbol]], test_data[[symbol]])
            if output.frame["position"].abs().sum() <= 0.0:
                continue
            outputs[symbol] = output
            selected_symbols.append(symbol)

        if not outputs:
            return self._flat_output(test_data.index, reason="no_tradeable_pead_events")

        portfolio_output = self.portfolio_manager.allocate_capital(
            strategy_outputs=outputs,
            portfolio_name=self.name,
        )
        portfolio_output.diagnostics.update(
            {
                "selected_symbols": selected_symbols,
                "pipeline_type": "pead_sentiment",
                "sentiment_enabled": self.daily_sentiment is not None and not self.daily_sentiment.empty,
                "event_count": int(
                    len(
                        events[
                            (events["ticker"].isin(selected_symbols))
                            & (events["timestamp"] >= pd.Timestamp(test_data.index[0]))
                            & (events["timestamp"] <= pd.Timestamp(test_data.index[-1]))
                        ]
                    )
                ),
                "config": self.config.__dict__,
            }
        )
        return portfolio_output
