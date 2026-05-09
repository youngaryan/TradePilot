# Market Research Agents

The market research committee is a research-only workflow inspired by multi-agent trading analysis frameworks. It does not execute trades, connect to brokers, or provide financial advice. Every report includes:

> For research and educational purposes only. Not financial advice.

## What It Does

The workflow accepts a ticker, optional analysis date, and horizon (`intraday`, `swing`, or `long-term`). It builds a shared market context, runs role-specific analyst agents, and returns a structured report with a simulated decision: `BUY`, `HOLD`, `SELL`, or `AVOID`.

The v1 agent roles are:

- Technical Analyst
- Fundamental Analyst
- News/Sentiment Analyst
- Risk Analyst
- Bull Researcher
- Bear Researcher
- Trader/Synthesizer
- Portfolio/Risk Manager

The final report contains the simulated decision, confidence, bull and bear theses, signal groups, data-quality notes, provenance, warnings, raw agent outputs, and an audit trail.

## Configuration

The default local configuration is offline and deterministic. It requires no API keys and is intended for development and tests:

```powershell
PAIRS_TRADING_MARKET_RESEARCH_DATA_PROVIDER=demo
PAIRS_TRADING_MARKET_RESEARCH_AGENT_TIMEOUT_SECONDS=8.0
PAIRS_TRADING_MARKET_RESEARCH_ARTIFACT_ROOT=artifacts/market_research/reports
PAIRS_TRADING_MARKET_RESEARCH_JOB_STATE_DIR=artifacts/market_research/jobs
PAIRS_TRADING_MARKET_RESEARCH_LLM_PROVIDER=mock
PAIRS_TRADING_MARKET_RESEARCH_LLM_MODEL=mock-research-v1
```

Set `PAIRS_TRADING_MARKET_RESEARCH_DATA_PROVIDER=cached_yahoo` to use the existing cached Yahoo price provider for close prices. If that provider fails or returns no usable rows, the workflow falls back to demo data and records a warning in the report.

Real structured LLM generation is server-configured only. Do not send API keys or provider secrets in requests.

```powershell
PAIRS_TRADING_SECRET_BACKEND=env
PAIRS_TRADING_MARKET_RESEARCH_LLM_PROVIDER=openai
PAIRS_TRADING_MARKET_RESEARCH_LLM_MODEL=gpt-4.1-mini
PAIRS_TRADING_MARKET_RESEARCH_OPENAI_API_KEY_REF=env:OPENAI_API_KEY
OPENAI_API_KEY=...
```

Anthropic can be selected with:

```powershell
PAIRS_TRADING_MARKET_RESEARCH_LLM_PROVIDER=anthropic
PAIRS_TRADING_MARKET_RESEARCH_LLM_MODEL=claude-3-5-sonnet-latest
PAIRS_TRADING_MARKET_RESEARCH_ANTHROPIC_API_KEY_REF=env:ANTHROPIC_API_KEY
ANTHROPIC_API_KEY=...
```

Production startup rejects `mock` and `disabled` LLM providers when market research generation is enabled. Provider metadata persisted with reports is sanitized: provider name, model, prompt version/hash metadata, agent versions, warnings, and latency/token metadata where available. Raw prompts, provider responses, and secrets are not returned to the frontend.

When `PAIRS_TRADING_MARKET_RESEARCH_DATA_PROVIDER=cached_yahoo`, the backend also attempts to enrich the research context with existing tenant sentiment datasets and the financial-events provider. Missing, stale, or incomplete data is surfaced as report warnings and data-quality notes.

## API

Authenticated premium route:

```text
POST /api/market-research/run-job
GET /api/market-research/jobs
GET /api/market-research/jobs/{job_id}
GET /api/workspaces/reports
GET /api/workspaces/reports/{report_id}
DELETE /api/workspaces/reports/{report_id}
POST /api/workspaces/reports/{report_id}/regenerate
GET /api/workspaces/reports/{report_id}/export?format=json
```

Example request:

```json
{
  "ticker": "AAPL",
  "analysis_date": "2026-05-08",
  "horizon": "swing",
  "include_sentiment": true,
  "include_financial_events": true,
  "lookback_days": 180,
  "options": {}
}
```

The UI is available in the `AI Research` view after login. Saved report history and detail pages are also available in Workspace under `Reports`.

## Local Use

Start the backend and frontend as described in the fullstack workflow docs, then open the web app and run the AI Research view. The demo provider works without API keys.

Targeted tests:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_market_research_agents tests.test_backend_app tests.test_frontend_contracts -v
npm --prefix frontend run typecheck
```

Full test suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm --prefix frontend run build
```

## Limitations

- The default provider uses deterministic demo data, not live financial advice.
- Real LLM providers require server-side environment or vault configuration.
- Sentiment enrichment uses already-created tenant datasets; it does not auto-trigger crawling or paid accumulation.
- Financial-events enrichment uses existing verified/inferred provider output and records missing-data warnings when unavailable.
- Reports are simulated research artifacts and must not be marketed as guaranteed returns or personalized recommendations.
- No brokerage, order placement, or trade execution functionality is included.
