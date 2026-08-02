# Strategy-builder providers

The constrained strategy builder supports five structured-output providers:

| Provider | `PAIRS_TRADING_STRATEGY_BUILDER_LLM_PROVIDER` | Credential setting | Example model |
| --- | --- | --- | --- |
| OpenAI | `openai` | `PAIRS_TRADING_STRATEGY_BUILDER_OPENAI_API_KEY_REF` | `gpt-5-mini` |
| Anthropic | `anthropic` | `PAIRS_TRADING_STRATEGY_BUILDER_ANTHROPIC_API_KEY_REF` | `claude-sonnet-4-5` |
| DeepInfra | `deepinfra` | `PAIRS_TRADING_STRATEGY_BUILDER_DEEPINFRA_API_KEY_REF` | `deepseek-ai/DeepSeek-V4-Flash` |
| NVIDIA Build/NIM | `nvidia` | `PAIRS_TRADING_STRATEGY_BUILDER_NVIDIA_API_KEY_REF` | `mistralai/mistral-large-3-675b-instruct-2512` |
| Local Ollama | `ollama` | none | any pulled instruction model with reliable JSON output |

Enable model-assisted generation and choose exactly one server-side provider:

```text
PAIRS_TRADING_STRATEGY_BUILDER_MODE=llm
PAIRS_TRADING_STRATEGY_BUILDER_LLM_PROVIDER=anthropic
PAIRS_TRADING_STRATEGY_BUILDER_LLM_MODEL=claude-sonnet-4-5
PAIRS_TRADING_STRATEGY_BUILDER_ANTHROPIC_API_KEY_REF=env:ANTHROPIC_API_KEY
```

For NVIDIA, set `PAIRS_TRADING_STRATEGY_BUILDER_NVIDIA_BASE_URL` when using a compatible private NIM endpoint. The public NVIDIA Build catalog is intended for evaluation and requires a model ID from the repository's vetted catalog.

For DeepInfra, set `PAIRS_TRADING_STRATEGY_BUILDER_DEEPINFRA_API_KEY_REF` to a server-side token reference. The default `PAIRS_TRADING_STRATEGY_BUILDER_DEEPINFRA_BASE_URL` is `https://api.deepinfra.com/v1/openai`, as specified by the [DeepInfra OpenAI-compatible API](https://docs.deepinfra.com/chat/overview). TradePilot first requests [DeepInfra's strict JSON Schema response format](https://docs.deepinfra.com/chat/structured-outputs) and falls back to JSON-object mode plus the same local Pydantic validation when a selected model does not support strict schema output.

For Ollama, set `PAIRS_TRADING_STRATEGY_BUILDER_OLLAMA_BASE_URL`, pull the configured model before starting TradePilot, and use a timeout suitable for local inference. Ollama does not require an API key. For a complete prompt, the local model reviews and explicitly accepts or rejects TradePilot's deterministic allowlisted candidate through a one-boolean structured schema; it does not need to regenerate the large nested spec. Incomplete prompts still return clarification questions. The live verification command uses `TEST_OLLAMA_MODEL` so each candidate model can be qualified before rollout.

API keys can use `env:NAME` (including the existing `NAME_FILE` mounted-secret behavior) or `secret-manager:aws:<secret-id>#<json-key>`. Credentials remain server-side.

Hosted providers use the [model-first semantic compiler architecture](strategy_builder_model_first_architecture.md): the model independently interprets every material prompt requirement, returns an auditable disposition trace, and compiles a candidate `StrategySpec`. It is no longer guided by a hidden regex-generated candidate. TradePilot then applies deterministic allowlist validation, a synthetic dry run, one bounded semantic repair when necessary, explicit approval, and the normal walk-forward backtest pipeline. Provider output never executes as code.

Hosted providers must also return a bounded pre-backtest analysis containing a concise summary, qualitative overall risk, key risks, mitigations, and walk-forward validation priorities. The prompt explicitly forbids invented performance metrics. These statements are hypotheses for review; computed backtest results remain the authoritative evidence.
