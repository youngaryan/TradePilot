from __future__ import annotations

import unittest

import pandas as pd

from pairs_trading.pipelines.committee_signal_follower import CommitteeSignalFollowerPipeline
from pairs_trading.research.decision_history import CommitteeDecision


class FakeDecisionStore:
    def __init__(self, decisions: list[CommitteeDecision]) -> None:
        self.decisions = decisions

    def decisions_by_ticker(self, ticker: str, *, limit: int = 20) -> list[CommitteeDecision]:
        return [decision for decision in self.decisions if decision.ticker.upper() == ticker.upper()][:limit]


class CommitteeSignalFollowerTests(unittest.TestCase):
    def test_weekend_decision_uses_next_available_bar(self) -> None:
        prices = pd.DataFrame(
            {"AAPL": [100.0, 101.0, 102.0]},
            index=pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19"]),
        )
        store = FakeDecisionStore([
            CommitteeDecision(ticker="AAPL", analysis_date="2026-05-16", decision="BUY", confidence=80)
        ])

        output = CommitteeSignalFollowerPipeline(["AAPL"], store).run_fold(prices.iloc[:1], prices.iloc[1:])

        self.assertEqual(float(output.frame.loc[pd.Timestamp("2026-05-18"), "target_weight_AAPL"]), 0.20)
        self.assertGreater(float(output.frame.loc[pd.Timestamp("2026-05-19"), "gross_return"]), 0.0)

    def test_train_window_decision_carries_into_test_window(self) -> None:
        prices = pd.DataFrame(
            {"AAPL": [100.0, 101.0, 102.0, 103.0]},
            index=pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]),
        )
        store = FakeDecisionStore([
            CommitteeDecision(ticker="AAPL", analysis_date="2026-01-02", decision="BUY", confidence=80)
        ])

        output = CommitteeSignalFollowerPipeline(["AAPL"], store).run_fold(prices.iloc[:2], prices.iloc[2:])

        self.assertEqual(float(output.frame.loc[pd.Timestamp("2026-01-06"), "target_weight_AAPL"]), 0.20)

    def test_sell_decision_reports_short_exposure(self) -> None:
        prices = pd.DataFrame(
            {"AAPL": [100.0, 99.0, 98.0]},
            index=pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
        )
        store = FakeDecisionStore([
            CommitteeDecision(ticker="AAPL", analysis_date="2026-01-02", decision="SELL", confidence=80)
        ])

        output = CommitteeSignalFollowerPipeline(["AAPL"], store).run_fold(prices.iloc[:1], prices.iloc[1:])

        self.assertEqual(float(output.frame.loc[pd.Timestamp("2026-01-05"), "short_exposure"]), 0.20)


if __name__ == "__main__":
    unittest.main()
