from __future__ import annotations

import unittest

from pairs_trading.engines.backtesting import CostModel, WalkForwardBacktester, WalkForwardConfig
from pairs_trading.pipelines import SectorStatArbPipeline, StatArbConfig
from pairs_trading.core.portfolio import PortfolioManager
from pairs_trading.research import PairScreenConfig, rank_sector_pairs
from pairs_trading.features.sentiment import SentimentConfig
from tests.common import fresh_test_dir, synthetic_daily_sentiment, synthetic_prices_and_sector_map


class WalkForwardSmokeTests(unittest.TestCase):
    def test_rank_sector_pairs_finds_candidates(self) -> None:
        prices, sector_map = synthetic_prices_and_sector_map()
        ranked = rank_sector_pairs(
            prices.iloc[:300],
            sector_map=sector_map,
            screen_config=PairScreenConfig(
                min_history=180,
                correlation_floor=0.2,
                coint_pvalue_threshold=0.5,
                min_half_life=1.0,
                max_half_life=80.0,
                target_half_life=10.0,
            ),
        )
        self.assertFalse(ranked.empty)
        self.assertIn("Rank_Score", ranked.columns)

    def test_walk_forward_backtester_runs(self) -> None:
        prices, sector_map = synthetic_prices_and_sector_map()
        pipeline = SectorStatArbPipeline(
            sector_map=sector_map,
            portfolio_manager=PortfolioManager(
                max_leverage=1.2,
                risk_per_trade=0.02,
                volatility_window=15,
                max_strategy_weight=0.5,
            ),
            screen_config=PairScreenConfig(
                min_history=180,
                correlation_floor=0.2,
                coint_pvalue_threshold=0.5,
                min_half_life=1.0,
                max_half_life=80.0,
                target_half_life=10.0,
            ),
            stat_arb_config=StatArbConfig(
                top_n_pairs=2,
                entry_z=1.0,
                exit_z=0.1,
                break_window=20,
                break_pvalue=0.8,
                transaction_cost_bps=2.0,
            ),
            name="smoke_stat_arb",
        )

        artifact_root = fresh_test_dir("artifacts/test_runs/base")
        backtester = WalkForwardBacktester(
            strategy=pipeline,
            prices=prices,
            config=WalkForwardConfig(
                train_bars=300,
                test_bars=80,
                step_bars=80,
                bars_per_year=252,
            ),
            cost_model=CostModel(
                commission_bps=0.2,
                spread_bps=0.5,
                slippage_bps=0.3,
                borrow_bps_annual=20,
            ),
            experiment_root=artifact_root,
        )
        result = backtester.run("smoke_stat_arb")

        self.assertGreater(len(result.fold_metrics), 0)
        self.assertGreater(result.summary["bars"], 0)
        self.assertIn("total_return", result.summary)
        self.assertTrue(result.artifact_dir.exists())

    def test_sentiment_aware_pipeline_changes_allocations(self) -> None:
        prices, sector_map = synthetic_prices_and_sector_map()
        daily_sentiment = synthetic_daily_sentiment(prices.index)

        common_kwargs = {
            "sector_map": sector_map,
            "portfolio_manager": PortfolioManager(
                max_leverage=1.2,
                risk_per_trade=0.02,
                volatility_window=15,
                max_strategy_weight=0.5,
            ),
            "screen_config": PairScreenConfig(
                min_history=180,
                correlation_floor=0.2,
                coint_pvalue_threshold=0.5,
                min_half_life=1.0,
                max_half_life=80.0,
                target_half_life=10.0,
            ),
            "stat_arb_config": StatArbConfig(
                top_n_pairs=2,
                entry_z=1.0,
                exit_z=0.1,
                break_window=20,
                break_pvalue=0.8,
                transaction_cost_bps=2.0,
            ),
        }

        train_data = prices.iloc[:300]
        test_data = prices.iloc[300:380]

        baseline_pipeline = SectorStatArbPipeline(name="baseline", **common_kwargs)
        sentiment_pipeline = SectorStatArbPipeline(
            daily_sentiment=daily_sentiment,
            sentiment_config=SentimentConfig(
                ranking_weight=0.15,
                forecast_weight=0.35,
                disagreement_penalty=1.0,
                smoothing_span=5,
                min_articles=1,
                min_confidence=0.1,
                max_position_multiplier=1.5,
                overlay_cost_bps=0.5,
            ),
            name="with_sentiment",
            **common_kwargs,
        )

        baseline_output = baseline_pipeline.run_fold(train_data=train_data, test_data=test_data)
        sentiment_output = sentiment_pipeline.run_fold(train_data=train_data, test_data=test_data)

        self.assertTrue(sentiment_output.diagnostics["sentiment_enabled"])
        self.assertFalse(baseline_output.frame.equals(sentiment_output.frame))
        self.assertIn("weight_pair_A1_A2", sentiment_output.frame.columns)
        self.assertIn("weight_pair_B1_B2", sentiment_output.frame.columns)
        self.assertGreater(
            float((baseline_output.frame["position"] - sentiment_output.frame["position"]).abs().sum()),
            0.0,
        )


if __name__ == "__main__":
    unittest.main()
