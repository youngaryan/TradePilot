from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..data.news import (
    AlphaVantageNewsProvider,
    BenzingaNewsProvider,
    CompositeHeadlineProvider,
    LocalNewsFileProvider,
    LocalWebSearchHeadlineProvider,
    NewsAPIHeadlineProvider,
    RSSHeadlineProvider,
    WebResearchHeadlineProvider,
)
from ..data.stocktwits import StockTwitsHeadlineProvider
from .config import BackendSettings
from .secrets import CompositeSecretResolver, SecretResolver


def build_headline_provider(
    settings: BackendSettings,
    *,
    provider_names: Sequence[str],
    options: Mapping[str, Any] | None = None,
    request_credentials: Mapping[str, str | None] | None = None,
    resolver: SecretResolver | None = None,
    provider_classes: Mapping[str, Any] | None = None,
) -> CompositeHeadlineProvider:
    """Construct existing headline adapters from server-owned configuration."""

    opts = dict(options or {})
    credentials = dict(request_credentials or {})
    secret_resolver = resolver or CompositeSecretResolver(settings)
    classes = {
        "rss": RSSHeadlineProvider,
        "local_web": LocalWebSearchHeadlineProvider,
        "web": WebResearchHeadlineProvider,
        "local": LocalNewsFileProvider,
        "newsapi": NewsAPIHeadlineProvider,
        "alphavantage": AlphaVantageNewsProvider,
        "benzinga": BenzingaNewsProvider,
        "stocktwits": StockTwitsHeadlineProvider,
        **dict(provider_classes or {}),
    }
    timeout = max(1.0, min(float(opts.get("timeout_seconds", 20.0)), 120.0))
    maximum = max(1, min(int(opts.get("maximum_articles", 100)), 500))
    providers = []
    for name in dict.fromkeys(str(item).strip().lower() for item in provider_names if str(item).strip()):
        if name == "rss":
            providers.append(
                classes["rss"](
                    feed_urls=opts.get("rss_feed_urls") or None,
                    max_items_per_feed=maximum,
                    timeout_seconds=timeout,
                )
            )
        elif name == "local_web":
            providers.append(
                classes["local_web"](
                    feed_urls=opts.get("local_web_search_urls") or None,
                    source_domains=opts.get("domains") or (),
                    direct_urls=opts.get("urls") or (),
                    query_terms=str(opts.get("query_terms") or ""),
                    cache_dir=settings.sentiment_cache_dir / "local_web_index",
                    max_results_per_ticker=maximum,
                    max_crawl_pages_per_source=int(opts.get("max_crawl_pages_per_source", 30)),
                    refresh_minutes=int(opts.get("refresh_minutes", 60)),
                    fetch_article_text=bool(opts.get("fetch_article_text", True)),
                    timeout_seconds=timeout,
                )
            )
        elif name == "web":
            providers.append(
                classes["web"](
                    domains=opts.get("domains") or (),
                    research_urls=opts.get("urls") or (),
                    query_terms=str(opts.get("query_terms") or ""),
                    max_articles_per_ticker=maximum,
                    fetch_article_text=bool(opts.get("fetch_article_text", True)),
                    timeout_seconds=timeout,
                )
            )
        elif name == "local":
            providers.extend(classes["local"](path) for path in opts.get("news_files") or ())
        elif name in {"newsapi", "alphavantage", "benzinga", "stocktwits"}:
            env_names = {
                "newsapi": "NEWSAPI_API_KEY",
                "alphavantage": "ALPHAVANTAGE_API_KEY",
                "benzinga": "BENZINGA_API_KEY",
                "stocktwits": "STOCKTWITS_ACCESS_TOKEN",
            }
            refs = opts.get("secret_refs") if isinstance(opts.get("secret_refs"), Mapping) else {}
            key = credentials.get(name)
            if not key:
                key = secret_resolver.resolve(str(refs.get(name) or f"env:{env_names[name]}"))
            if not key:
                raise ValueError(f"{name} headline provider credentials are not configured.")
            if name == "newsapi":
                providers.append(classes["newsapi"](api_key=key, page_size=min(maximum, 100), timeout_seconds=timeout))
            elif name == "alphavantage":
                providers.append(classes["alphavantage"](api_key=key, limit=maximum, timeout_seconds=timeout))
            elif name == "benzinga":
                providers.append(classes["benzinga"](api_key=key, page_size=min(maximum, 100), timeout_seconds=timeout))
            else:
                providers.append(
                    classes["stocktwits"](
                        access_token=key,
                        max_pages=int(opts.get("stocktwits_max_pages", 20)),
                        timeout_seconds=timeout,
                    )
                )
        else:
            raise ValueError(f"Unsupported headline provider: {name}.")
    if not providers:
        raise ValueError("Choose at least one headline provider.")
    return CompositeHeadlineProvider(providers, skip_errors=True)


__all__ = ["build_headline_provider"]
