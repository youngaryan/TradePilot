from __future__ import annotations

from dataclasses import dataclass
import itertools
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.stattools import coint

from ..core.framework import estimate_half_life
from ..data.market import CachedParquetProvider, MarketDataProvider, YahooFinanceProvider


@dataclass(frozen=True)
class PairScreenConfig:
    min_history: int = 252
    correlation_floor: float = 0.55
    coint_pvalue_threshold: float = 0.10
    min_half_life: float = 2.0
    max_half_life: float = 80.0
    target_half_life: float = 20.0


def generate_sector_pairs(sector_map: Mapping[str, str]) -> list[tuple[str, str, str]]:
    sector_buckets: dict[str, list[str]] = {}
    for ticker, sector in sector_map.items():
        sector_buckets.setdefault(sector, []).append(ticker)

    pairs: list[tuple[str, str, str]] = []
    for sector, tickers in sector_buckets.items():
        for ticker1, ticker2 in itertools.combinations(sorted(tickers), 2):
            pairs.append((sector, ticker1, ticker2))
    return pairs


def score_candidate_pair(
    correlation: float,
    coint_pvalue: float,
    half_life: float,
    target_half_life: float,
) -> float:
    stationarity_score = max(0.0, 1.0 - min(coint_pvalue, 1.0))
    correlation_score = max(0.0, min(correlation, 1.0))
    half_life_score = 1.0 / (1.0 + abs(half_life - target_half_life) / max(target_half_life, 1.0))
    return 0.50 * stationarity_score + 0.30 * correlation_score + 0.20 * half_life_score


def rank_sector_pairs(
    prices: pd.DataFrame,
    sector_map: Mapping[str, str],
    screen_config: PairScreenConfig = PairScreenConfig(),
) -> pd.DataFrame:
    results: list[dict[str, float | str]] = []

    for sector, ticker1, ticker2 in generate_sector_pairs(sector_map):
        if ticker1 not in prices.columns or ticker2 not in prices.columns:
            continue

        pair_prices = prices[[ticker1, ticker2]].dropna()
        if len(pair_prices) < screen_config.min_history:
            continue

        returns_corr = pair_prices[ticker1].pct_change().corr(pair_prices[ticker2].pct_change())
        if pd.isna(returns_corr) or returns_corr < screen_config.correlation_floor:
            continue

        try:
            coint_pvalue = float(coint(pair_prices[ticker1], pair_prices[ticker2])[1])
        except Exception:
            continue

        if coint_pvalue > screen_config.coint_pvalue_threshold:
            continue

        ols_model = sm.OLS(pair_prices[ticker1], sm.add_constant(pair_prices[ticker2])).fit()
        hedge_ratio = float(ols_model.params.iloc[1])
        spread = pair_prices[ticker1] - hedge_ratio * pair_prices[ticker2]
        half_life = estimate_half_life(spread)

        if not np.isfinite(half_life):
            continue
        if half_life < screen_config.min_half_life or half_life > screen_config.max_half_life:
            continue

        spread_std = float(spread.std()) if pd.notna(spread.std()) else np.nan
        spread_zscore = 0.0
        if spread_std and spread_std > 0:
            spread_zscore = float((spread.iloc[-1] - spread.mean()) / spread_std)

        rank_score = score_candidate_pair(
            correlation=float(returns_corr),
            coint_pvalue=coint_pvalue,
            half_life=half_life,
            target_half_life=screen_config.target_half_life,
        )

        results.append(
            {
                "Sector": sector,
                "Ticker_1": ticker1,
                "Ticker_2": ticker2,
                "Correlation": float(returns_corr),
                "Coint_PValue": coint_pvalue,
                "Half_Life": float(half_life),
                "Hedge_Ratio": hedge_ratio,
                "Spread_Std": spread_std,
                "Latest_ZScore": spread_zscore,
                "Rank_Score": rank_score,
            }
        )

    results_frame = pd.DataFrame(results)
    if results_frame.empty:
        return results_frame

    return results_frame.sort_values(by="Rank_Score", ascending=False).reset_index(drop=True)


def find_candidate_pairs(
    provider: MarketDataProvider,
    sector_map: Mapping[str, str],
    start_date: str,
    end_date: str,
    interval: str = "1d",
    screen_config: PairScreenConfig = PairScreenConfig(),
) -> pd.DataFrame:
    prices = provider.get_close_prices(
        symbols=list(sector_map.keys()),
        start=start_date,
        end=end_date,
        interval=interval,
    )
    return rank_sector_pairs(prices=prices, sector_map=sector_map, screen_config=screen_config)


def main() -> None:
    demo_sector_map = {
        "KO": "Beverages",
        "PEP": "Beverages",
        "KDP": "Beverages",
        "XOM": "Energy",
        "CVX": "Energy",
        "COP": "Energy",
        "JPM": "Banks",
        "BAC": "Banks",
        "WFC": "Banks",
        "C": "Banks",
    }

    provider = CachedParquetProvider(
        upstream=YahooFinanceProvider(),
        cache_dir="data/cache",
    )
    candidates = find_candidate_pairs(
        provider=provider,
        sector_map=demo_sector_map,
        start_date="2018-01-01",
        end_date="2025-12-31",
    )
    print(candidates.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
