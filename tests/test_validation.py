from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from pairs_trading.engines.validation import (
    build_validation_report,
    build_walk_forward_boundaries,
    probability_of_backtest_overfitting,
)


class ValidationTests(unittest.TestCase):
    def test_walk_forward_boundaries_respect_purge_and_embargo(self) -> None:
        boundaries = build_walk_forward_boundaries(
            total_bars=120,
            train_bars=40,
            test_bars=10,
            step_bars=10,
            purge_bars=5,
            embargo_bars=2,
        )

        self.assertGreater(len(boundaries), 0)
        first = boundaries[0]
        self.assertEqual(first.train_end, first.test_start - 5)
        if len(boundaries) > 1:
            self.assertEqual(boundaries[1].test_start - boundaries[0].test_start, 12)

    def test_validation_report_includes_dsr_and_pbo(self) -> None:
        rng = np.random.default_rng(31)
        index = pd.date_range("2020-01-01", periods=240, freq="B")
        returns = pd.Series(rng.normal(0.0008, 0.01, len(index)), index=index)
        trial_returns = pd.DataFrame(
            {
                "trial_a": rng.normal(0.0008, 0.01, len(index)),
                "trial_b": rng.normal(0.0002, 0.011, len(index)),
                "trial_c": rng.normal(-0.0001, 0.012, len(index)),
            },
            index=index,
        )

        report = build_validation_report(
            returns=returns,
            bars_per_year=252,
            trial_returns=trial_returns,
            pbo_partitions=8,
        )

        self.assertIn("dsr", report)
        self.assertIn("pbo", report)
        self.assertGreaterEqual(float(report["dsr"]), 0.0)
        self.assertLessEqual(float(report["dsr"]), 1.0)
        self.assertIsNotNone(report["pbo"])

    def test_probability_of_backtest_overfitting_returns_detail(self) -> None:
        rng = np.random.default_rng(41)
        index = pd.date_range("2021-01-01", periods=160, freq="B")
        trial_returns = pd.DataFrame(
            {
                "alpha": rng.normal(0.0010, 0.010, len(index)),
                "beta": rng.normal(0.0004, 0.011, len(index)),
                "gamma": rng.normal(-0.0002, 0.012, len(index)),
                "delta": rng.normal(0.0001, 0.0105, len(index)),
            },
            index=index,
        )

        report = probability_of_backtest_overfitting(
            trial_returns=trial_returns,
            bars_per_year=252,
            partitions=8,
        )

        self.assertIsNotNone(report)
        assert report is not None
        self.assertIn("pbo", report)
        self.assertIn("lambda_logit", report)
        self.assertGreater(len(report["lambda_logit"]), 0)


if __name__ == "__main__":
    unittest.main()
