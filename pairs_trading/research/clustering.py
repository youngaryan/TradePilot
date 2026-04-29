from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
from typing import Mapping, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class GraphClusterConfig:
    min_history: int = 180
    correlation_floor: float = 0.55
    min_cluster_size: int = 3
    max_cluster_size: int = 8
    use_absolute_correlation: bool = False


@dataclass(frozen=True)
class GraphCluster:
    cluster_id: str
    symbols: tuple[str, ...]
    sector: str | None
    mst_edges: tuple[tuple[str, str, float, float], ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["symbols"] = list(self.symbols)
        payload["mst_edges"] = [
            {"source": source, "target": target, "correlation": correlation, "distance": distance}
            for source, target, correlation, distance in self.mst_edges
        ]
        return payload


class _UnionFind:
    def __init__(self, items: Sequence[str]) -> None:
        self.parent = {item: item for item in items}
        self.rank = {item: 0 for item in items}

    def find(self, item: str) -> str:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: str, right: str) -> bool:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return False
        if self.rank[root_left] < self.rank[root_right]:
            root_left, root_right = root_right, root_left
        self.parent[root_right] = root_left
        if self.rank[root_left] == self.rank[root_right]:
            self.rank[root_left] += 1
        return True


def _distance_from_correlation(correlation: float, *, use_absolute: bool) -> float:
    rho = abs(correlation) if use_absolute else correlation
    return float(np.sqrt(max(0.0, 2.0 * (1.0 - np.clip(rho, -1.0, 1.0)))))


def _candidate_edges(returns: pd.DataFrame, config: GraphClusterConfig) -> list[tuple[str, str, float, float]]:
    correlation = returns.corr().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    edges: list[tuple[str, str, float, float]] = []
    for left, right in combinations(correlation.columns, 2):
        rho = float(correlation.loc[left, right])
        effective_rho = abs(rho) if config.use_absolute_correlation else rho
        if effective_rho < config.correlation_floor:
            continue
        edges.append(
            (
                str(left),
                str(right),
                rho,
                _distance_from_correlation(rho, use_absolute=config.use_absolute_correlation),
            )
        )
    return sorted(edges, key=lambda edge: edge[3])


def _minimum_spanning_tree_edges(symbols: Sequence[str], edges: Sequence[tuple[str, str, float, float]]) -> list[tuple[str, str, float, float]]:
    union_find = _UnionFind(symbols)
    selected: list[tuple[str, str, float, float]] = []
    for edge in edges:
        if union_find.union(edge[0], edge[1]):
            selected.append(edge)
        if len(selected) >= max(len(symbols) - 1, 0):
            break
    return selected


def _split_large_cluster(symbols: list[str], max_cluster_size: int) -> list[list[str]]:
    if len(symbols) <= max_cluster_size:
        return [symbols]
    return [symbols[start : start + max_cluster_size] for start in range(0, len(symbols), max_cluster_size)]


def find_graph_clusters(
    prices: pd.DataFrame,
    sector_map: Mapping[str, str] | None = None,
    config: GraphClusterConfig = GraphClusterConfig(),
) -> list[GraphCluster]:
    """Find correlation-graph clusters with a dependency-free MST/Kruskal pass."""

    if prices.empty:
        return []

    clean_prices = prices.copy().sort_index()
    clean_prices = clean_prices.loc[:, clean_prices.notna().sum(axis=0) >= config.min_history]
    clean_prices = clean_prices.dropna(axis=0, how="all").ffill().dropna(axis=1, how="any")
    if clean_prices.shape[1] < config.min_cluster_size:
        return []

    sector_map = {str(ticker).upper(): str(sector) for ticker, sector in (sector_map or {}).items()}
    buckets: dict[str | None, list[str]] = {}
    if sector_map:
        for symbol in clean_prices.columns:
            sector = sector_map.get(str(symbol).upper())
            if sector is not None:
                buckets.setdefault(sector, []).append(str(symbol))
    else:
        buckets[None] = [str(symbol) for symbol in clean_prices.columns]

    clusters: list[GraphCluster] = []
    for sector, symbols in sorted(buckets.items(), key=lambda item: str(item[0])):
        if len(symbols) < config.min_cluster_size:
            continue
        returns = clean_prices[symbols].pct_change().dropna(how="any")
        if len(returns) < config.min_history:
            continue

        candidate_edges = _candidate_edges(returns, config)
        mst_edges = _minimum_spanning_tree_edges(symbols, candidate_edges)
        if not mst_edges:
            continue

        union_find = _UnionFind(symbols)
        for source, target, _, _ in mst_edges:
            union_find.union(source, target)

        grouped: dict[str, list[str]] = {}
        for symbol in symbols:
            grouped.setdefault(union_find.find(symbol), []).append(symbol)

        for group in grouped.values():
            if len(group) < config.min_cluster_size:
                continue
            group_edges = tuple(edge for edge in mst_edges if edge[0] in group and edge[1] in group)
            for chunk in _split_large_cluster(sorted(group), config.max_cluster_size):
                if len(chunk) < config.min_cluster_size:
                    continue
                chunk_edges = tuple(edge for edge in group_edges if edge[0] in chunk and edge[1] in chunk)
                cluster_id = f"{sector or 'market'}_{len(clusters) + 1}"
                clusters.append(
                    GraphCluster(
                        cluster_id=cluster_id,
                        symbols=tuple(chunk),
                        sector=sector,
                        mst_edges=chunk_edges,
                    )
                )

    return clusters
