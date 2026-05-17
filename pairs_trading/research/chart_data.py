from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import pandas as pd

from .market_research_agents import PriceBar


class ChartDataBuilder:
    def __init__(self) -> None:
        pass

    def price_chart(
        self,
        prices: list[PriceBar],
        recommendation_dates: list[str] | None = None,
        ticker: str = "",
    ) -> dict[str, Any]:
        if not prices:
            return {"type": "price", "ticker": ticker, "data": [], "markers": []}
        df = pd.DataFrame([{"date": p.date, "close": p.close} for p in prices])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")

        close = df["close"].values.astype(float)
        sma_20 = pd.Series(close).rolling(20).mean().values if len(close) >= 20 else np.full_like(close, np.nan)
        sma_50 = pd.Series(close).rolling(50).mean().values if len(close) >= 50 else np.full_like(close, np.nan)

        markers = []
        if recommendation_dates:
            for rd in recommendation_dates:
                closest = df.iloc[(df["date"] - pd.Timestamp(rd)).abs().argsort()[:1]]
                if not closest.empty:
                    row = closest.iloc[0]
                    markers.append({"date": str(row["date"]), "price": float(row["close"]), "label": "Recommendation"})

        data = []
        for i in range(len(df)):
            row = df.iloc[i]
            data.append({
                "date": str(row["date"]),
                "close": round(float(row["close"]), 4),
                "sma20": round(float(sma_20[i]), 4) if np.isfinite(sma_20[i]) else None,
                "sma50": round(float(sma_50[i]), 4) if np.isfinite(sma_50[i]) else None,
            })

        return {"type": "price", "ticker": ticker, "data": data, "markers": markers}

    def spread_chart(
        self,
        prices_a: list[PriceBar],
        prices_b: list[PriceBar],
        ticker_a: str = "",
        ticker_b: str = "",
        hedge_ratio: float = 1.0,
        recommendation_dates: list[str] | None = None,
    ) -> dict[str, Any]:
        if not prices_a or not prices_b:
            return {"type": "spread", "pair": f"{ticker_a}-{ticker_b}", "data": [], "bands": [], "markers": []}

        df_a = pd.DataFrame([{"date": p.date, "close": p.close} for p in prices_a])
        df_b = pd.DataFrame([{"date": p.date, "close": p.close} for p in prices_b])
        df_a["date"] = pd.to_datetime(df_a["date"])
        df_b["date"] = pd.to_datetime(df_b["date"])

        merged = pd.merge(df_a, df_b, on="date", suffixes=("_a", "_b")).sort_values("date")
        if merged.empty:
            return {"type": "spread", "pair": f"{ticker_a}-{ticker_b}", "data": [], "bands": [], "markers": []}

        merged["spread"] = merged["close_a"] - hedge_ratio * merged["close_b"]
        spread_mean_raw = merged["spread"].mean()
        spread_std_raw = merged["spread"].std()
        spread_mean = float(spread_mean_raw) if np.isfinite(spread_mean_raw) else 0.0
        spread_std = float(spread_std_raw) if np.isfinite(spread_std_raw) else 0.0

        merged["zscore"] = (merged["spread"] - spread_mean) / spread_std if spread_std > 0 else 0.0

        data = []
        for _, row in merged.iterrows():
            data.append({
                "date": str(row["date"]),
                "spread": round(float(row["spread"]), 4),
                "zscore": round(float(row["zscore"]), 4),
            })

        bands = {
            "mean": round(float(spread_mean), 4),
            "std": round(float(spread_std), 4) if np.isfinite(spread_std) else 0,
            "upper_1sigma": round(float(spread_mean + spread_std), 4),
            "lower_1sigma": round(float(spread_mean - spread_std), 4),
            "upper_2sigma": round(float(spread_mean + 2 * spread_std), 4),
            "lower_2sigma": round(float(spread_mean - 2 * spread_std), 4),
        }

        markers = []
        if recommendation_dates:
            for rd in recommendation_dates:
                closest = merged.iloc[(merged["date"] - pd.Timestamp(rd)).abs().argsort()[:1]]
                if not closest.empty:
                    row = closest.iloc[0]
                    markers.append({
                        "date": str(row["date"]),
                        "spread": round(float(row["spread"]), 4),
                        "zscore": round(float(row["zscore"]), 4),
                        "label": "Recommendation",
                    })

        return {"type": "spread", "pair": f"{ticker_a}-{ticker_b}", "data": data, "bands": bands, "markers": markers}

    def zscore_chart(
        self,
        prices_a: list[PriceBar],
        prices_b: list[PriceBar],
        ticker_a: str = "",
        ticker_b: str = "",
        hedge_ratio: float = 1.0,
        recommendation_dates: list[str] | None = None,
    ) -> dict[str, Any]:
        spread_data = self.spread_chart(prices_a, prices_b, ticker_a, ticker_b, hedge_ratio, recommendation_dates)
        if not spread_data["data"]:
            return {"type": "zscore", "pair": f"{ticker_a}-{ticker_b}", "data": [], "thresholds": {}, "markers": []}

        data = [{"date": p["date"], "zscore": p["zscore"]} for p in spread_data["data"]]
        thresholds = {
            "upper_entry": 2.0,
            "upper_exit": 1.0,
            "lower_entry": -2.0,
            "lower_exit": -1.0,
        }
        markers = spread_data.get("markers", [])
        return {"type": "zscore", "pair": f"{ticker_a}-{ticker_b}", "data": data, "thresholds": thresholds, "markers": markers}

    def correlation_chart(
        self,
        prices_a: list[PriceBar],
        prices_b: list[PriceBar],
        ticker_a: str = "",
        ticker_b: str = "",
        rolling_window: int = 30,
    ) -> dict[str, Any]:
        if not prices_a or not prices_b:
            return {"type": "correlation", "pair": f"{ticker_a}-{ticker_b}", "data": []}

        df_a = pd.DataFrame([{"date": p.date, "close": p.close} for p in prices_a])
        df_b = pd.DataFrame([{"date": p.date, "close": p.close} for p in prices_b])
        df_a["date"] = pd.to_datetime(df_a["date"])
        df_b["date"] = pd.to_datetime(df_b["date"])

        merged = pd.merge(df_a, df_b, on="date", suffixes=("_a", "_b")).sort_values("date")
        if merged.empty:
            return {"type": "correlation", "pair": f"{ticker_a}-{ticker_b}", "data": []}

        returns_a = merged["close_a"].pct_change()
        returns_b = merged["close_b"].pct_change()
        rolling_corr = returns_a.rolling(rolling_window).corr(returns_b)
        overall_corr_raw = returns_a.corr(returns_b) if len(returns_a) > 1 else 0.0
        overall_corr = float(overall_corr_raw) if np.isfinite(overall_corr_raw) else 0.0

        data = []
        for i in range(len(merged)):
            row = merged.iloc[i]
            corr_val = rolling_corr.iloc[i]
            data.append({
                "date": str(row["date"]),
                "rolling_correlation": round(float(corr_val), 4) if np.isfinite(corr_val) else None,
            })

        return {
            "type": "correlation",
            "pair": f"{ticker_a}-{ticker_b}",
            "data": data,
            "overall_correlation": round(overall_corr, 4),
            "rolling_window": rolling_window,
        }

    def returns_chart(
        self,
        prices: list[PriceBar],
        benchmark_prices: list[PriceBar] | None = None,
        ticker: str = "",
        benchmark_ticker: str = "SPY",
    ) -> dict[str, Any]:
        if not prices:
            return {"type": "returns", "ticker": ticker, "data": []}

        df = pd.DataFrame([{"date": p.date, "close": p.close} for p in prices])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        df["return"] = df["close"] / df["close"].iloc[0] - 1

        data = [{"date": str(row["date"]), "return": round(float(row["return"]), 6)} for _, row in df.iterrows()]

        result: dict[str, Any] = {"type": "returns", "ticker": ticker, "data": data}
        if benchmark_prices:
            df_bench = pd.DataFrame([{"date": p.date, "close": p.close} for p in benchmark_prices])
            df_bench["date"] = pd.to_datetime(df_bench["date"])
            df_bench = df_bench.sort_values("date")
            merged = pd.merge(df, df_bench, on="date", suffixes=("", "_bench"))
            if not merged.empty:
                merged["bench_return"] = merged["close_bench"] / merged["close_bench"].iloc[0] - 1
                bench_data = [
                    {"date": str(row["date"]), "return": round(float(row["bench_return"]), 6)}
                    for _, row in merged.iterrows()
                ]
                result["benchmark"] = {"ticker": benchmark_ticker, "data": bench_data}
        return result

    def build_all(
        self,
        prices: dict[str, list[PriceBar]],
        tickers: list[str],
        recommendation_dates: list[str] | None = None,
        pair: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"charts": {}}
        for t in tickers:
            t_prices = prices.get(t, [])
            result["charts"][f"price_{t}"] = self.price_chart(t_prices, recommendation_dates, t)
            result["charts"][f"returns_{t}"] = self.returns_chart(t_prices, ticker=t)

        if pair and pair[0] in prices and pair[1] in prices:
            a, b = pair
            result["charts"]["spread"] = self.spread_chart(prices[a], prices[b], a, b, recommendation_dates=recommendation_dates)
            result["charts"]["zscore"] = self.zscore_chart(prices[a], prices[b], a, b, recommendation_dates=recommendation_dates)
            result["charts"]["correlation"] = self.correlation_chart(prices[a], prices[b], a, b)

        return result
