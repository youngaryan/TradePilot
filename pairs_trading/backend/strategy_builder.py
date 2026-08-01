from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, ValidationError

from ..core.timeframes import TradingMode, normalize_trading_mode
from ..platform import build_metadata_store
from ..strategies import build_rule_based_strategy_factory
from .config import BackendSettings
from .observability import METRICS


CUSTOM_PIPELINE_PREFIX = "user_strategy:"
RULES_PROMPT_VERSION = "strategy-builder-rules/v1"
SUPPORTED_TIMEFRAMES = {"1d", "daily", "1h", "4h", "hourly", "intraday", "short_term", "short-term"}
SUPPORTED_RULE_KINDS = {
    "price_above_sma",
    "price_below_sma",
    "price_above_ema",
    "price_below_ema",
    "sma_cross_above",
    "sma_cross_below",
    "ema_cross_above",
    "ema_cross_below",
    "rsi_below",
    "rsi_above",
    "macd_above_signal",
    "macd_below_signal",
}
PROMPT_INJECTION_PATTERNS = (
    r"ignore\s+(all\s+)?previous\s+instructions?",
    r"system\s+(prompt|override|instructions?)",
    r"developer\s+mode",
    r"reveal\s+(the\s+)?prompt",
    r"bypass\s+(security|permissions|authorization|auth)",
    r"(read|write|delete)\s+(files?|database|secrets?)",
    r"(os\.|subprocess|shell|powershell|cmd\.exe|rm\s+-rf)",
    r"(api[_ -]?key|password|secret|token)",
)
SYMBOL_STOPWORDS = {
    "AND",
    "A",
    "ARE",
    "IS",
    "IN",
    "BUY",
    "SELL",
    "SHORT",
    "LONG",
    "WHEN",
    "THEN",
    "WITH",
    "FROM",
    "TRADE",
    "TRADES",
    "USE",
    "USES",
    "ON",
    "BARS",
    "BAR",
    "ABOVE",
    "BELOW",
    "DAILY",
    "DAY",
    "WEEK",
    "MONTH",
    "SMA",
    "EMA",
    "RSI",
    "MACD",
    "ENTRY",
    "EXIT",
    "STOP",
    "LOSS",
    "TAKE",
    "PROFIT",
    "PRICE",
    "CROSS",
    "CROSSES",
    "EQUAL",
    "WEIGHT",
    "SIZE",
    "COST",
    "COSTS",
    "BPS",
}


class StrategyRule(BaseModel):
    kind: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None


class StrategyIndicator(BaseModel):
    name: str
    kind: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class StrategyParameter(BaseModel):
    name: str
    default: Any
    min: float | None = None
    max: float | None = None
    description: str


class StrategySpecModel(BaseModel):
    schema_version: Literal["strategy_spec/v1"] = "strategy_spec/v1"
    name: str = Field(min_length=3, max_length=120)
    summary: str = Field(min_length=12, max_length=1000)
    asset_universe: dict[str, Any]
    timeframe: str
    side: Literal["long_only", "short_only", "long_short"] = "long_only"
    required_indicators: list[StrategyIndicator] = Field(default_factory=list)
    entry_rules: list[StrategyRule] = Field(default_factory=list)
    exit_rules: list[StrategyRule] = Field(default_factory=list)
    position_sizing: dict[str, Any]
    risk_controls: dict[str, Any] = Field(default_factory=dict)
    rebalancing: dict[str, Any] = Field(default_factory=dict)
    costs: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    editable_parameters: list[StrategyParameter] = Field(default_factory=list)
    compatibility: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class SpecValidation:
    ok: bool
    errors: list[str]
    warnings: list[str]
    spec: dict[str, Any] | None = None


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def custom_strategy_pipeline(strategy_id: str) -> str:
    return f"{CUSTOM_PIPELINE_PREFIX}{strategy_id}"


def parse_custom_strategy_pipeline(pipeline: str) -> str | None:
    if not pipeline.startswith(CUSTOM_PIPELINE_PREFIX):
        return None
    strategy_id = pipeline[len(CUSTOM_PIPELINE_PREFIX):].strip()
    return strategy_id or None


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _detect_prompt_injection(text: str) -> bool:
    normalized = _normalize_text(text).casefold()
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in PROMPT_INJECTION_PATTERNS)


def _extract_symbols(text: str) -> list[str]:
    candidates = re.findall(r"\b[A-Za-z]{1,5}\b", text.upper())
    symbols: list[str] = []
    for candidate in candidates:
        if candidate in SYMBOL_STOPWORDS:
            continue
        if candidate not in symbols:
            symbols.append(candidate)
    return symbols[:12]


def _extract_timeframe(text: str) -> str | None:
    lowered = text.casefold()
    if any(token in lowered for token in ("1d", "daily", "day bars", "daily bars", "end of day")):
        return "1d"
    if any(token in lowered for token in ("hour", "intraday", "1h", "4h", "four-hour", "short-term", "short term")):
        return "short_term"
    if any(token in lowered for token in ("minute", "tick", "5m", "15m")):
        return "unsupported_intraday"
    return None


def _extract_percent(text: str, default: float | None = None) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if not match:
        return default
    return float(match.group(1)) / 100.0


def _first_int_near(text: str, keyword: str, default: int) -> int:
    patterns = (
        rf"{keyword}\s*(\d+)",
        rf"(\d+)\s*(?:day|bar|period)?\s*{keyword}",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return max(1, int(match.group(1)))
    return default


def _ma_windows(text: str, default_fast: int, default_slow: int) -> tuple[int, int]:
    numbers = [int(value) for value in re.findall(r"\b(\d{1,3})\b", text)]
    if len(numbers) >= 2:
        fast, slow = sorted(numbers[:2])
        if fast < slow:
            return fast, slow
    return default_fast, default_slow


def _build_rule_spec(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    lowered = text.casefold()
    indicators: list[dict[str, Any]] = []
    entry_rules: list[dict[str, Any]] = []
    exit_rules: list[dict[str, Any]] = []
    params: list[dict[str, Any]] = []

    if "rsi" in lowered or "oversold" in lowered or "overbought" in lowered:
        window = _first_int_near(text, "rsi", 14)
        lower = 30.0
        upper = 70.0
        below_match = re.search(r"(?:below|under|oversold)\D{0,12}(\d{1,2}(?:\.\d+)?)", text, re.IGNORECASE)
        above_match = re.search(r"(?:above|over|overbought)\D{0,12}(\d{1,2}(?:\.\d+)?)", text, re.IGNORECASE)
        if below_match:
            lower = float(below_match.group(1))
        if above_match:
            upper = float(above_match.group(1))
        if not below_match and not above_match:
            threshold_matches = [float(value) for value in re.findall(r"\b(\d{2}(?:\.\d+)?)\b", text)]
            thresholds = [value for value in threshold_matches if 1 <= value <= 99 and value != float(window)]
            if len(thresholds) >= 2:
                lower, upper = min(thresholds[:2]), max(thresholds[:2])
        indicators.append({"name": f"RSI {window}", "kind": "rsi", "parameters": {"window": window}})
        entry_rules.append({"kind": "rsi_below", "parameters": {"window": window, "threshold": lower}, "description": f"Enter when RSI is at or below {lower:g}."})
        exit_rules.append({"kind": "rsi_above", "parameters": {"window": window, "threshold": upper}, "description": f"Exit when RSI is at or above {upper:g}."})
        params.extend(
            [
                {"name": "rsi_window", "default": window, "min": 2, "max": 100, "description": "RSI lookback window in bars."},
                {"name": "rsi_entry_threshold", "default": lower, "min": 1, "max": 50, "description": "Oversold entry threshold."},
                {"name": "rsi_exit_threshold", "default": upper, "min": 50, "max": 99, "description": "Exit threshold."},
            ]
        )
        return indicators, entry_rules, exit_rules, params

    if "macd" in lowered:
        indicators.append({"name": "MACD", "kind": "macd", "parameters": {"fast_window": 12, "slow_window": 26, "signal_window": 9}})
        entry_rules.append({"kind": "macd_above_signal", "parameters": {"fast_window": 12, "slow_window": 26, "signal_window": 9}, "description": "Enter when MACD is above the signal line."})
        exit_rules.append({"kind": "macd_below_signal", "parameters": {"fast_window": 12, "slow_window": 26, "signal_window": 9}, "description": "Exit when MACD is below the signal line."})
        params.extend(
            [
                {"name": "macd_fast_window", "default": 12, "min": 2, "max": 100, "description": "Fast EMA window."},
                {"name": "macd_slow_window", "default": 26, "min": 3, "max": 200, "description": "Slow EMA window."},
                {"name": "macd_signal_window", "default": 9, "min": 2, "max": 100, "description": "Signal-line EMA window."},
            ]
        )
        return indicators, entry_rules, exit_rules, params

    if any(token in lowered for token in ("moving average", "sma", "ema", "golden cross", "death cross", "ma cross")):
        uses_ema = "ema" in lowered or "exponential" in lowered
        fast, slow = _ma_windows(text, 50, 200)
        indicator_kind = "ema" if uses_ema else "sma"
        entry_kind = "ema_cross_above" if uses_ema else "sma_cross_above"
        exit_kind = "ema_cross_below" if uses_ema else "sma_cross_below"
        indicators.extend(
            [
                {"name": f"{indicator_kind.upper()} {fast}", "kind": indicator_kind, "parameters": {"window": fast}},
                {"name": f"{indicator_kind.upper()} {slow}", "kind": indicator_kind, "parameters": {"window": slow}},
            ]
        )
        entry_rules.append({"kind": entry_kind, "parameters": {"fast_window": fast, "slow_window": slow}, "description": f"Enter when the {fast}-bar {indicator_kind.upper()} crosses above the {slow}-bar {indicator_kind.upper()}."})
        exit_rules.append({"kind": exit_kind, "parameters": {"fast_window": fast, "slow_window": slow}, "description": f"Exit when the {fast}-bar {indicator_kind.upper()} crosses below the {slow}-bar {indicator_kind.upper()}."})
        params.extend(
            [
                {"name": "fast_window", "default": fast, "min": 2, "max": 250, "description": "Fast moving-average lookback."},
                {"name": "slow_window", "default": slow, "min": 3, "max": 500, "description": "Slow moving-average lookback."},
            ]
        )
        return indicators, entry_rules, exit_rules, params

    return indicators, entry_rules, exit_rules, params


def _infer_name(text: str, indicators: list[dict[str, Any]]) -> str:
    if indicators:
        main = str(indicators[0].get("kind") or "Rule").upper()
        if main == "SMA":
            return "Moving Average User Strategy"
        if main == "EMA":
            return "EMA User Strategy"
        return f"{main} User Strategy"
    words = re.findall(r"[A-Za-z0-9]+", text)
    return " ".join(words[:6]).title()[:80] or "User Strategy"


def _build_draft_from_text(text: str) -> tuple[dict[str, Any] | None, list[str], str]:
    if _detect_prompt_injection(text):
        return None, ["Remove instructions that request system access, permission bypasses, secrets, files, shell commands, or database access."], "rejected"
    if re.search(r"\b(short\s+(?:only|positions?|selling|sell|book)|long\s*/\s*short|long-short)\b", text, re.IGNORECASE):
        return None, ["The safe strategy builder currently supports long-only rule specs. Short and long/short strategies need a separate borrow, margin, and short-rule review path."], "rejected"

    symbols = _extract_symbols(text)
    timeframe = _extract_timeframe(text)
    if timeframe == "unsupported_intraday":
        return None, ["The safe builder supports daily bars and short-term hourly/4-hour bars, but not minute or tick strategies."], "rejected"

    indicators, entry_rules, exit_rules, editable_params = _build_rule_spec(text)
    max_position = _extract_percent(text)
    lowered = text.casefold()
    if "equal weight" in lowered and symbols:
        max_position = min(1.0, 1.0 / len(symbols))
    stop_loss = None
    stop_match = re.search(r"stop(?:\s+loss)?\s*(?:at|of)?\s*(\d+(?:\.\d+)?)\s*%", text, re.IGNORECASE)
    if not stop_match:
        stop_match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*stop(?:\s+loss)?", text, re.IGNORECASE)
    if stop_match:
        stop_loss = float(stop_match.group(1)) / 100.0
    cost_match = re.search(r"(\d+(?:\.\d+)?)\s*bps", text, re.IGNORECASE)
    cost_bps = float(cost_match.group(1)) if cost_match else None

    questions: list[str] = []
    if not symbols:
        questions.append("What exact asset universe should this trade? Provide ticker symbols such as SPY QQQ TLT.")
    if not timeframe:
        questions.append("What timeframe should be used: daily or short-term hourly/4-hour bars?")
    if not entry_rules:
        questions.append("What exact entry condition should trigger a position? Supported examples include RSI thresholds, SMA/EMA crossovers, price-vs-moving-average, and MACD signal-line rules.")
    if not exit_rules:
        questions.append("What exact exit condition closes the position?")
    if max_position is None:
        questions.append("How should positions be sized? Specify equal weight or a maximum percent per symbol.")
    if stop_loss is None and "no stop" not in lowered:
        questions.append("Should there be a stop loss or should the strategy explicitly run without a hard stop?")
    if cost_bps is None:
        questions.append("What transaction cost assumption should be used in basis points?")
    if questions:
        return None, questions, "needs_clarification"

    side = "long_only"
    name = _infer_name(text, indicators)
    max_positions = min(len(symbols), max(1, int(round(1.0 / max(max_position or 1.0, 0.01)))))
    cost_component = max((cost_bps or 2.0) / 3.0, 0.0)
    spec = {
        "schema_version": "strategy_spec/v1",
        "name": name,
        "summary": f"{name} on {', '.join(symbols)} using {', '.join(item['name'] for item in indicators) or 'approved technical rules'}.",
        "asset_universe": {"type": "explicit_symbols", "symbols": symbols},
        "timeframe": timeframe,
        "side": side,
        "required_indicators": indicators,
        "entry_rules": entry_rules,
        "exit_rules": exit_rules,
        "position_sizing": {
            "method": "equal_weight",
            "max_position_per_symbol": round(float(max_position or min(1.0, 1.0 / len(symbols))), 6),
            "max_gross_exposure": min(1.0, round(float(max_position or 0.0) * len(symbols), 6) if max_position else 1.0),
        },
        "risk_controls": {
            "stop_loss_pct": stop_loss,
            "take_profit_pct": None,
            "max_positions": max_positions,
        },
        "rebalancing": {
            "frequency": "intraday" if timeframe == "short_term" else "daily",
            "execution_timing": "next_bar_close",
        },
        "costs": {
            "commission_bps": round(cost_component, 4),
            "spread_bps": round(cost_component, 4),
            "slippage_bps": round(cost_component, 4),
            "market_impact_bps": 0.5,
            "delay_bars": 1,
        },
        "assumptions": [
            "Signals are computed from historical daily close data." if timeframe == "1d" else "Signals use hourly execution bars with 4-hour confirmation in short-term mode.",
            "Execution uses the existing backtest engine's next-bar delayed close-to-close execution model.",
            "No arbitrary user code is generated or executed.",
        ],
        "limitations": [
            "The safe builder currently supports a constrained set of technical-indicator rule blocks.",
            "Minute bars, tick data, custom external data, and discretionary text rules are not supported in this builder.",
            "Backtest performance does not guarantee future results.",
        ],
        "editable_parameters": editable_params,
        "compatibility": {
            "engine": "directional_ledger_v1",
            "supported": True,
            "notes": ["Compatible with the directional walk-forward backtest path and ledger visualization outputs."],
        },
    }
    return spec, [], "ready_for_approval"


def validate_strategy_spec(spec: dict[str, Any]) -> SpecValidation:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        parsed = StrategySpecModel.model_validate(spec)
    except ValidationError as exc:
        return SpecValidation(ok=False, errors=[error["msg"] for error in exc.errors()], warnings=warnings)

    normalized = parsed.model_dump(mode="json")
    timeframe = str(normalized.get("timeframe") or "").casefold()
    if timeframe not in SUPPORTED_TIMEFRAMES:
        errors.append("Only daily and short-term hourly/4-hour timeframe specs are currently supported.")
    else:
        try:
            mode = normalize_trading_mode(timeframe)
        except ValueError:
            errors.append("Only daily and short-term hourly/4-hour timeframe specs are currently supported.")
        else:
            normalized["timeframe"] = "short_term" if mode == TradingMode.SHORT_TERM else "1d"
    symbols = normalized.get("asset_universe", {}).get("symbols")
    if not isinstance(symbols, list) or not symbols:
        errors.append("asset_universe.symbols must contain at least one ticker.")
    else:
        cleaned_symbols = []
        for symbol in symbols:
            text = str(symbol).upper().strip()
            if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", text):
                errors.append(f"Invalid ticker symbol: {symbol}")
            elif text not in cleaned_symbols:
                cleaned_symbols.append(text)
        normalized["asset_universe"]["symbols"] = cleaned_symbols[:12]
        if len(cleaned_symbols) > 12:
            warnings.append("Only the first 12 symbols are used to keep generated strategies reviewable.")

    if not normalized.get("entry_rules"):
        errors.append("At least one entry rule is required.")
    if not normalized.get("exit_rules"):
        errors.append("At least one exit rule is required.")
    for rule in list(normalized.get("entry_rules") or []) + list(normalized.get("exit_rules") or []):
        kind = str(rule.get("kind") or "")
        if kind not in SUPPORTED_RULE_KINDS:
            errors.append(f"Unsupported rule kind: {kind}")
        params = rule.get("parameters") if isinstance(rule.get("parameters"), dict) else {}
        for key in ("window", "fast_window", "slow_window", "signal_window"):
            if key in params and int(params[key]) <= 1:
                errors.append(f"{kind}.{key} must be greater than 1.")
        if "fast_window" in params and "slow_window" in params and int(params["fast_window"]) >= int(params["slow_window"]):
            errors.append(f"{kind}.fast_window must be smaller than slow_window.")

    sizing = normalized.get("position_sizing") if isinstance(normalized.get("position_sizing"), dict) else {}
    max_position = float(sizing.get("max_position_per_symbol", 0.0) or 0.0)
    max_gross = float(sizing.get("max_gross_exposure", 0.0) or 0.0)
    if max_position <= 0 or max_position > 1.0:
        errors.append("position_sizing.max_position_per_symbol must be in (0, 1].")
    if max_gross <= 0 or max_gross > 1.5:
        errors.append("position_sizing.max_gross_exposure must be in (0, 1.5].")
    if normalized.get("side") != "long_only":
        errors.append("The strategy-builder DSL currently supports long-only generated strategies only.")
    risk = normalized.get("risk_controls") if isinstance(normalized.get("risk_controls"), dict) else {}
    stop_loss = risk.get("stop_loss_pct")
    if stop_loss is not None and not (0 < float(stop_loss) <= 0.50):
        errors.append("risk_controls.stop_loss_pct must be null or between 0 and 50%.")
    if stop_loss is None:
        warnings.append("No hard stop loss is configured; exits rely on rule logic.")

    return SpecValidation(ok=not errors, errors=errors, warnings=warnings, spec=normalized if not errors else None)


def dry_run_strategy_spec(spec: dict[str, Any]) -> dict[str, Any]:
    factory, _ = build_rule_based_strategy_factory(spec)
    index = pd.bdate_range("2024-01-01", periods=180)
    path = pd.Series(100.0 + np.sin(np.arange(len(index)) / 8.0) * 4.0 + np.arange(len(index)) * 0.05, index=index)
    train = pd.DataFrame({"SYN": path.iloc[:120]})
    test = pd.DataFrame({"SYN": path.iloc[120:]})
    output = factory("SYN").run_fold(train, test).validate(extra_columns=("unit_return", "gross_return"))
    frame = output.frame
    if frame.empty:
        raise ValueError("Synthetic dry-run produced no output rows.")
    required = {"signal", "position", "gross_return"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Synthetic dry-run missing output columns: {', '.join(sorted(missing))}")
    return {
        "status": "passed",
        "rows": int(len(frame)),
        "nonzero_positions": int((frame["position"].abs() > 0).sum()),
        "checked_at_utc": utc_now_iso(),
    }


def risk_level_for_spec(spec: dict[str, Any]) -> str:
    sizing = spec.get("position_sizing") if isinstance(spec.get("position_sizing"), dict) else {}
    risk = spec.get("risk_controls") if isinstance(spec.get("risk_controls"), dict) else {}
    side = str(spec.get("side") or "long_only")
    max_gross = float(sizing.get("max_gross_exposure", 1.0) or 1.0)
    if side != "long_only" or max_gross > 1.0:
        return "high"
    if risk.get("stop_loss_pct") is None:
        return "medium"
    return "low"


class StrategyBuilderService:
    def __init__(self, settings: BackendSettings, *, generation_service: Any | None = None) -> None:
        self.settings = settings
        self.store = build_metadata_store(settings)
        self.generation_service = generation_service
        if self.settings.strategy_builder_mode == "llm" and self.generation_service is None:
            from .llm_config import build_strategy_builder_llm_provider
            from .strategy_builder_generation import StrategyBuilderGenerationService

            self.generation_service = StrategyBuilderGenerationService(
                build_strategy_builder_llm_provider(settings)
            )

    @staticmethod
    def _content_audit(messages: list[dict[str, str]]) -> dict[str, Any]:
        bounded = messages[-20:]
        digest = hashlib.sha256(
            json.dumps(bounded, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {
            "message_count": len(messages),
            "roles": [str(message.get("role") or "")[:20] for message in bounded],
            "character_counts": [min(len(str(message.get("content") or "")), 5_000) for message in bounded],
            "content_sha256": digest,
        }

    def _sign_provenance(self, spec: dict[str, Any] | None, provenance: dict[str, Any]) -> str | None:
        if spec is None:
            return None
        payload = {
            "spec_sha256": hashlib.sha256(
                json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "provenance": provenance,
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self.settings.csrf_secret.encode("utf-8"), body, hashlib.sha256).digest()
        return (
            base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
            + "."
            + base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        )

    def _verified_provenance(self, spec: dict[str, Any], token: str | None) -> dict[str, Any]:
        fallback = {
            "mode": "rules",
            "provider": "deterministic",
            "model": None,
            "prompt_version": RULES_PROMPT_VERSION,
            "latency_ms": None,
            "usage": {},
        }
        if not token or "." not in token:
            return fallback
        try:
            body_part, signature_part = token.split(".", 1)
            body = base64.urlsafe_b64decode(body_part + "=" * (-len(body_part) % 4))
            signature = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
            expected = hmac.new(self.settings.csrf_secret.encode("utf-8"), body, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                return fallback
            payload = json.loads(body)
            spec_hash = hashlib.sha256(
                json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if payload.get("spec_sha256") != spec_hash or not isinstance(payload.get("provenance"), dict):
                return fallback
            provenance = payload["provenance"]
            raw_usage = provenance.get("usage", {})
            return {
                "mode": "llm" if provenance.get("mode") == "llm" else "rules",
                "provider": str(provenance.get("provider") or "deterministic")[:80],
                "model": str(provenance.get("model"))[:160] if provenance.get("model") else None,
                "prompt_version": str(provenance.get("prompt_version") or RULES_PROMPT_VERSION)[:120],
                "latency_ms": max(0, int(provenance["latency_ms"])) if isinstance(provenance.get("latency_ms"), int) else None,
                "usage": {
                    str(key)[:40]: int(value)
                    for key, value in raw_usage.items()
                    if isinstance(key, str)
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                    and 0 <= value <= 100_000_000
                } if isinstance(raw_usage, dict) else {},
            }
        except (ValueError, TypeError, json.JSONDecodeError):
            return fallback

    def chat(self, *, organization_id: str, user_id: str, messages: list[dict[str, str]], draft_spec: dict[str, Any] | None = None) -> dict[str, Any]:
        started_at = time.monotonic()
        user_text = "\n".join(str(message.get("content") or "") for message in messages if message.get("role") == "user")
        precheck_spec, precheck_questions, precheck_state = _build_draft_from_text(user_text)
        generation_mode = self.settings.strategy_builder_mode
        provider = "deterministic"
        model: str | None = None
        prompt_version = RULES_PROMPT_VERSION
        latency_ms: int | None = 0
        usage: dict[str, int] = {}
        retry_count = 0
        provider_outcome = "not_called"

        if precheck_state == "rejected":
            spec, questions, state = None, precheck_questions, "rejected"
            validation = SpecValidation(ok=False, errors=questions, warnings=[])
        elif generation_mode == "llm":
            from .strategy_builder_generation import PROMPT_VERSION

            provider = self.settings.strategy_builder_llm_provider
            model = self.settings.strategy_builder_llm_model
            prompt_version = PROMPT_VERSION
            provider_messages = list(messages)
            candidate_seed = draft_spec or precheck_spec
            if candidate_seed:
                provider_messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            "Deterministically parsed safe candidate seed. Preserve it when it matches the user's intent; "
                            "return it as candidate_spec with state ready_for_validation unless a concrete requirement is missing: "
                            + json.dumps(candidate_seed, separators=(",", ":"))
                        ),
                    }
                )
            try:
                result = self.generation_service.generate(provider_messages, candidate_seed=candidate_seed)
                provider_outcome = "success"
                generated = result.value
                provider = result.provider
                model = result.model
                latency_ms = max(0, int(result.latency_ms))
                usage = {
                    str(key)[:40]: int(value)
                    for key, value in result.usage.items()
                    if isinstance(key, str)
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                    and 0 <= value <= 100_000_000
                }
                raw_attempt = result.metadata.get("attempt", 1) if isinstance(result.metadata, dict) else 1
                retry_count = max(0, min(int(raw_attempt) - 1, 20)) if isinstance(raw_attempt, int) else 0
                questions = list(generated.clarification_questions)
                spec = generated.candidate_spec.model_dump(mode="json") if generated.candidate_spec else None
                state = generated.state
                validation = validate_strategy_spec(spec) if spec else SpecValidation(ok=False, errors=questions, warnings=[])
                if state == "ready_for_validation":
                    if not validation.ok or validation.spec is None:
                        questions = validation.errors or ["The generated candidate did not pass deterministic validation."]
                        state = "needs_clarification"
                        spec = None
                    else:
                        try:
                            dry_run_strategy_spec(validation.spec)
                        except Exception:
                            questions = ["The candidate could not pass the safe synthetic dry run. Please revise the rules."]
                            state = "needs_clarification"
                            spec = None
                        else:
                            state = "ready_for_approval"
                            spec = validation.spec
                elif state != "rejected":
                    state = "needs_clarification"
                    spec = None
            except Exception:
                provider_outcome = "error"
                spec = None
                questions = ["The strategy generation provider is temporarily unavailable. Please try again later."]
                state = "needs_clarification"
                validation = SpecValidation(ok=False, errors=[], warnings=["Hosted strategy generation was unavailable."])
                latency_ms = None
        elif draft_spec:
            validation = validate_strategy_spec(draft_spec)
            state = "ready_for_approval" if validation.ok else "needs_clarification"
            questions = validation.errors
            spec = validation.spec
        else:
            spec, questions, state = _build_draft_from_text(user_text)
            validation = validate_strategy_spec(spec) if spec else SpecValidation(ok=False, errors=questions, warnings=[])
            if spec and not validation.ok:
                questions = validation.errors
                state = "needs_clarification"
                spec = None

        if state == "rejected":
            assistant_message = "I cannot safely implement that request. Keep the strategy description limited to market data, indicators, sizing, risk controls, and execution assumptions."
        elif state == "ready_for_approval":
            assistant_message = "The strategy is precise enough to review. Read the structured specification, then approve it explicitly if it matches your intent."
        else:
            assistant_message = "I need a few more specifics before this can become an implementable strategy."

        if generation_mode == "llm":
            metric_provider = provider if provider in {"openai", "anthropic", "nvidia", "ollama"} else "other"
            metric_model = re.sub(r"[^A-Za-z0-9._:-]", "_", str(model or "unknown"))[:80] or "unknown"
            labels = {"provider": metric_provider, "model": metric_model, "outcome": provider_outcome}
            METRICS.inc("tradepilot_strategy_builder_provider_calls_total", labels)
            METRICS.observe("tradepilot_strategy_builder_provider_duration_seconds", labels, max(0.0, time.monotonic() - started_at))
            METRICS.observe("tradepilot_strategy_builder_provider_retries", {"provider": metric_provider}, retry_count)
            METRICS.inc(
                "tradepilot_strategy_builder_validation_total",
                {"result": "passed" if validation.ok and spec else "rejected"},
            )

        self.store.record_audit_log(
            action="strategy_builder.chat",
            organization_id=organization_id,
            actor_user_id=user_id,
            target_type="strategy_builder",
            metadata={
                "state": state,
                "question_count": len(questions),
                **self._content_audit(messages),
                "generation_mode": generation_mode,
                "provider": provider,
                "model": model,
                "prompt_version": prompt_version,
                "validation_outcome": "passed" if validation.ok and spec else "not_ready",
                "retry_count": retry_count,
                "candidate_spec": spec,
            },
        )
        provenance = {
            "mode": generation_mode,
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            "latency_ms": latency_ms,
            "usage": usage,
        }
        return {
            "state": state,
            "assistant_message": assistant_message,
            "questions": questions,
            "draft_spec": spec,
            "validation": {
                "ok": validation.ok if spec else False,
                "errors": validation.errors,
                "warnings": validation.warnings,
            },
            "generation_mode": generation_mode,
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            "provenance_token": self._sign_provenance(spec, provenance),
        }

    def approve(
        self,
        *,
        organization_id: str,
        user_id: str,
        spec: dict[str, Any],
        approval_text: str,
        provenance_token: str | None = None,
    ) -> dict[str, Any]:
        validation = validate_strategy_spec(spec)
        if not validation.ok or validation.spec is None:
            raise ValueError("; ".join(validation.errors) or "Strategy spec is invalid.")
        dry_run = dry_run_strategy_spec(validation.spec)
        risk_level = risk_level_for_spec(validation.spec)
        generation = self._verified_provenance(validation.spec, provenance_token)
        record = self.store.create_user_strategy(
            organization_id=organization_id,
            owner_user_id=user_id,
            spec=validation.spec,
            validation={"warnings": validation.warnings, "dry_run": dry_run},
            approval={
                "approval_text": approval_text,
                "approved_at_utc": utc_now_iso(),
                "source": "strategy_builder",
                "generation": generation,
            },
            status="active",
            risk_level=risk_level,
        )
        self.store.record_audit_log(
            action="strategy_builder.approved",
            organization_id=organization_id,
            actor_user_id=user_id,
            target_type="user_strategy",
            target_id=record["id"],
            metadata={
                "strategy_name": record["name"],
                "risk_level": risk_level,
                "version": record["version"],
                "spec": validation.spec,
                "generation": generation,
            },
        )
        return {"strategy": record, "catalog_item": self.catalog_item(record), "validation": {"warnings": validation.warnings, "dry_run": dry_run}}

    def allowed_catalog(self, *, organization_id: str, user_id: str, base_catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
        strategies = self.store.list_user_strategies(organization_id=organization_id, owner_user_id=user_id, active_only=True)
        return [*base_catalog, *[self.catalog_item(record) for record in strategies]]

    def user_strategies(self, *, organization_id: str, user_id: str) -> list[dict[str, Any]]:
        return self.store.list_user_strategies(organization_id=organization_id, owner_user_id=user_id, active_only=False)

    def get_for_backtest(self, *, organization_id: str, user_id: str | None, pipeline: str) -> dict[str, Any] | None:
        strategy_id = parse_custom_strategy_pipeline(pipeline)
        if not strategy_id or not user_id:
            return None
        return self.store.get_user_strategy(
            organization_id=organization_id,
            strategy_id=strategy_id,
            owner_user_id=user_id,
            active_only=True,
        )

    def admin_list(self, *, organization_id: str | None = None, user_id: str | None = None, status: str | None = None, risk_level: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.list_admin_user_strategies(
            organization_id=organization_id,
            owner_user_id=user_id,
            status=status,
            risk_level=risk_level,
            limit=limit,
        )

    def admin_update_status(self, *, strategy_id: str, status: str, actor_user_id: str) -> dict[str, Any]:
        if status not in {"active", "disabled"}:
            raise ValueError("Status must be active or disabled.")
        updated = self.store.update_user_strategy_status(strategy_id=strategy_id, status=status)
        if updated is None:
            raise KeyError(f"Strategy not found: {strategy_id}")
        self.store.record_audit_log(
            action="admin.user_strategy_status_updated",
            organization_id=updated.get("organization_id"),
            actor_user_id=actor_user_id,
            target_type="user_strategy",
            target_id=strategy_id,
            metadata={"status": status},
        )
        return updated

    def admin_delete(self, *, strategy_id: str, actor_user_id: str) -> dict[str, Any]:
        updated = self.store.update_user_strategy_status(strategy_id=strategy_id, status="deleted")
        if updated is None:
            raise KeyError(f"Strategy not found: {strategy_id}")
        self.store.record_audit_log(
            action="admin.user_strategy_deleted",
            organization_id=updated.get("organization_id"),
            actor_user_id=actor_user_id,
            target_type="user_strategy",
            target_id=strategy_id,
            metadata={"status": "deleted"},
        )
        return updated

    @staticmethod
    def catalog_item(record: dict[str, Any]) -> dict[str, Any]:
        spec = record.get("spec") if isinstance(record.get("spec"), dict) else {}
        symbols = spec.get("asset_universe", {}).get("symbols", []) if isinstance(spec.get("asset_universe"), dict) else []
        params = {item.get("name"): item.get("default") for item in spec.get("editable_parameters", []) if isinstance(item, dict) and item.get("name")}
        approval = record.get("approval") if isinstance(record.get("approval"), dict) else {}
        generation = approval.get("generation") if isinstance(approval.get("generation"), dict) else {}
        mode = "llm" if generation.get("mode") == "llm" else "rules"
        generation_label = "AI-assisted" if mode == "llm" else "Rule-generated"
        return {
            "id": record["id"],
            "name": record["name"],
            "family": "User-created",
            "difficulty": f"{generation_label} / {record.get('risk_level', 'medium')} risk",
            "pipeline": custom_strategy_pipeline(record["id"]),
            "summary": spec.get("summary") or "User-approved strategy generated from a validated rule specification.",
            "how_it_works": "Interprets the approved StrategySpec through allowlisted technical-indicator rule blocks. No generated code is executed.",
            "best_for": "Owner-only backtesting after reviewing the generated specification.",
            "watch_out": f"{generation_label} strategies are user-approved research hypotheses, not guaranteed profitable trading systems.",
            "key_parameters": tuple(params.keys()),
            "example_cli": "Available from the authenticated web backtesting UI only.",
            "paper_config_example": {
                "name": record.get("name"),
                "pipeline": custom_strategy_pipeline(record["id"]),
                "symbols": symbols,
                "lookback_bars": 360,
                "params": params,
                "user_strategy_id": record["id"],
            },
            "user_strategy": True,
            "owner_user_id": record.get("owner_user_id"),
            "status": record.get("status"),
            "version": record.get("version"),
            "risk_level": record.get("risk_level"),
            "generation_mode": mode,
            "generation_label": generation_label,
        }
