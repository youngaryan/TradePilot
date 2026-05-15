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
PAIRS_TRADING_MARKET_RESEARCH_AGENT_TIMEOUT_SECONDS=120.0
PAIRS_TRADING_MARKET_RESEARCH_ARTIFACT_ROOT=artifacts/market_research/reports
PAIRS_TRADING_MARKET_RESEARCH_JOB_STATE_DIR=artifacts/market_research/jobs
PAIRS_TRADING_MARKET_RESEARCH_LLM_PROVIDER=mock
PAIRS_TRADING_MARKET_RESEARCH_LLM_MODEL=mock-research-v1
PAIRS_TRADING_MARKET_RESEARCH_LLM_TIMEOUT_SECONDS=120
PAIRS_TRADING_MARKET_RESEARCH_LLM_MAX_CONCURRENCY=1
PAIRS_TRADING_MARKET_RESEARCH_FREE_ENDPOINT_TIMEOUT_CAP_SECONDS=45
PAIRS_TRADING_MARKET_RESEARCH_LLM_FAIL_FAST_AFTER_FAILURES=1
PAIRS_TRADING_MARKET_RESEARCH_ALLOW_REQUEST_MODEL_OVERRIDE=true
```

For a free local development model, install Ollama, pull a small model, and run the backend with the local provider:

```powershell
ollama pull llama3.2:1b
$env:PAIRS_TRADING_MARKET_RESEARCH_LLM_PROVIDER="ollama"
$env:PAIRS_TRADING_MARKET_RESEARCH_LLM_MODEL="llama3.2:1b"
$env:PAIRS_TRADING_MARKET_RESEARCH_OLLAMA_BASE_URL="http://127.0.0.1:11434"
$env:PAIRS_TRADING_MARKET_RESEARCH_LLM_TIMEOUT_SECONDS="120"
$env:PAIRS_TRADING_MARKET_RESEARCH_AGENT_TIMEOUT_SECONDS="120"
$env:PAIRS_TRADING_MARKET_RESEARCH_LLM_MAX_CONCURRENCY="1"
```

Ollama mode uses the local `/api/chat` endpoint with schema/JSON output validation. It is intended for development and tests only; production startup rejects `ollama`. Restart the backend after changing environment variables. Use `GET /api/market-research/runtime` to confirm the active provider/model, timeout settings, data provider, and whether the configured Ollama model is reachable.

Set `PAIRS_TRADING_MARKET_RESEARCH_DATA_PROVIDER=cached_yahoo` to use the existing cached Yahoo price provider for close prices. If that provider fails or returns no usable rows, the workflow falls back to demo data and records a warning in the report.

Real structured LLM generation is server-configured only. Do not send API keys or provider secrets in requests.

NVIDIA Build free endpoints are available for research-stage experiments through the OpenAI-compatible NVIDIA API. They require a server-side NVIDIA API key and are intentionally blocked by production startup because free endpoints are not treated as SLA-backed production infrastructure.

```powershell
$env:PAIRS_TRADING_MARKET_RESEARCH_LLM_PROVIDER="nvidia"
$env:PAIRS_TRADING_MARKET_RESEARCH_LLM_MODEL="mistralai/mistral-large-3-675b-instruct-2512"
$env:PAIRS_TRADING_MARKET_RESEARCH_NVIDIA_API_KEY_REF="env:NVIDIA_API_KEY"
$env:NVIDIA_API_KEY="..."
```

Secret refs such as `env:NVIDIA_API_KEY` first read the backend process environment and then fall back to the repo `.env` file. Only the requested secret key is read from `.env`; the backend does not load unrelated `.env` settings into runtime config.

NVIDIA research endpoints are deliberately fail-fast in this app. Per-call timeout is capped by `PAIRS_TRADING_MARKET_RESEARCH_FREE_ENDPOINT_TIMEOUT_CAP_SECONDS` and, by default, the committee stops making hosted LLM calls after the first provider timeout/failure. Remaining agents continue with deterministic baselines and record `llm_refinement_skipped` trace events. This prevents one slow free endpoint from turning an 8-agent run into a long serial timeout.

The development UI can override the NVIDIA model per market-research job when `PAIRS_TRADING_MARKET_RESEARCH_ALLOW_REQUEST_MODEL_OVERRIDE=true`. The vetted chat-compatible catalog currently includes:

- `mistralai/mistral-large-3-675b-instruct-2512`
- `mistralai/mistral-nemotron`
- `qwen/qwen3-coder-480b-a35b-instruct`
- `stepfun-ai/step-3.5-flash`
- `minimaxai/minimax-m2.7`
- `meta/llama-4-maverick-17b-128e-instruct`
- `microsoft/phi-4-multimodal-instruct`
- `google/gemma-3n-e4b-it`
- `google/gemma-3n-e2b-it`
- `bytedance/seed-oss-36b-instruct`
- `abacusai/dracarys-llama-3.1-70b-instruct`
- `nvidia/nemotron-mini-4b-instruct`

The runtime endpoint also exposes useful non-chat NVIDIA research utilities for future RAG/guardrail work, including `nvidia/rerank-qa-mistral-4b`, `nvidia/nv-embedcode-7b-v1`, `nvidia/gliner-pii`, Llama/Nemotron safety models, and `nvidia/riva-translate-4b-instruct-v1.1`. Those are cataloged separately because they are not drop-in market-research chat models.

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
GET /api/market-research/runtime
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
