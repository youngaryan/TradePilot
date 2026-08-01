from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .execution import ExecutionConfig, ExecutionEngine
from ..core.framework import StrategyOutput
from .reconciliation import ReconciliationEngine
from .risk import RiskConfig, RiskManager


class BrokerAdapter(ABC):
    @abstractmethod
    def process(self, output: StrategyOutput, *, bars_per_year: int) -> StrategyOutput:
        """Apply risk checks, execution assumptions, and reconciliation to a strategy output."""


@dataclass(frozen=True)
class BrokerConfig:
    risk: RiskConfig = RiskConfig()
    execution: ExecutionConfig = ExecutionConfig()


class SimulatedBroker(BrokerAdapter):
    def __init__(
        self,
        config: BrokerConfig = BrokerConfig(),
        *,
        risk_manager: RiskManager | None = None,
        execution_engine: ExecutionEngine | None = None,
        reconciliation_engine: ReconciliationEngine | None = None,
    ) -> None:
        self.config = config
        self.risk_manager = risk_manager or RiskManager(config.risk)
        self.execution_engine = execution_engine or ExecutionEngine(config.execution)
        self.reconciliation_engine = reconciliation_engine or ReconciliationEngine()

    def process(self, output: StrategyOutput, *, bars_per_year: int) -> StrategyOutput:
        output.validate(extra_columns=("gross_return",))
        risk_adjusted = self.risk_manager.apply(output.frame)
        executed = self.execution_engine.apply(risk_adjusted, bars_per_year=bars_per_year)

        diagnostics: dict[str, Any] = dict(output.diagnostics)
        diagnostics["broker"] = {
            "risk": {
                "max_gross_leverage": self.config.risk.max_gross_leverage,
                "max_net_leverage": self.config.risk.max_net_leverage,
                "max_turnover": self.config.risk.max_turnover,
            },
            "execution": {
                "commission_bps": self.config.execution.commission_bps,
                "spread_bps": self.config.execution.spread_bps,
                "slippage_bps": self.config.execution.slippage_bps,
                "market_impact_bps": self.config.execution.market_impact_bps,
                "borrow_bps_annual": self.config.execution.borrow_bps_annual,
                "delay_bars": self.config.execution.delay_bars,
            },
            "reconciliation": self.reconciliation_engine.summarize(executed),
        }

        return StrategyOutput(
            name=output.name,
            frame=executed,
            diagnostics=diagnostics,
        ).validate(extra_columns=("gross_return",))
