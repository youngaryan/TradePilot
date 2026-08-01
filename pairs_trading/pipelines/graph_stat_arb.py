from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import pandas as pd

from ..core.framework import StrategyOutput, WalkForwardStrategy
from ..core.portfolio import PortfolioManager
from ..research import GraphClusterConfig, find_graph_clusters
from ..strategies import GraphClusterResidualStrategy, GraphClusterTradingConfig


@dataclass(frozen=True)
class GraphStatArbConfig:
    cluster_config: GraphClusterConfig = field(default_factory=GraphClusterConfig)
    trading_config: GraphClusterTradingConfig = field(default_factory=GraphClusterTradingConfig)
    max_clusters: int = 8


class GraphStatArbPipeline(WalkForwardStrategy):
    def __init__(
        self,
        sector_map: Mapping[str, str] | None = None,
        portfolio_manager: PortfolioManager | None = None,
        config: GraphStatArbConfig = GraphStatArbConfig(),
        name: str = "graph_stat_arb",
    ) -> None:
        self.sector_map = dict(sector_map or {})
        self.portfolio_manager = portfolio_manager or PortfolioManager()
        self.config = config
        self.name = name

    def _flat_output(self, index: pd.Index, reason: str) -> StrategyOutput:
        frame = pd.DataFrame(index=index)
        for column in ("signal", "forecast", "position", "cost_estimate", "unit_return", "gross_return", "turnover"):
            frame[column] = 0.0
        frame["short_exposure"] = 0.0
        frame["gross_exposure"] = 0.0
        return StrategyOutput(
            name=self.name,
            frame=frame,
            diagnostics={"status": reason, "clusters": [], "pipeline_type": "graph_stat_arb"},
        ).validate(extra_columns=("unit_return", "gross_return"))

    def build_component_outputs(
        self,
        train_data: pd.DataFrame,
        test_data: pd.DataFrame,
    ) -> tuple[dict[str, StrategyOutput], list[dict[str, object]]]:
        clusters = find_graph_clusters(
            prices=train_data,
            sector_map=self.sector_map,
            config=self.config.cluster_config,
        )[: max(1, self.config.max_clusters)]

        outputs: dict[str, StrategyOutput] = {}
        cluster_payloads: list[dict[str, object]] = []
        for cluster in clusters:
            strategy = GraphClusterResidualStrategy(
                cluster_id=cluster.cluster_id,
                symbols=list(cluster.symbols),
                config=self.config.trading_config,
                cluster_metadata={"cluster": cluster.to_dict()},
            )
            output = strategy.run_fold(
                train_data=train_data[list(cluster.symbols)],
                test_data=test_data[list(cluster.symbols)],
            )
            cluster_payloads.append(cluster.to_dict())
            if output.frame["position"].abs().sum() <= 0.0:
                continue
            outputs[f"cluster_{cluster.cluster_id}"] = output
        return outputs, cluster_payloads

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        outputs, clusters = self.build_component_outputs(train_data=train_data, test_data=test_data)
        if not outputs:
            return self._flat_output(test_data.index, reason="no_tradeable_graph_clusters")

        portfolio_output = self.portfolio_manager.allocate_capital(
            strategy_outputs=outputs,
            portfolio_name=self.name,
        )
        portfolio_output.diagnostics.update(
            {
                "pipeline_type": "graph_stat_arb",
                "clusters": clusters,
                "active_cluster_count": int(len(outputs)),
                "cluster_config": self.config.cluster_config.__dict__,
                "trading_config": self.config.trading_config.__dict__,
            }
        )
        return portfolio_output
