from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from ..core.framework import StrategyOutput, WalkForwardStrategy
from ..core.portfolio import PortfolioManager
from ..research import PairScreenConfig, rank_sector_pairs
from ..features.sentiment import (
    SentimentConfig,
    adjust_pair_rankings_with_sentiment,
    apply_sentiment_overlay,
    build_pair_sentiment_overlay,
)
from ..features.regime_overlay import (
    RegimeOverlayConfig,
    apply_regime_overlay,
    build_regime_overlay,
)
from ..strategies import KalmanPairsStrategy, SectorResidualMeanReversionStrategy


@dataclass(frozen=True)
class StatArbConfig:
    include_residual_book: bool = True
    residual_lookback: int = 60
    residual_entry_z: float = 1.5
    residual_exit_z: float = 0.35
    residual_transaction_cost_bps: float = 2.0
    include_classic_pairs: bool = True
    top_n_pairs: int = 3
    entry_z: float = 2.0
    exit_z: float = 0.35
    break_window: int = 80
    break_pvalue: float = 0.20
    transaction_cost_bps: float = 4.0


def select_diversified_pairs(ranked_pairs: pd.DataFrame, top_n_pairs: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used_tickers: set[str] = set()

    for record in ranked_pairs.to_dict("records"):
        ticker1 = record["Ticker_1"]
        ticker2 = record["Ticker_2"]
        if ticker1 in used_tickers or ticker2 in used_tickers:
            continue
        selected.append(record)
        used_tickers.update({ticker1, ticker2})
        if len(selected) >= top_n_pairs:
            return selected

    if len(selected) >= top_n_pairs:
        return selected

    for record in ranked_pairs.to_dict("records"):
        if record in selected:
            continue
        selected.append(record)
        if len(selected) >= top_n_pairs:
            break

    return selected


class SectorStatArbPipeline(WalkForwardStrategy):
    def __init__(
        self,
        sector_map: Mapping[str, str],
        portfolio_manager: PortfolioManager | None = None,
        screen_config: PairScreenConfig = PairScreenConfig(),
        stat_arb_config: StatArbConfig = StatArbConfig(),
        daily_sentiment: pd.DataFrame | None = None,
        sentiment_config: SentimentConfig | None = None,
        fred_events: pd.DataFrame | None = None,
        regime_config: RegimeOverlayConfig | None = None,
        name: str = "sector_stat_arb",
    ) -> None:
        self.sector_map = dict(sector_map)
        self.portfolio_manager = portfolio_manager or PortfolioManager()
        self.screen_config = screen_config
        self.stat_arb_config = stat_arb_config
        self.daily_sentiment = daily_sentiment
        self.sentiment_config = sentiment_config
        self.fred_events = fred_events
        self.regime_config = regime_config
        self.name = name

    def _flat_output(self, index: pd.Index, reason: str) -> StrategyOutput:
        frame = pd.DataFrame(index=index)
        frame["signal"] = 0.0
        frame["forecast"] = 0.0
        frame["position"] = 0.0
        frame["cost_estimate"] = 0.0
        frame["unit_return"] = 0.0
        frame["gross_return"] = 0.0
        frame["turnover"] = 0.0
        frame["short_exposure"] = 0.0
        frame["gross_exposure"] = 0.0
        return StrategyOutput(
            name=self.name,
            frame=frame,
            diagnostics={"status": reason, "selected_pairs": []},
        ).validate(extra_columns=("unit_return", "gross_return"))

    def build_component_outputs(
        self,
        train_data: pd.DataFrame,
        test_data: pd.DataFrame,
    ) -> tuple[dict[str, StrategyOutput], pd.DataFrame, list[dict[str, Any]], list[str]]:
        pair_outputs: dict[str, StrategyOutput] = {}

        residual_symbols: list[str] = []
        if self.stat_arb_config.include_residual_book:
            sector_buckets: dict[str, list[str]] = {}
            for ticker, sector in self.sector_map.items():
                if ticker in train_data.columns and ticker in test_data.columns:
                    sector_buckets.setdefault(sector, []).append(ticker)

            for sector, sector_symbols in sector_buckets.items():
                if len(sector_symbols) < 2:
                    continue
                for symbol in sorted(sector_symbols):
                    strategy = SectorResidualMeanReversionStrategy(
                        symbol=symbol,
                        sector_symbols=sector_symbols,
                        lookback=self.stat_arb_config.residual_lookback,
                        entry_z=self.stat_arb_config.residual_entry_z,
                        exit_z=self.stat_arb_config.residual_exit_z,
                        transaction_cost_bps=self.stat_arb_config.residual_transaction_cost_bps,
                    )
                    output = strategy.run_fold(
                        train_data=train_data[sector_symbols],
                        test_data=test_data[sector_symbols],
                    )
                    pair_outputs[f"residual_{symbol}"] = output
                    residual_symbols.append(symbol)

        ranked_pairs = pd.DataFrame()
        selected_pairs: list[dict[str, Any]] = []
        if self.stat_arb_config.include_classic_pairs:
            ranked_pairs = rank_sector_pairs(
                prices=train_data,
                sector_map=self.sector_map,
                screen_config=self.screen_config,
            )
            if self.daily_sentiment is not None and self.sentiment_config is not None and not ranked_pairs.empty:
                ranked_pairs = adjust_pair_rankings_with_sentiment(
                    ranked_pairs=ranked_pairs,
                    daily_sentiment=self.daily_sentiment,
                    asof_date=train_data.index[-1],
                    config=self.sentiment_config,
                )
            selected_pairs = select_diversified_pairs(
                ranked_pairs=ranked_pairs,
                top_n_pairs=self.stat_arb_config.top_n_pairs,
            )

            for pair_record in selected_pairs:
                ticker1 = pair_record["Ticker_1"]
                ticker2 = pair_record["Ticker_2"]
                strategy = KalmanPairsStrategy(
                    ticker1=ticker1,
                    ticker2=ticker2,
                    entry_z=self.stat_arb_config.entry_z,
                    exit_z=self.stat_arb_config.exit_z,
                    break_window=self.stat_arb_config.break_window,
                    break_pvalue=self.stat_arb_config.break_pvalue,
                    transaction_cost_bps=self.stat_arb_config.transaction_cost_bps,
                    pair_metadata=pair_record,
                )
                pair_name = f"pair_{ticker1}_{ticker2}"
                pair_outputs[pair_name] = strategy.run_fold(
                    train_data=train_data[[ticker1, ticker2]],
                    test_data=test_data[[ticker1, ticker2]],
                )
                if self.daily_sentiment is not None and self.sentiment_config is not None:
                    overlay = build_pair_sentiment_overlay(
                        daily_sentiment=self.daily_sentiment,
                        ticker1=ticker1,
                        ticker2=ticker2,
                        index=test_data.index,
                        config=self.sentiment_config,
                    )
                    pair_outputs[pair_name] = apply_sentiment_overlay(
                        strategy_output=pair_outputs[pair_name],
                        sentiment_overlay=overlay,
                        config=self.sentiment_config,
                    )

        if self.fred_events is not None and self.regime_config is not None:
            regime_overlay = build_regime_overlay(
                fred_events=self.fred_events,
                index=test_data.index,
                config=self.regime_config,
            )
            for pair_name in list(pair_outputs.keys()):
                pair_outputs[pair_name] = apply_regime_overlay(
                    strategy_output=pair_outputs[pair_name],
                    regime_overlay=regime_overlay,
                    config=self.regime_config,
                )

        return pair_outputs, ranked_pairs, selected_pairs, residual_symbols

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        pair_outputs, ranked_pairs, selected_pairs, residual_symbols = self.build_component_outputs(
            train_data=train_data,
            test_data=test_data,
        )

        if not pair_outputs:
            return self._flat_output(index=test_data.index, reason="no_tradeable_stat_arb_components")

        portfolio_output = self.portfolio_manager.allocate_capital(
            strategy_outputs=pair_outputs,
            portfolio_name=self.name,
        )
        portfolio_output.diagnostics.update(
            {
                "sub_sleeves": {
                    "residual_book": self.stat_arb_config.include_residual_book,
                    "classic_pairs": self.stat_arb_config.include_classic_pairs,
                },
                "residual_symbols": residual_symbols,
                "selected_pairs": selected_pairs,
                "candidate_count": int(len(ranked_pairs)),
                "screen_config": self.screen_config.__dict__,
                "stat_arb_config": self.stat_arb_config.__dict__,
                "sentiment_enabled": self.daily_sentiment is not None and self.sentiment_config is not None,
            }
        )
        return portfolio_output
