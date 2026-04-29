# Worker App

This is the future home for durable background workers.

The current backend still runs jobs with in-process thread pools, but job metadata is now mirrored into SQLite so a future worker can pick up the same contracts.

Inspect local metadata:

```powershell
.\.venv\Scripts\python.exe apps\worker\main.py
```

List paper jobs:

```powershell
.\.venv\Scripts\python.exe apps\worker\main.py --kind paper
```

List backtest jobs:

```powershell
.\.venv\Scripts\python.exe apps\worker\main.py --kind backtest
```

Build the local free sentiment dataset:

```powershell
.\.venv\Scripts\python.exe apps\worker\main.py --accumulate-sentiment --symbols AAPL MSFT NVDA --sentiment-provider rss --start 2026-04-01 --end 2026-04-29
```

This writes:

- `data/sentiment_cache/shadow/raw_headlines.parquet`
- `data/sentiment_cache/shadow/scored_headlines.parquet`
- `data/sentiment_cache/shadow/daily_sentiment.parquet`

Use the daily file in PEAD or stat-arb configs:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading.apps.cli --pipeline pead_sentiment --symbols AAPL MSFT NVDA --event-file examples/events.sample.csv --daily-sentiment-file data/sentiment_cache/shadow/daily_sentiment.parquet
```

Optional NewsAPI supplement:

```powershell
$env:NEWSAPI_API_KEY="your_key"
.\.venv\Scripts\python.exe apps\worker\main.py --accumulate-sentiment --symbols AAPL MSFT NVDA --sentiment-provider rss newsapi
```

Offline sample smoke test:

```powershell
.\.venv\Scripts\python.exe apps\worker\main.py --accumulate-sentiment --symbols AAPL MSFT NVDA --sentiment-provider local --news-file examples/news_headlines.sample.csv --start 2024-01-01 --end 2024-02-10 --sentiment-output-dir artifacts/smoke_tests/sentiment_shadow
```
