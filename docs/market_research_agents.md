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

The default configuration is offline and deterministic:

```powershell
PAIRS_TRADING_MARKET_RESEARCH_DATA_PROVIDER=demo
PAIRS_TRADING_MARKET_RESEARCH_AGENT_TIMEOUT_SECONDS=8.0
PAIRS_TRADING_MARKET_RESEARCH_ARTIFACT_ROOT=artifacts/market_research/reports
PAIRS_TRADING_MARKET_RESEARCH_JOB_STATE_DIR=artifacts/market_research/jobs
```

Set `PAIRS_TRADING_MARKET_RESEARCH_DATA_PROVIDER=cached_yahoo` to use the existing cached Yahoo price provider for close prices. If that provider fails or returns no usable rows, the workflow falls back to demo data and records a warning in the report.

The LLM interface is intentionally a mock provider in v1. Future providers should implement the structured provider interface without accepting raw API keys in requests; use environment variables or a vault reference.

## API

Authenticated premium route:

```text
POST /api/market-research/run-job
GET /api/market-research/jobs
GET /api/market-research/jobs/{job_id}
```

Example request:

```json
{
  "ticker": "AAPL",
  "analysis_date": "2026-05-08",
  "horizon": "swing",
  "provider": "mock",
  "model": "mock-research-v1",
  "options": {}
}
```

The UI is available in the `AI Research` view after login.

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
- The v1 real-data path only attempts existing close-price data when configured.
- Fundamental and news/sentiment integrations are extension points unless separate providers are added.
- Reports are simulated research artifacts and must not be marketed as guaranteed returns or personalized recommendations.
- No brokerage, order placement, or trade execution functionality is included.
