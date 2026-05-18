import { useEffect, useState } from "react";
import { Copy, Trash2 } from "lucide-react";

import type { PaperAgentConfig, StrategyCatalogItem } from "../api/types";
import { Badge } from "./Badge";
import { pipelineLabel, splitList, splitSymbols } from "../utils/format";
import { agentHasSentiment, defaultAgentId } from "../utils/quant";

const DEFAULT_SENTIMENT_PROVIDERS = ["rss", "local_web", "local"];
const DEFAULT_NEWS_FILES = ["examples/news_headlines.sample.csv"];

export function withDefaultSentiment<T extends Partial<PaperAgentConfig>>(agent: T): T {
  return {
    ...agent,
    news_provider_names: agent.news_provider_names?.length ? agent.news_provider_names : DEFAULT_SENTIMENT_PROVIDERS,
    news_files: agent.news_files?.length ? agent.news_files : DEFAULT_NEWS_FILES,
    local_web_search_urls: agent.local_web_search_urls ?? [],
    local_web_refresh_minutes: agent.local_web_refresh_minutes ?? 60,
    local_web_max_pages_per_source: agent.local_web_max_pages_per_source ?? 30,
    web_research_urls: agent.web_research_urls ?? [],
    web_research_domains: agent.web_research_domains ?? [],
    web_research_query_terms: agent.web_research_query_terms ?? "",
    web_research_max_articles: agent.web_research_max_articles ?? 4,
    web_research_fetch_article_text: agent.web_research_fetch_article_text ?? true,
    use_finbert: agent.use_finbert ?? false,
    local_finbert_only: agent.local_finbert_only ?? true
  };
}

export function AgentEditor({
  agent,
  catalog,
  paramsText,
  onChange,
  onParamsChange,
  onClone,
  onRemove
}: {
  agent: PaperAgentConfig;
  catalog: StrategyCatalogItem[];
  paramsText: string;
  onChange: (agent: PaperAgentConfig) => void;
  onParamsChange: (value: string) => void;
  onClone: () => void;
  onRemove: () => void;
}) {
  const sentimentEnabled = agentHasSentiment(agent);
  const [symbolsText, setSymbolsText] = useState(agent.symbols.join(" "));

  useEffect(() => {
    setSymbolsText(agent.symbols.join(" "));
  }, [agent.id]);

  function updateSymbolsText(value: string) {
    setSymbolsText(value);
    onChange({ ...agent, symbols: splitSymbols(value) });
  }

  function commitSymbolsText() {
    const nextSymbols = splitSymbols(symbolsText);
    setSymbolsText(nextSymbols.join(" "));
    onChange({ ...agent, symbols: nextSymbols });
  }

  function toggleProvider(provider: string, enabled: boolean) {
    const current = agent.news_provider_names ?? [];
    onChange({
      ...agent,
      news_provider_names: enabled
        ? Array.from(new Set([...current, provider]))
        : current.filter((item) => item !== provider)
    });
  }

  function applyStrategy(pipeline: string) {
    const item = catalog.find((candidate) => candidate.pipeline === pipeline || candidate.id === pipeline);
    const example = item?.paper_config_example as {
      symbols?: string[];
      params?: Record<string, unknown>;
      sector_map_path?: string;
      event_file?: string;
      use_sec_companyfacts?: boolean;
      include_sec_filings?: boolean;
      sec_filing_forms?: string[];
      edgar_user_agent?: string;
      lookback_bars?: number;
    } | undefined;
    const nextParams = example?.params ?? agent.params;
    const nextSymbols = Array.isArray(example?.symbols) ? example.symbols : agent.symbols;
    setSymbolsText(nextSymbols.join(" "));
    onChange({
      ...withDefaultSentiment(agent),
      pipeline,
      symbols: nextSymbols,
      sector_map_path: example?.sector_map_path ?? agent.sector_map_path,
      event_file: example?.event_file ?? agent.event_file,
      use_sec_companyfacts: example?.use_sec_companyfacts ?? agent.use_sec_companyfacts,
      include_sec_filings: example?.include_sec_filings ?? agent.include_sec_filings,
      sec_filing_forms: example?.sec_filing_forms ?? agent.sec_filing_forms,
      edgar_user_agent: example?.edgar_user_agent ?? agent.edgar_user_agent,
      lookback_bars: example?.lookback_bars ?? agent.lookback_bars,
      params: nextParams
    });
    onParamsChange(JSON.stringify(nextParams, null, 2));
  }

  function toggleSentiment(enabled: boolean) {
    if (!enabled) {
      onChange({
        ...agent,
        use_finbert: false,
        local_finbert_only: false,
        daily_sentiment_file: null,
        news_provider_names: [],
        news_files: [],
        rss_feed_urls: [],
        local_web_search_urls: [],
        local_web_refresh_minutes: 60,
        local_web_max_pages_per_source: 30,
        web_research_urls: [],
        web_research_domains: [],
        web_research_query_terms: "",
        web_research_max_articles: 4,
        web_research_fetch_article_text: true,
        newsapi_api_key: null,
        news_topics: []
      });
      return;
    }
    onChange({
      ...agent,
      use_finbert: false,
      news_provider_names: agent.news_provider_names?.length ? agent.news_provider_names : DEFAULT_SENTIMENT_PROVIDERS,
      news_files: agent.news_files?.length ? agent.news_files : DEFAULT_NEWS_FILES,
      local_web_search_urls: agent.local_web_search_urls ?? [],
      local_web_refresh_minutes: agent.local_web_refresh_minutes ?? 60,
      local_web_max_pages_per_source: agent.local_web_max_pages_per_source ?? 30,
      web_research_max_articles: agent.web_research_max_articles ?? 4,
      web_research_fetch_article_text: agent.web_research_fetch_article_text ?? true,
      news_topics: agent.news_topics?.length ? agent.news_topics : ["earnings"]
    });
  }

  function toggleOfficialEvents(enabled: boolean) {
    const defaultForms = agent.sec_filing_forms?.length ? agent.sec_filing_forms : ["8-K", "10-Q", "10-K"];
    onChange({
      ...agent,
      include_sec_filings: enabled,
      sec_filing_forms: enabled ? defaultForms : agent.sec_filing_forms
    });
  }

  return (
    <article className="agent-card">
      <div className="agent-card__header">
        <div>
          <strong>{agent.name}</strong>
          <span>{pipelineLabel(agent.pipeline)} | {agent.interval}</span>
        </div>
        <div className="badge-row">
          <Badge label={sentimentEnabled ? "sentiment on" : "price only"} tone={sentimentEnabled ? "good" : "neutral"} />
          <Badge label={`${agent.lookback_bars} bars`} tone="info" />
        </div>
      </div>

      <div className="form-grid">
        <label htmlFor="lo-agent-name">
          Agent name
          <input id="lo-agent-name" value={agent.name} onChange={(event) => onChange({ ...agent, name: event.target.value })} />
        </label>
        <label htmlFor="lo-method">
          Method
          <select id="lo-method" value={agent.pipeline} onChange={(event) => applyStrategy(event.target.value)}>
            {catalog.map((item) => (
              <option key={item.id} value={item.pipeline}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor="lo-timeframe">
          Timeframe
          <select id="lo-timeframe" value={agent.interval} onChange={(event) => onChange({ ...agent, interval: event.target.value })}>
            <option value="1d">Daily bars</option>
            <option value="1wk">Weekly bars</option>
            <option value="1h">Hourly bars</option>
            <option value="30m">30 minute bars</option>
          </select>
        </label>
        <label htmlFor="lo-lookback-bars">
          Lookback bars
          <input id="lo-lookback-bars" type="number" value={agent.lookback_bars} onChange={(event) => onChange({ ...agent, lookback_bars: Number(event.target.value) })} />
        </label>
      </div>

      <label htmlFor="lo-symbols">
        Symbols
        <input id="lo-symbols" value={symbolsText} onChange={(event) => updateSymbolsText(event.target.value)} onBlur={commitSymbolsText} placeholder="SPY QQQ TLT GLD" />
        <small>ETF, event, and directional methods trade symbols directly. Stat-arb can use the sector map instead.</small>
      </label>

      <div className="form-grid">
        <label htmlFor="lo-sector-map">
          Sector map
          <input id="lo-sector-map" value={agent.sector_map_path ?? ""} onChange={(event) => onChange({ ...agent, sector_map_path: event.target.value || null })} placeholder="examples/sector_map.sample.json" />
        </label>
        <label htmlFor="lo-event-file">
          Event file
          <input id="lo-event-file" value={agent.event_file ?? ""} onChange={(event) => onChange({ ...agent, event_file: event.target.value || null })} placeholder="examples/events.sample.csv" />
        </label>
      </div>

      <div className="sentiment-panel official-events-panel">
        <label className="checkbox-line" htmlFor="lo-add-official-sec-filing-events">
          <input id="lo-add-official-sec-filing-events" type="checkbox" checked={Boolean(agent.include_sec_filings)} onChange={(event) => toggleOfficialEvents(event.target.checked)} />
          Add official SEC filing events
        </label>
        <p>
          Adds auditable company events from SEC EDGAR, including earnings 8-K filings, 10-Q quarterly reports, and 10-K annual reports.
          Use this with the event-driven method when you want official filing dates considered.
        </p>
        <label className="checkbox-line" htmlFor="lo-add-sec-company-facts-scores-too">
          <input id="lo-add-sec-company-facts-scores-too" type="checkbox" checked={Boolean(agent.use_sec_companyfacts)} onChange={(event) => onChange({ ...agent, use_sec_companyfacts: event.target.checked })} />
          Add SEC company facts scores too
        </label>
        {agent.include_sec_filings || agent.use_sec_companyfacts ? (
          <div className="form-grid">
            {agent.include_sec_filings ? (
              <label htmlFor="lo-sec-filing-forms">
                SEC filing forms
                <input id="lo-sec-filing-forms"
                  value={(agent.sec_filing_forms ?? ["8-K", "10-Q", "10-K"]).join(" ")}
                  onChange={(event) => onChange({ ...agent, sec_filing_forms: splitList(event.target.value).map((form) => form.toUpperCase()) })}
                  placeholder="8-K 10-Q 10-K"
                />
              </label>
            ) : null}
            <label htmlFor="lo-sec-user-agent">
              SEC user agent
              <input id="lo-sec-user-agent"
                value={agent.edgar_user_agent ?? ""}
                onChange={(event) => onChange({ ...agent, edgar_user_agent: event.target.value || null })}
                placeholder="Your Name your@email.com"
              />
              <small>SEC asks automated API clients to identify themselves with contact information.</small>
            </label>
          </div>
        ) : null}
      </div>

      <div className="sentiment-panel">
        <label className="checkbox-line" htmlFor="lo-add-news-sentiment-overlay">
          <input id="lo-add-news-sentiment-overlay" type="checkbox" checked={sentimentEnabled} onChange={(event) => toggleSentiment(event.target.checked)} />
          Add news/sentiment overlay
        </label>
        <p>Enabled by default. Unselect sources you do not want. Stat-arb uses this for ranking/conviction, and PEAD blends it with event scores.</p>
        {sentimentEnabled ? (
          <div className="form-grid">
            <label htmlFor="lo-daily-sentiment-file">
              Daily sentiment file
              <input id="lo-daily-sentiment-file" value={agent.daily_sentiment_file ?? ""} onChange={(event) => onChange({ ...agent, daily_sentiment_file: event.target.value || null })} placeholder="data/sentiment/daily.parquet" />
            </label>
            <label htmlFor="lo-news-providers">
              News providers
              <input id="lo-news-providers" value={(agent.news_provider_names ?? []).join(" ")} onChange={(event) => onChange({ ...agent, news_provider_names: splitList(event.target.value) })} placeholder="rss local_web local newsapi alphavantage benzinga stocktwits" />
            </label>
            <div className="provider-check-grid provider-check-grid--compact">
              {["rss", "local_web", "web", "local", "newsapi", "alphavantage", "benzinga", "stocktwits"].map((provider) => (
                <label key={provider} className="checkbox-line" htmlFor={`lo-${provider}`}>
                  <input
                    id={`lo-${provider}`}
                    type="checkbox"
                    checked={Boolean(agent.news_provider_names?.includes(provider))}
                    onChange={(event) => toggleProvider(provider, event.target.checked)}
                  />
                  {provider}
                </label>
              ))}
            </div>
            <label htmlFor="lo-rss-feeds">
              RSS feeds
              <input id="lo-rss-feeds" value={(agent.rss_feed_urls ?? []).join(" ")} onChange={(event) => onChange({ ...agent, rss_feed_urls: splitList(event.target.value) })} placeholder="optional; default Yahoo template is used for rss" />
            </label>
            <label htmlFor="lo-local-web-search-feeds">
              Local web-search feeds
              <input id="lo-local-web-search-feeds" value={(agent.local_web_search_urls ?? []).join(" ")} onChange={(event) => onChange({ ...agent, local_web_search_urls: splitList(event.target.value) })} placeholder="optional RSS/Atom feeds or {ticker} templates" />
            </label>
            <label htmlFor="lo-local-web-cache-refresh-minutes">
              Local web cache refresh minutes
              <input id="lo-local-web-cache-refresh-minutes" type="number" min={0} max={1440} value={agent.local_web_refresh_minutes ?? 60} onChange={(event) => onChange({ ...agent, local_web_refresh_minutes: Number(event.target.value) })} />
            </label>
            <label htmlFor="lo-website-domains-to-crawl">
              Website domains to crawl
              <input id="lo-website-domains-to-crawl" value={(agent.web_research_domains ?? []).join(" ")} onChange={(event) => onChange({ ...agent, web_research_domains: splitList(event.target.value) })} placeholder="optional trusted domains: reuters.com cnbc.com" />
            </label>
            <label htmlFor="lo-website-pages-per-source">
              Website pages per source
              <input id="lo-website-pages-per-source" type="number" min={1} max={250} value={agent.local_web_max_pages_per_source ?? 30} onChange={(event) => onChange({ ...agent, local_web_max_pages_per_source: Number(event.target.value) })} />
            </label>
            <label htmlFor="lo-direct-web-urls">
              Direct web URLs
              <input id="lo-direct-web-urls" value={(agent.web_research_urls ?? []).join(" ")} onChange={(event) => onChange({ ...agent, web_research_urls: splitList(event.target.value) })} placeholder="optional article URLs or {ticker} templates" />
            </label>
            <label htmlFor="lo-web-query-terms">
              Web query terms
              <input id="lo-web-query-terms" value={agent.web_research_query_terms ?? ""} onChange={(event) => onChange({ ...agent, web_research_query_terms: event.target.value })} placeholder="earnings OR guidance" />
            </label>
            <label htmlFor="lo-web-articles-per-symbol">
              Web articles per symbol
              <input id="lo-web-articles-per-symbol" type="number" min={1} max={25} value={agent.web_research_max_articles ?? 4} onChange={(event) => onChange({ ...agent, web_research_max_articles: Number(event.target.value) })} />
            </label>
            <label htmlFor="lo-news-files">
              News files
              <input id="lo-news-files" value={(agent.news_files ?? []).join(" ")} onChange={(event) => onChange({ ...agent, news_files: splitList(event.target.value) })} placeholder="data/news/headlines.csv" />
            </label>
            <label htmlFor="lo-newsapi-key">
              NewsAPI key
              <input id="lo-newsapi-key" value={agent.newsapi_api_key ?? ""} onChange={(event) => onChange({ ...agent, newsapi_api_key: event.target.value || null })} placeholder="optional; or set NEWSAPI_API_KEY in the backend environment" />
            </label>
            <label htmlFor="lo-stocktwits-token">
              StockTwits token
              <input id="lo-stocktwits-token" value={agent.stocktwits_access_token ?? ""} onChange={(event) => onChange({ ...agent, stocktwits_access_token: event.target.value || null })} placeholder="optional; or set STOCKTWITS_ACCESS_TOKEN in the backend environment" />
            </label>
            <label htmlFor="lo-news-topics">
              News topics
              <input id="lo-news-topics" value={(agent.news_topics ?? []).join(" ")} onChange={(event) => onChange({ ...agent, news_topics: splitList(event.target.value) })} placeholder="earnings macro" />
            </label>
            <label className="checkbox-line" htmlFor="lo-fetch-web-pages-and-summarize-lightly">
              <input id="lo-fetch-web-pages-and-summarize-lightly" type="checkbox" checked={agent.web_research_fetch_article_text ?? true} onChange={(event) => onChange({ ...agent, web_research_fetch_article_text: event.target.checked })} />
              Fetch web pages and summarize lightly
            </label>
            <label className="checkbox-line" htmlFor="lo-use-finbert-when-available-heavier">
              <input id="lo-use-finbert-when-available-heavier" type="checkbox" checked={Boolean(agent.use_finbert)} onChange={(event) => onChange({ ...agent, use_finbert: event.target.checked })} />
              Use FinBERT when available (heavier)
            </label>
            <label className="checkbox-line" htmlFor="lo-require-local-finbert-cache">
              <input id="lo-require-local-finbert-cache" type="checkbox" checked={Boolean(agent.local_finbert_only)} onChange={(event) => onChange({ ...agent, local_finbert_only: event.target.checked })} />
              Require local FinBERT cache
            </label>
          </div>
        ) : null}
      </div>

      <label htmlFor="lo-method-parameters-json">
        Method parameters JSON
        <textarea id="lo-method-parameters-json" rows={7} value={paramsText} onChange={(event) => onParamsChange(event.target.value)} spellCheck={false} />
        <small>This is passed to the backend strategy spec. Invalid JSON blocks deployment before it touches ledgers.</small>
      </label>

      <div className="button-row">
        <button type="button" className="ghost-button" onClick={onClone}>
          <Copy size={16} />
          Clone
        </button>
        <button type="button" className="danger-button" onClick={onRemove}>
          <Trash2 size={16} />
          Remove
        </button>
      </div>
    </article>
  );
}
