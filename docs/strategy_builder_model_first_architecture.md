# Model-first strategy-builder architecture

Status: implemented in `strategy-builder/v3-model-first`  
Scope: natural-language interpretation, safe strategy compilation, semantic repair, and backtest handoff

## Why the previous hybrid path was insufficient

The v2 hosted-provider path called an LLM, but first ran the request through the local regex parser and appended that parser's candidate to the conversation as a preferred seed. This made the parser the practical semantic bottleneck: a request the parser misunderstood could bias the model or trigger unnecessary clarification even when the model understood it.

The v3 path separates responsibilities:

1. A security precheck rejects attempts to access secrets, files, shells, databases, or hidden instructions without making a provider call.
2. A hosted LLM independently interprets the complete bounded conversation against an explicit engine capability manifest.
3. The model returns a requirement trace, a candidate `StrategySpec`, a summary, and qualitative risk analysis through a strict structured-output schema.
4. Pydantic checks the envelope and JSON types.
5. TradePilot's deterministic validator checks rule kinds, timeframes, ticker syntax, exposure, sizing, and risk-control bounds.
6. The real strategy factory runs a synthetic fold as a compiler/runtime smoke test.
7. If steps 5 or 6 reject a hosted-model candidate, the model receives sanitized validator feedback for exactly one semantic repair attempt. It must ask for clarification rather than change user intent.
8. A human must explicitly approve the validated spec before it is stored or backtested.

The deterministic parser remains the implementation for explicit `rules` mode and the compact Ollama seed-review path. It is no longer a hidden seed or completeness gate for hosted providers such as DeepInfra.

## Research findings behind the design

### Structured output is necessary, but not sufficient

[DeepInfra structured outputs](https://docs.deepinfra.com/chat/structured-outputs) support strict JSON Schema constrained responses and recommend production schema validation. The same documentation warns that forcing JSON can increase fabricated values, so schema conformance cannot be treated as factual or semantic correctness. [JSON Schema](https://json-schema.org/understanding-json-schema/basics) validates document structure and constraints; it does not establish that a trading rule means what the user intended.

This is why TradePilot uses two independent layers after generation: Pydantic/schema validation for shape, followed by domain validation and an engine dry run for executable meaning.

### Treat natural-language generation as compilation

[PICARD](https://aclanthology.org/2021.emnlp-main.779/) demonstrates the core compiler principle for model-generated formal languages: reject inadmissible outputs against the target grammar rather than trusting unconstrained text. TradePilot applies the same principle at two points. Provider-side constrained decoding limits the JSON language, while local validation limits the strategy DSL and actual engine behavior.

The model is therefore a semantic planner/compiler front end, not the execution engine. It cannot create Python, arbitrary expressions, data-fetch URLs, or new indicator implementations.

### Validation feedback should be bounded and intent-preserving

Schema-valid output can still contain an unknown rule kind, an invalid window relationship, unsupported exposure, or a spec that the factory cannot run. One repair pass is useful for mechanical compilation errors, but unbounded self-correction would increase latency, cost, and the chance of silently drifting away from the user's request. V3 therefore provides one sanitized feedback pass and requires clarification when a correction would change intent.

### Backtests remain evidence, not model output

The LLM is forbidden from inventing returns, Sharpe ratio, drawdown, win rate, or backtest conclusions. The approved spec runs through the existing chronological walk-forward pipeline. Time-ordered evaluation is important because ordinary random splits can train on future observations; [scikit-learn's TimeSeriesSplit documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html) describes this exact failure mode. Even walk-forward results can be overfit after repeated strategy trials. Bailey et al.'s [Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/Papers.cfm?abstract_id=2326253) motivates reporting robustness and degradation rather than selecting a strategy from a single attractive historical result.

TradePilot consequently keeps provider commentary qualitative and treats computed fold stability, costs, turnover, drawdown, parameter sensitivity, and overfitting diagnostics as authoritative.

## Requirement trace contract

Every material request element must receive one of four dispositions:

- `implemented`: represented directly in the candidate spec;
- `normalized`: conservatively mapped to supported behavior and disclosed;
- `unsupported`: cannot be executed faithfully by the current engine;
- `missing`: required information was not supplied.

A candidate can reach `ready_for_validation` only when it has no `unsupported` or `missing` requirements. The UI exposes the trace so the user can see what the model understood instead of inferring it from the final rule names.

## Current executable boundary

V3 understands broad phrasing, but can only compile semantics supported by `directional_ledger_v1`:

- one to twelve explicit ticker symbols;
- daily or short-term hourly/4-hour bars;
- long-only exposure;
- SMA, EMA, RSI, and MACD allowlisted rule blocks;
- bounded equal-weight sizing, exposure, stops, take-profit fields, and transaction costs;
- next-bar-close execution.

Examples that are understood but rejected as non-executable include short books, options/futures mechanics, minute/tick trading, fundamental/news/alternative-data signals, portfolio optimizers, arbitrary formulas, and broker-specific order behavior. Adding these requires new typed DSL nodes, deterministic implementations, validation, fixtures, and backtest tests; a larger model alone cannot safely add them.

## Provider behavior

Hosted providers use the model-first path. DeepInfra first receives the strict JSON Schema response format; if the selected model does not support it, the provider falls back to JSON-object mode and applies the same local Pydantic schema. Ollama retains the smaller deterministic-seed review because regenerating the full nested schema is unreliable for many local models; the response provenance exposes that path as `deterministic_seed_review`.

The API returns `interpreted_intent`, `generation_path`, and `semantic_repair_count` in addition to the candidate, summary, risk analysis, validation, and signed provenance.

## Qualification criteria for a model

A model should be enabled only after it passes:

1. diverse paraphrases that are outside the regex parser's phrasing;
2. multi-constraint requirement-trace completeness;
3. clarification for genuinely missing requirements;
4. explicit unsupported-feature reporting without invented substitutions;
5. prompt-injection rejection before the provider call;
6. strict-schema and JSON-object fallback validation;
7. semantic repair of a mechanically invalid candidate;
8. approval and a real multi-fold walk-forward backtest with finite metrics and persisted artifacts;
9. cost, execution-delay, stop, and position-sizing fidelity;
10. no fabricated performance claims in summaries or risk analysis.

