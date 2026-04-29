from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from pairs_trading.apps.cli import run_graph_stat_arb_pipeline, run_pead_sentiment_pipeline
from pairs_trading.core.portfolio import PortfolioManager
from pairs_trading.engines.backtesting import CostModel, WalkForwardBacktester, WalkForwardConfig
from pairs_trading.pipelines import GraphStatArbConfig, GraphStatArbPipeline, PEADSentimentConfig, PEADSentimentPipeline
from pairs_trading.research import GraphClusterConfig, find_graph_clusters
from pairs_trading.strategies import GraphClusterTradingConfig
from tests.common import fresh_test_dir, synthetic_daily_sentiment, synthetic_directional_prices, synthetic_prices_and_sector_map


class PEADSentimentPipelineTests(unittest.TestCase):
    def test_pead_sentiment_pipeline_trades_earnings_events_with_sentiment(self) -> None:
        prices = synthetic_directional_prices()[["TREND", "MEAN"]].rename(columns={"TREND": "AAA", "MEAN": "BBB"})
        event_dates = [prices.index[285], prices.index[365], prices.index[445]]
        events = pd.DataFrame(
            {
                "timestamp": event_dates,
                "ticker": ["AAA", "AAA", "BBB"],
                "event_score": [0.35, 0.30, -0.35],
                "confidence": [0.85, 0.80, 0.90],
                "event_type": ["earnings_release_8k", "quarterly_earnings_report", "earnings_release_8k"],
                "source": ["unit_test", "unit_test", "unit_test"],
                "form": ["8-K", "10-Q", "8-K"],
            }
        )
        daily_sentiment = pd.DataFrame(
            {
                "date": [date.normalize() for date in event_dates],
                "ticker": ["AAA", "AAA", "BBB"],
                "sentiment_score": [0.70, 0.55, -0.65],
                "sentiment_abs": [0.70, 0.55, 0.65],
                "confidence": [0.90, 0.85, 0.88],
                "article_count": [3, 2, 4],
                "positive_prob": [0.80, 0.70, 0.05],
                "negative_prob": [0.05, 0.10, 0.78],
                "neutral_prob": [0.15, 0.20, 0.17],
            }
        )

        pipeline = PEADSentimentPipeline(
            events=events,
            daily_sentiment=daily_sentiment,
            portfolio_manager=PortfolioManager(max_leverage=1.0, risk_per_trade=0.04, max_strategy_weight=0.30),
            config=PEADSentimentConfig.from_symbols(
                ["AAA", "BBB"],
                holding_period_bars=5,
                entry_threshold=0.12,
                require_sentiment=True,
            ),
            name="pead_unit",
        )
        result = WalkForwardBacktester(
            strategy=pipeline,
            prices=prices,
            config=WalkForwardConfig(train_bars=260, test_bars=80, step_bars=80, bars_per_year=252, purge_bars=5),
            cost_model=CostModel(commission_bps=0.5, spread_bps=1.0, slippage_bps=1.0, delay_bars=1),
            experiment_root=fresh_test_dir("artifacts/test_runs/pead_sentiment"),
        ).run("pead_unit")

        self.assertGreater(len(result.fold_metrics), 0)
        self.assertIn("dsr", result.summary)
        self.assertTrue(any(column.startswith("weight_") for column in result.equity_curve.columns))
        self.assertGreater(float(result.equity_curve["position"].abs().sum()), 0.0)

    def test_pead_treats_company_facts_events_as_earnings_like(self) -> None:
        prices = synthetic_directional_prices()[["TREND"]].rename(columns={"TREND": "AAA"})
        event_date = prices.index[285]
        events = pd.DataFrame(
            {
                "timestamp": [event_date],
                "ticker": ["AAA"],
                "event_score": [0.60],
                "confidence": [0.90],
                "event_type": ["company_facts"],
                "source": ["unit_test"],
                "form": ["10-Q"],
            }
        )
        pipeline = PEADSentimentPipeline(
            events=events,
            portfolio_manager=PortfolioManager(max_leverage=1.0, risk_per_trade=0.05, max_strategy_weight=0.50),
            config=PEADSentimentConfig.from_symbols(["AAA"], entry_threshold=0.20, require_earnings_event=True),
            name="pead_company_facts",
        )

        output = pipeline.run_fold(prices.iloc[:280], prices.iloc[280:340])

        self.assertGreater(float(output.frame["position"].abs().sum()), 0.0)
        self.assertIn("AAA", output.diagnostics["selected_symbols"])

    def test_run_pead_sentiment_pipeline_integration(self) -> None:
        prices = synthetic_directional_prices()[["TREND", "MEAN"]].rename(columns={"TREND": "AAA", "MEAN": "BBB"})
        data_dir = fresh_test_dir("artifacts/test_runner/pead")
        event_path = data_dir / "events.csv"
        sentiment_path = data_dir / "daily_sentiment.parquet"
        pd.DataFrame(
            {
                "timestamp": [prices.index[285], prices.index[365]],
                "ticker": ["AAA", "BBB"],
                "event_score": [0.40, -0.35],
                "confidence": [0.90, 0.90],
                "event_type": ["earnings_release_8k", "earnings_release_8k"],
                "form": ["8-K", "8-K"],
            }
        ).to_csv(event_path, index=False)
        pd.DataFrame(
            {
                "date": [prices.index[285].normalize(), prices.index[365].normalize()],
                "ticker": ["AAA", "BBB"],
                "sentiment_score": [0.70, -0.70],
                "sentiment_abs": [0.70, 0.70],
                "confidence": [0.90, 0.90],
                "article_count": [3, 3],
                "positive_prob": [0.80, 0.05],
                "negative_prob": [0.05, 0.80],
                "neutral_prob": [0.15, 0.15],
            }
        ).to_parquet(sentiment_path)

        with patch("pairs_trading.apps.cli.CachedParquetProvider.get_close_prices", return_value=prices):
            output = run_pead_sentiment_pipeline(
                symbols=["AAA", "BBB"],
                start="2020-01-01",
                end="2023-12-31",
                event_file=str(event_path),
                daily_sentiment_file=str(sentiment_path),
                experiment_name="pead_integration",
                artifact_root=str(data_dir / "experiments"),
                entry_threshold=0.12,
                require_sentiment=True,
            )

        self.assertIn("summary", output)
        self.assertTrue(output["result"].artifact_dir.exists())
        self.assertIn("report", output["visuals"])


class GraphStatArbPipelineTests(unittest.TestCase):
    def test_find_graph_clusters_uses_sector_mst_relationships(self) -> None:
        prices, sector_map = synthetic_prices_and_sector_map()
        clusters = find_graph_clusters(
            prices.iloc[:320],
            sector_map=sector_map,
            config=GraphClusterConfig(min_history=180, correlation_floor=0.20, min_cluster_size=3),
        )

        self.assertTrue(clusters)
        self.assertIn("A1", clusters[0].symbols)
        self.assertGreaterEqual(len(clusters[0].mst_edges), 2)

    def test_graph_stat_arb_pipeline_runs_walk_forward(self) -> None:
        prices, sector_map = synthetic_prices_and_sector_map()
        pipeline = GraphStatArbPipeline(
            sector_map=sector_map,
            portfolio_manager=PortfolioManager(max_leverage=1.2, risk_per_trade=0.03, max_strategy_weight=0.40),
            config=GraphStatArbConfig(
                cluster_config=GraphClusterConfig(min_history=180, correlation_floor=0.20, min_cluster_size=3),
                trading_config=GraphClusterTradingConfig(residual_lookback=40, entry_z=0.50, top_n_per_side=1),
            ),
            name="graph_unit",
        )
        result = WalkForwardBacktester(
            strategy=pipeline,
            prices=prices,
            config=WalkForwardConfig(train_bars=300, test_bars=80, step_bars=80, bars_per_year=252, purge_bars=5),
            cost_model=CostModel(commission_bps=0.5, spread_bps=1.0, slippage_bps=1.0, delay_bars=1),
            experiment_root=fresh_test_dir("artifacts/test_runs/graph_stat_arb"),
        ).run("graph_unit")

        self.assertGreater(len(result.fold_metrics), 0)
        self.assertIn("pbo", result.summary)
        self.assertTrue(any(column.startswith("weight_cluster_") for column in result.equity_curve.columns))

    def test_run_graph_stat_arb_pipeline_integration(self) -> None:
        prices, sector_map = synthetic_prices_and_sector_map()
        data_dir = fresh_test_dir("artifacts/test_runner/graph")
        sector_map_path = data_dir / "sector_map.json"
        sector_map_path.write_text(pd.Series(sector_map).to_json(), encoding="utf-8")

        with patch("pairs_trading.apps.cli.CachedParquetProvider.get_close_prices", return_value=prices):
            output = run_graph_stat_arb_pipeline(
                sector_map_path=str(sector_map_path),
                start="2020-01-01",
                end="2023-12-31",
                experiment_name="graph_integration",
                artifact_root=str(data_dir / "experiments"),
                cluster_correlation_floor=0.20,
                cluster_min_size=3,
                residual_lookback=40,
                entry_z=0.50,
                top_n_per_side=1,
            )

        self.assertIn("summary", output)
        self.assertTrue(output["result"].artifact_dir.exists())
        self.assertIn("report", output["visuals"])


if __name__ == "__main__":
    unittest.main()
