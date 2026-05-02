from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, timedelta
import json
import os

import pandas as pd

from pairs_trading.backend.config import BackendSettings
from pairs_trading.data.news import CompositeHeadlineProvider, LocalNewsFileProvider, NewsAPIHeadlineProvider, RSSHeadlineProvider
from pairs_trading.data.sentiment_accumulator import ShadowSentimentAccumulator
from pairs_trading.engines.backtesting import json_ready
from pairs_trading.features.sentiment import FinBERTSentimentModel, build_best_available_sentiment_model
from pairs_trading.platform import SQLiteMetadataStore
from pairs_trading.backend.telemetry import DailyRefreshService


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect local worker metadata or accumulate local sentiment datasets.")
    parser.add_argument("--kind", choices=("paper", "backtest"), help="Optionally list jobs for one job kind.")
    parser.add_argument("--run-daily-refresh", action="store_true", help="Run the due 24-hour per-user data refresh once.")
    parser.add_argument("--refresh-limit", type=int, default=100, help="Maximum users to refresh in one worker tick.")
    parser.add_argument("--force-refresh", action="store_true", help="Refresh users even if they are not due yet.")
    parser.add_argument("--accumulate-sentiment", action="store_true", help="Fetch free headlines and write local sentiment parquet files.")
    parser.add_argument("--symbols", nargs="*", help="Tickers to accumulate sentiment for.")
    parser.add_argument("--start", help="Start date for sentiment accumulation. Defaults to seven days ago.")
    parser.add_argument("--end", help="End date for sentiment accumulation. Defaults to today.")
    parser.add_argument(
        "--sentiment-provider",
        nargs="*",
        choices=("rss", "newsapi", "local"),
        default=["rss"],
        help="Headline sources for accumulation. RSS is free and enabled by default.",
    )
    parser.add_argument("--rss-feed", nargs="*", help="Optional RSS feed URLs. Use {ticker} for per-symbol RSS templates.")
    parser.add_argument("--news-file", nargs="*", help="Optional local headline CSV/parquet files.")
    parser.add_argument("--newsapi-api-key", help="NewsAPI.org key. Defaults to NEWSAPI_API_KEY.")
    parser.add_argument("--sentiment-output-dir", default="data/sentiment_cache/shadow", help="Accumulator output directory.")
    parser.add_argument("--use-finbert", action="store_true", help="Force FinBERT instead of best local fallback.")
    parser.add_argument("--local-finbert-only", action="store_true", help="Do not download FinBERT; require local cache.")
    args = parser.parse_args()

    settings = BackendSettings.from_env()

    if args.run_daily_refresh:
        result = DailyRefreshService(settings).run_due_users(limit=args.refresh_limit, force=args.force_refresh)
        print(json.dumps(result, indent=2))
        return

    if args.accumulate_sentiment:
        if not args.symbols:
            parser.error("--symbols is required with --accumulate-sentiment.")

        end = pd.Timestamp(args.end or date.today().isoformat()).strftime("%Y-%m-%d")
        start = pd.Timestamp(args.start or (date.today() - timedelta(days=7)).isoformat()).strftime("%Y-%m-%d")
        providers = []
        selected = set(args.sentiment_provider or ["rss"])
        if "rss" in selected:
            providers.append(RSSHeadlineProvider(feed_urls=args.rss_feed))
        if "newsapi" in selected:
            api_key = args.newsapi_api_key or os.getenv("NEWSAPI_API_KEY")
            if not api_key:
                parser.error("NewsAPI accumulation requires --newsapi-api-key or NEWSAPI_API_KEY.")
            providers.append(NewsAPIHeadlineProvider(api_key=api_key))
        if "local" in selected:
            if not args.news_file:
                parser.error("Local accumulation requires at least one --news-file.")
            providers.extend(LocalNewsFileProvider(path) for path in args.news_file)

        headline_provider = providers[0] if len(providers) == 1 else CompositeHeadlineProvider(providers)
        sentiment_model = (
            FinBERTSentimentModel(local_files_only=args.local_finbert_only)
            if args.use_finbert
            else build_best_available_sentiment_model()
        )
        result = ShadowSentimentAccumulator(
            headline_provider=headline_provider,
            sentiment_model=sentiment_model,
            output_dir=args.sentiment_output_dir,
        ).run(tickers=args.symbols, start=start, end=end)
        print(json.dumps(json_ready(asdict(result)), indent=2))
        return

    store = SQLiteMetadataStore(settings.metadata_db_path)
    counts = store.counts()
    payload: dict[str, object] = {
        "metadata_db_path": str(settings.metadata_db_path),
        "counts": {
            "jobs": counts.jobs,
            "deployment_configs": counts.deployment_configs,
            "experiment_runs": counts.experiment_runs,
        },
    }
    if args.kind:
        payload["jobs"] = store.list_jobs(kind=args.kind)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
