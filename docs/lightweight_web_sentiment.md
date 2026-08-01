# Lightweight Web Sentiment

This project can now add free web-research headlines to the sentiment pipeline without running a heavy local language model.

## What It Does

- `rss` reads live RSS feeds, including Yahoo Finance ticker feeds and Reddit `.rss` feeds.
- `local_web` builds a local cached search index from RSS/Atom feeds, configured website domains, seed pages, and optional direct URLs. This is the default no-key web tool and avoids hosted search API rate limits.
- `web` discovers recent articles through GDELT DOC, optionally restricted to trusted domains. Keep it off unless you need broad hosted discovery.
- Direct article URLs or URL templates containing `{ticker}` can be used by both local web search and GDELT web research.
- Page text is summarized with `lightweight_extractive_v1`, a small stdlib-only extractive summarizer.
- Sentiment scoring defaults to the fast local fallback model. FinBERT is still optional when your machine can handle it.

## Frontend Usage

Open `Sentiment Data Lab`, leave `RSS firehose`, `Local web search`, and `Local files` selected, then run the accumulator.

Recommended weak-hardware settings:

- Keep `Use FinBERT when available` unchecked.
- Keep `Fetch web pages and create lightweight summaries` checked.
- Set `Web articles per symbol` to `3-4`.
- Keep `Local web cache refresh minutes` at `60` or higher to avoid repeated source fetches.
- Add website domains such as `marketwatch.com cnbc.com` to crawl each site's sitemap/homepage links into the local cache.
- Use `Website pages per source` to control how deep the local crawler goes.
- Select `GDELT web research` only when the local RSS/page index is not enough.

## API Example

```json
{
  "symbols": ["NVDA", "GLD"],
  "start": "2026-04-01",
  "end": "2026-05-02",
  "providers": ["rss", "local_web"],
  "local_web_search_urls": [],
  "local_web_refresh_minutes": 60,
  "local_web_max_pages_per_source": 30,
  "web_research_domains": ["marketwatch.com", "cnbc.com"],
  "web_research_query_terms": "earnings OR guidance OR gold",
  "web_research_max_articles": 4,
  "web_research_fetch_article_text": true,
  "use_finbert": false,
  "local_finbert_only": true,
  "output_dir": "data/sentiment_cache/shadow"
}
```

## CLI Example

```powershell
.\.venv\Scripts\python.exe -m pairs_trading.apps.cli `
  --pipeline pead_sentiment `
  --symbols NVDA MSFT `
  --event-file examples/events.sample.csv `
  --news-provider rss local_web local `
  --news-file examples/news_headlines.sample.csv `
  --web-research-domain marketwatch.com cnbc.com `
  --local-web-refresh-minutes 60 `
  --local-web-max-pages-per-source 30 `
  --web-research-max-articles 4 `
  --local-finbert-only
```

Use `--local-web-search-feed` to add RSS/Atom feeds to the local index. Use `--web-research-metadata-only` if you enable GDELT but only want discovered titles and do not want the app to fetch article pages.
