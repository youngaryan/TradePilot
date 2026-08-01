from __future__ import annotations

import unittest

import pandas as pd

from pairs_trading.core.framework import StrategyOutput
from pairs_trading.engines.broker import BrokerConfig, SimulatedBroker
from pairs_trading.engines.execution import ExecutionConfig
from pairs_trading.engines.risk import RiskConfig


class BrokerStackTests(unittest.TestCase):
    def test_simulated_broker_applies_risk_and_execution_costs(self) -> None:
        index = pd.date_range("2024-01-01", periods=6, freq="B")
        frame = pd.DataFrame(
            {
                "signal": [1, 1, 1, -1, -1, 0],
                "forecast": [0.5, 0.6, 0.4, -0.8, -0.5, 0.0],
                "position": [0.0, 1.4, 1.4, -1.2, -1.2, 0.0],
                "gross_return": [0.0, 0.01, -0.003, 0.004, 0.002, 0.0],
                "unit_return": [0.0, 0.007, -0.002, 0.003, 0.0015, 0.0],
                "cost_estimate": [0.0, 0.0002, 0.0001, 0.0002, 0.0001, 0.0],
                "turnover": [0.0, 1.4, 0.0, 2.6, 0.0, 1.2],
                "short_exposure": [0.0, 0.0, 0.0, 0.8, 0.8, 0.0],
                "gross_exposure": [0.0, 1.4, 1.4, 1.2, 1.2, 0.0],
            },
            index=index,
        )

        output = StrategyOutput(name="broker_test", frame=frame).validate(extra_columns=("gross_return",))
        broker = SimulatedBroker(
            BrokerConfig(
                risk=RiskConfig(max_gross_leverage=1.0, max_net_leverage=1.0, max_turnover=2.0),
                execution=ExecutionConfig(
                    commission_bps=0.5,
                    spread_bps=1.0,
                    slippage_bps=0.5,
                    market_impact_bps=0.75,
                    borrow_bps_annual=25.0,
                    delay_bars=1,
                ),
            )
        )

        processed = broker.process(output, bars_per_year=252)

        self.assertIn("net_return", processed.frame.columns)
        self.assertIn("broker_cost", processed.frame.columns)
        self.assertIn("risk_scale", processed.frame.columns)
        self.assertGreater(float(processed.frame["total_cost"].sum()), 0.0)
        self.assertTrue((processed.frame["risk_scale"] <= 1.0).all())
        self.assertIn("broker", processed.diagnostics)
        self.assertIn("reconciliation", processed.diagnostics["broker"])


if __name__ == "__main__":
    unittest.main()
