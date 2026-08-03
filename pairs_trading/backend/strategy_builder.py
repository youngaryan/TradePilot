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
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..core.portfolio import PortfolioManager
from ..core.timeframes import TradingMode, normalize_trading_mode
from ..pipelines import DirectionalPipelineConfig, DirectionalStrategyPipeline
from ..platform import build_metadata_store
from ..strategies import build_rule_based_strategy_factory
from .config import BackendSettings
from .observability import METRICS


CUSTOM_PIPELINE_PREFIX = "user_strategy:"
MARKETPLACE_PIPELINE_PREFIX = "marketplace_strategy:"
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
    "OR",
    "I",
    "IT",
    "TO",
    "AT",
    "OF",
    "MAX",
    "MIN",
    "THE",
    "ONLY",
    "PERCENT",
}


class StrategyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None


class StrategyIndicator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class StrategyParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    default: Any
    min: float | None = None
    max: float | None = None
    description: str


class StrategySpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["strategy_spec/v1"] = "strategy_spec/v1"
    name: str = Field(min_length=3, max_length=120)
    summary: str = Field(min_length=12, max_length=1000)
    asset_universe: dict[str, Any]
    timeframe: str
    side: Literal["long_only", "short_only", "long_short"] = "long_only"
    required_indicators: list[StrategyIndicator] = Field(default_factory=list)
    entry_rules: list[StrategyRule] = Field(default_factory=list)
    exit_rules: list[StrategyRule] = Field(default_factory=list)
    entry_logic: Literal["all", "any"] = "all"
    exit_logic: Literal["all", "any"] = "any"
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


def marketplace_strategy_pipeline(subscription_id: str) -> str:
    return f"{MARKETPLACE_PIPELINE_PREFIX}{subscription_id}"


def parse_marketplace_strategy_pipeline(pipeline: str) -> str | None:
    if not pipeline.startswith(MARKETPLACE_PIPELINE_PREFIX):
        return None
    subscription_id = pipeline[len(MARKETPLACE_PIPELINE_PREFIX):].strip()
    return subscription_id or None


def max_rule_lookback(spec: dict[str, Any]) -> int:
    """Return the longest indicator window referenced by an allowlisted spec."""
    windows: list[int] = []
    for group in ("required_indicators", "entry_rules", "exit_rules"):
        for item in spec.get(group, []) if isinstance(spec.get(group), list) else []:
            parameters = item.get("parameters") if isinstance(item, dict) and isinstance(item.get("parameters"), dict) else {}
            for key in ("window", "fast_window", "slow_window", "signal_window"):
                try:
                    value = int(parameters.get(key))
                except (TypeError, ValueError):
                    continue
                if value > 1:
                    windows.append(value)
    return max(windows, default=20)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _detect_prompt_injection(text: str) -> bool:
    normalized = _normalize_text(text).casefold()
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in PROMPT_INJECTION_PATTERNS)


def _extract_symbols(text: str) -> list[str]:
    # Prefer explicitly capitalized ticker-like tokens. Treating every short
    # English word as a ticker made natural descriptions produce universes such
    # as ["SPY", "THE", "ONLY"].
    candidates = re.findall(r"\b[A-Z]{1,5}\b", text)
    symbols: list[str] = []
    for candidate in candidates:
        if candidate in SYMBOL_STOPWORDS:
            continue
        if candidate not in symbols:
            symbols.append(candidate)
    if symbols:
        return symbols[:12]

    # Support a bounded lower-case symbol list after an explicit universe or
    # trading phrase without treating arbitrary short English words as tickers.
    list_match = re.search(
        r"\b(?:trade|symbols?|tickers?|universe)\s+(.+?)(?=\s+(?:on|using|when|with|at|from)\b|[.;]|$)",
        text,
        re.IGNORECASE,
    )
    if list_match:
        for raw in re.split(r"\s*(?:,|\band\b)\s*", list_match.group(1), flags=re.IGNORECASE):
            candidate = raw.strip().upper()
            if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", candidate) and candidate not in SYMBOL_STOPWORDS and candidate not in symbols:
                symbols.append(candidate)
    return symbols[:12]


def _extract_cost_assumptions(text: str) -> dict[str, float] | None:
    labels = {
        "commission_bps": r"commission|commissions|broker(?:age)? fee",
        "spread_bps": r"spread|bid[- ]ask spread",
        "slippage_bps": r"slippage",
        "market_impact_bps": r"market impact|impact",
    }
    explicit: dict[str, float] = {}
    number = r"(\d+(?:\.\d+)?)"
    for field, label in labels.items():
        patterns = (
            rf"{number}\s*bps?\s*(?:of\s+)?(?:{label})",
            rf"(?:{label})\s*(?:of|at|=|:)?\s*{number}\s*bps?",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                explicit[field] = max(0.0, float(match.group(1)))
                break
    if explicit:
        return {
            "commission_bps": explicit.get("commission_bps", 0.0),
            "spread_bps": explicit.get("spread_bps", 0.0),
            "slippage_bps": explicit.get("slippage_bps", 0.0),
            "market_impact_bps": explicit.get("market_impact_bps", 0.0),
        }

    total_patterns = (
        rf"{number}\s*bps?\s*(?:total\s+)?(?:transaction\s+|execution\s+)?costs?",
        rf"(?:total\s+)?(?:transaction\s+|execution\s+)?costs?\s*(?:of|at|=|:)?\s*{number}\s*bps?",
    )
    total: float | None = None
    for pattern in total_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            total = max(0.0, float(match.group(1)))
            break
    if total is None:
        fallback = re.search(rf"{number}\s*bps?", text, re.IGNORECASE)
        total = max(0.0, float(fallback.group(1))) if fallback else None
    if total is None:
        return None
    component = round(total / 4.0, 4)
    return {
        "commission_bps": component,
        "spread_bps": component,
        "slippage_bps": component,
        "market_impact_bps": round(total - (3.0 * component), 4),
    }


def _requests_same_bar_execution(text: str) -> bool:
    return bool(re.search(
        r"(?:same|signal)\s+bar(?:'s)?\s+(?:close|closing)|"
        r"(?:on|at)\s+(?:the\s+)?close\s+of\s+(?:the\s+)?signal\s+bar|"
        r"execute\w*\s+(?:on|at)\s+(?:the\s+)?(?:same|signal)\s+bar",
        text,
        re.IGNORECASE,
    ))


def _extract_timeframe(text: str) -> str | None:
    lowered = text.casefold()
    if any(token in lowered for token in ("1d", "daily", "day bars", "daily bars", "end of day")):
        return "1d"
    if any(token in lowered for token in ("hour", "intraday", "1h", "4h", "four-hour", "short-term", "short term")):
        return "short_term"
    if any(token in lowered for token in ("minute", "tick", "5m", "15m")):
        return "unsupported_intraday"
    return None


def _extract_sizing_percent(text: str, default: float | None = None) -> float | None:
    patterns = (
        r"(?:size|sizing|allocate|allocation|hold|holding|position|max(?:imum)?(?: position)?|per (?:name|symbol)|equal\s+weight(?:ed)?(?:\s+(?:at|of))?)\D{0,20}(\d+(?:\.\d+)?)\s*%",
        r"(\d+(?:\.\d+)?)\s*%\s*(?:position|allocation|weight|per (?:name|symbol)|of (?:the )?(?:account|portfolio|capital))",
    )
    match = next((found for pattern in patterns if (found := re.search(pattern, text, re.IGNORECASE))), None)
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
    numbers = [
        int(match.group(1))
        for match in re.finditer(
            r"\b(\d{1,4})\s*(?:[- ]?(?:day|bar|period)s?)?\s*(?:SMA|EMA|moving average)\b",
            text,
            re.IGNORECASE,
        )
    ]
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
        below_match = re.search(
            r"(?:rsi(?:\s+\d+)?(?:(?!\b(?:price|sma|ema|macd)\b).){0,30}?(?:below|under)|(?:below|under)[^.;,]{0,16}?rsi|oversold)\D{0,12}(\d{1,2}(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
        above_match = re.search(
            r"(?:rsi(?:\s+\d+)?(?:(?!\b(?:price|sma|ema|macd)\b).){0,30}?(?:above|over)|(?:above|over)[^.;,]{0,16}?rsi|overbought)\D{0,12}(\d{1,2}(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
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
    if "macd" in lowered:
        macd_match = re.search(
            r"macd\D{0,12}(\d{1,3})\D{1,8}(\d{1,3})\D{1,8}(\d{1,3})",
            text,
            re.IGNORECASE,
        )
        fast, slow, signal = (12, 26, 9)
        if macd_match:
            fast, slow, signal = (int(macd_match.group(1)), int(macd_match.group(2)), int(macd_match.group(3)))
        indicators.append({"name": "MACD", "kind": "macd", "parameters": {"fast_window": fast, "slow_window": slow, "signal_window": signal}})
        entry_rules.append({"kind": "macd_above_signal", "parameters": {"fast_window": fast, "slow_window": slow, "signal_window": signal}, "description": "Enter when MACD is above the signal line."})
        exit_rules.append({"kind": "macd_below_signal", "parameters": {"fast_window": fast, "slow_window": slow, "signal_window": signal}, "description": "Exit when MACD is below the signal line."})
        params.extend(
            [
                {"name": "macd_fast_window", "default": fast, "min": 2, "max": 100, "description": "Fast EMA window."},
                {"name": "macd_slow_window", "default": slow, "min": 3, "max": 200, "description": "Slow EMA window."},
                {"name": "macd_signal_window", "default": signal, "min": 2, "max": 100, "description": "Signal-line EMA window."},
            ]
        )

    if any(token in lowered for token in ("moving average", "sma", "ema", "golden cross", "death cross", "ma cross")):
        uses_ema = "ema" in lowered or "exponential" in lowered
        fast, slow = _ma_windows(text, 50, 200)
        indicator_kind = "ema" if uses_ema else "sma"
        entry_kind = "ema_cross_above" if uses_ema else "sma_cross_above"
        exit_kind = "ema_cross_below" if uses_ema else "sma_cross_below"
        price_vs_average = bool(re.search(r"price.{0,20}?(?:above|below|under|over).{0,30}?(?:sma|ema|moving average)", text, re.IGNORECASE))
        if price_vs_average:
            window = slow if slow != 200 or fast != 50 else _first_int_near(text, indicator_kind, 50)
            indicators.append({"name": f"{indicator_kind.upper()} {window}", "kind": indicator_kind, "parameters": {"window": window}})
            entry_rules.append({"kind": f"price_above_{indicator_kind}", "parameters": {"window": window}, "description": f"Enter when price is above the {window}-bar {indicator_kind.upper()}."})
            exit_rules.append({"kind": f"price_below_{indicator_kind}", "parameters": {"window": window}, "description": f"Exit when price is below the {window}-bar {indicator_kind.upper()}."})
            params.append({"name": "ma_window", "default": window, "min": 2, "max": 500, "description": "Moving-average lookback."})
        else:
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
    max_position = _extract_sizing_percent(text)
    lowered = text.casefold()
    if max_position is None and "equal weight" in lowered and symbols:
        max_position = min(1.0, 1.0 / len(symbols))
    stop_loss = None
    stop_match = re.search(r"stop(?:\s+loss)?\s*(?:at|of)?\s*(\d+(?:\.\d+)?)\s*%", text, re.IGNORECASE)
    if not stop_match:
        stop_match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*stop(?:\s+loss)?", text, re.IGNORECASE)
    if not stop_match:
        stop_match = re.search(
            r"(?:price\s+)?(?:falls?|drops?|declines?)\s+(\d+(?:\.\d+)?)\s*%\s+"
            r"(?:below|from)\s+(?:the\s+)?(?:entry|entry price|purchase price)",
            text,
            re.IGNORECASE,
        )
    if not stop_match:
        stop_match = re.search(
            r"(\d+(?:\.\d+)?)\s*%\s+(?:below|under)\s+(?:the\s+)?(?:entry|entry price|purchase price)",
            text,
            re.IGNORECASE,
        )
    if stop_match:
        stop_loss = float(stop_match.group(1)) / 100.0
    take_profit = None
    take_profit_match = re.search(
        r"(?:take\s+profit|profit\s+target)\s*(?:at|of)?\s*(\d+(?:\.\d+)?)\s*%|"
        r"(\d+(?:\.\d+)?)\s*%\s*(?:take\s+profit|profit\s+target)",
        text,
        re.IGNORECASE,
    )
    if take_profit_match:
        take_profit = float(take_profit_match.group(1) or take_profit_match.group(2)) / 100.0
    parsed_costs = _extract_cost_assumptions(text)

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
    if parsed_costs is None:
        questions.append("What transaction cost assumption should be used in basis points?")
    if questions:
        return None, questions, "needs_clarification"

    side = "long_only"
    name = _infer_name(text, indicators)
    max_positions = min(len(symbols), max(1, int(round(1.0 / max(max_position or 1.0, 0.01)))))
    same_bar_normalized = _requests_same_bar_execution(text)
    editable_params.append({
        "name": "max_position_per_symbol",
        "default": round(float(max_position or min(1.0, 1.0 / len(symbols))), 6),
        "min": 0.01,
        "max": 1.0,
        "description": "Maximum portfolio weight for each active symbol.",
    })
    editable_params.append({"name": "max_positions", "default": max_positions, "min": 1, "max": min(12, len(symbols)), "description": "Maximum number of symbols held concurrently."})
    if stop_loss is not None:
        editable_params.append({"name": "stop_loss_pct", "default": stop_loss, "min": 0.001, "max": 0.50, "description": "Close-based stop loss from the delayed fill price."})
    if take_profit is not None:
        editable_params.append({"name": "take_profit_pct", "default": take_profit, "min": 0.001, "max": 5.0, "description": "Close-based take-profit threshold from the delayed fill price."})
    for cost_name, cost_value in (parsed_costs or {}).items():
        editable_params.append({"name": cost_name, "default": cost_value, "min": 0.0, "max": 10_000.0, "description": f"{cost_name.replace('_', ' ').replace(' bps', '')} in basis points."})
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
            "take_profit_pct": take_profit,
            "max_positions": max_positions,
        },
        "rebalancing": {
            "frequency": "intraday" if timeframe == "short_term" else "daily",
            "execution_timing": "next_bar_close",
        },
        "costs": {
            **(parsed_costs or {}),
            "delay_bars": 1,
        },
        "assumptions": [
            "Signals are computed from historical daily close data." if timeframe == "1d" else "Signals use hourly execution bars with 4-hour confirmation in short-term mode.",
            "Execution uses the existing backtest engine's next-bar delayed close-to-close execution model.",
            "No arbitrary user code is generated or executed.",
            *(["Requested signal-bar-close execution is conservatively normalized to next-bar-close execution to avoid look-ahead bias."] if same_bar_normalized else []),
        ],
        "limitations": [
            "The safe builder currently supports a constrained set of technical-indicator rule blocks.",
            "Minute bars, tick data, custom external data, and discretionary text rules are not supported in this builder.",
            "Backtest performance does not guarantee future results.",
            *(["The engine does not execute at the already-observed signal-bar close; orders are delayed to the next bar close."] if same_bar_normalized else []),
        ],
        "editable_parameters": editable_params,
        "compatibility": {
            "engine": "directional_ledger_v1",
            "supported": True,
            "execution_normalized": same_bar_normalized,
            "notes": ["Compatible with the directional walk-forward backtest path and ledger visualization outputs."],
        },
    }
    return spec, [], "ready_for_approval"


def _revise_rule_draft(draft_spec: dict[str, Any], text: str) -> dict[str, Any]:
    revised = json.loads(json.dumps(draft_spec))
    lowered = text.casefold()
    indicators, entry_rules, exit_rules, indicator_params = _build_rule_spec(text)
    has_entry_instruction = bool(re.search(r"\b(?:entry|enter|buy|below|under|cross(?:es)?\s+above)\b", text, re.IGNORECASE))
    has_exit_instruction = bool(re.search(r"\b(?:exit|leave|sell|close|above|over|cross(?:es)?\s+below)\b", text, re.IGNORECASE))
    if entry_rules and exit_rules and has_entry_instruction and has_exit_instruction:
        revised["required_indicators"] = indicators
        revised["entry_rules"] = entry_rules
        revised["exit_rules"] = exit_rules
        retained = [
            item for item in revised.get("editable_parameters", [])
            if isinstance(item, dict) and item.get("name") in {"max_position_per_symbol", "stop_loss_pct", "take_profit_pct"}
        ]
        revised["editable_parameters"] = [*indicator_params, *retained]
    elif any(str(rule.get("kind") or "").startswith("rsi_") for rule in revised.get("entry_rules", []) + revised.get("exit_rules", [])):
        entry_match = re.search(r"(?:entry|enter|buy|below|under)\D{0,20}(\d{1,3}(?:\.\d+)?)", text, re.IGNORECASE)
        exit_match = re.search(r"(?:exit|leave|sell|above|over)\D{0,20}(\d{1,3}(?:\.\d+)?)", text, re.IGNORECASE)
        if entry_match:
            for rule in revised.get("entry_rules", []):
                if str(rule.get("kind") or "").startswith("rsi_"):
                    rule.setdefault("parameters", {})["threshold"] = float(entry_match.group(1))
                    rule["description"] = f"Enter when RSI is at or below {float(entry_match.group(1)):g}."
        if exit_match:
            for rule in revised.get("exit_rules", []):
                if str(rule.get("kind") or "").startswith("rsi_"):
                    rule.setdefault("parameters", {})["threshold"] = float(exit_match.group(1))
                    rule["description"] = f"Exit when RSI is at or above {float(exit_match.group(1)):g}."

    symbols = _extract_symbols(text)
    if symbols:
        revised.setdefault("asset_universe", {})["symbols"] = symbols
    timeframe = _extract_timeframe(text)
    if timeframe and timeframe != "unsupported_intraday":
        revised["timeframe"] = timeframe
        revised.setdefault("rebalancing", {})["frequency"] = "intraday" if timeframe == "short_term" else "daily"
    sizing = _extract_sizing_percent(text)
    if sizing is not None:
        symbol_count = len(revised.get("asset_universe", {}).get("symbols", []))
        position_sizing = revised.setdefault("position_sizing", {})
        existing_gross = float(position_sizing.get("max_gross_exposure", 1.0))
        position_sizing["max_position_per_symbol"] = sizing
        position_sizing["max_gross_exposure"] = min(existing_gross, sizing * max(symbol_count, 1))
    stop_match = re.search(r"(?:stop(?:\s+loss)?\D{0,12})(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*%\s*stop", text, re.IGNORECASE)
    if "no stop" in lowered:
        revised.setdefault("risk_controls", {})["stop_loss_pct"] = None
    elif stop_match:
        revised.setdefault("risk_controls", {})["stop_loss_pct"] = float(stop_match.group(1) or stop_match.group(2)) / 100.0
    take_match = re.search(r"(?:take\s+profit|profit\s+target)\D{0,12}(\d+(?:\.\d+)?)\s*%", text, re.IGNORECASE)
    if take_match:
        revised.setdefault("risk_controls", {})["take_profit_pct"] = float(take_match.group(1)) / 100.0
    costs = _extract_cost_assumptions(text)
    if costs is not None:
        revised["costs"] = {**costs, "delay_bars": 1}

    existing_parameter_names = {
        str(item.get("name")) for item in revised.get("editable_parameters", []) if isinstance(item, dict)
    }
    for name, field, maximum, description in (
        ("stop_loss_pct", "stop_loss_pct", 0.5, "Close-based stop loss from the delayed fill price."),
        ("take_profit_pct", "take_profit_pct", 5.0, "Close-based take-profit threshold from the delayed fill price."),
    ):
        value = revised.get("risk_controls", {}).get(field)
        if value is not None and name not in existing_parameter_names:
            revised.setdefault("editable_parameters", []).append(
                {"name": name, "default": value, "min": 0.001, "max": maximum, "description": description}
            )
            existing_parameter_names.add(name)
    for name, value in (costs or {}).items():
        if name not in existing_parameter_names:
            revised.setdefault("editable_parameters", []).append(
                {"name": name, "default": value, "min": 0.0, "max": 10_000.0, "description": f"{name.replace('_', ' ').replace(' bps', '')} in basis points."}
            )
            existing_parameter_names.add(name)

    # Keep the UI/backtest defaults bound to the revised executable values.
    for item in revised.get("editable_parameters", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if name == "max_position_per_symbol":
            item["default"] = revised.get("position_sizing", {}).get(name)
        elif name in {"stop_loss_pct", "take_profit_pct", "max_positions"}:
            item["default"] = revised.get("risk_controls", {}).get(name)
        elif name in {"commission_bps", "spread_bps", "slippage_bps", "market_impact_bps", "delay_bars"}:
            item["default"] = revised.get("costs", {}).get(name)
        elif name.startswith("rsi_"):
            group = "entry_rules" if name == "rsi_entry_threshold" else "exit_rules" if name == "rsi_exit_threshold" else "entry_rules"
            key = "window" if name == "rsi_window" else "threshold"
            rule = next((rule for rule in revised.get(group, []) if str(rule.get("kind") or "").startswith("rsi_")), None)
            if rule:
                item["default"] = rule.get("parameters", {}).get(key)
    revised["summary"] = str(revised.get("summary") or "Validated user strategy.")
    return revised


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

    for indicator in normalized.get("required_indicators") or []:
        kind = str(indicator.get("kind") or "")
        params = indicator.get("parameters") if isinstance(indicator.get("parameters"), dict) else {}
        required_keys = {
            "rsi": ("window",),
            "sma": ("window",),
            "ema": ("window",),
            "macd": ("fast_window", "slow_window", "signal_window"),
        }.get(kind)
        if required_keys is None:
            errors.append(f"Unsupported indicator kind: {kind}")
            continue
        parsed_indicator_windows: dict[str, int] = {}
        for key in required_keys:
            if key not in params:
                errors.append(f"Indicator {kind}.{key} is required.")
                continue
            try:
                value = int(params[key])
            except (TypeError, ValueError):
                errors.append(f"Indicator {kind}.{key} must be an integer.")
                continue
            if isinstance(params[key], bool) or not 2 <= value <= 5_000:
                errors.append(f"Indicator {kind}.{key} must be an integer between 2 and 5000.")
            parsed_indicator_windows[key] = value
        if "fast_window" in parsed_indicator_windows and "slow_window" in parsed_indicator_windows and parsed_indicator_windows["fast_window"] >= parsed_indicator_windows["slow_window"]:
            errors.append(f"Indicator {kind}.fast_window must be smaller than slow_window.")

    if not normalized.get("entry_rules"):
        errors.append("At least one entry rule is required.")
    if not normalized.get("exit_rules"):
        errors.append("At least one exit rule is required.")
    for rule in list(normalized.get("entry_rules") or []) + list(normalized.get("exit_rules") or []):
        kind = str(rule.get("kind") or "")
        if kind not in SUPPORTED_RULE_KINDS:
            errors.append(f"Unsupported rule kind: {kind}")
        params = rule.get("parameters") if isinstance(rule.get("parameters"), dict) else {}
        required_by_kind = {
            "price_above_sma": ("window",),
            "price_below_sma": ("window",),
            "price_above_ema": ("window",),
            "price_below_ema": ("window",),
            "sma_cross_above": ("fast_window", "slow_window"),
            "sma_cross_below": ("fast_window", "slow_window"),
            "ema_cross_above": ("fast_window", "slow_window"),
            "ema_cross_below": ("fast_window", "slow_window"),
            "rsi_below": ("window", "threshold"),
            "rsi_above": ("window", "threshold"),
            "macd_above_signal": ("fast_window", "slow_window", "signal_window"),
            "macd_below_signal": ("fast_window", "slow_window", "signal_window"),
        }
        for key in required_by_kind.get(kind, ()):
            if key not in params:
                errors.append(f"{kind}.{key} is required.")
        parsed_windows: dict[str, int] = {}
        for key in ("window", "fast_window", "slow_window", "signal_window"):
            if key not in params:
                continue
            try:
                value = int(params[key])
            except (TypeError, ValueError):
                errors.append(f"{kind}.{key} must be an integer.")
                continue
            if isinstance(params[key], bool) or value <= 1 or value > 5_000:
                errors.append(f"{kind}.{key} must be an integer between 2 and 5000.")
            parsed_windows[key] = value
        if "fast_window" in parsed_windows and "slow_window" in parsed_windows and parsed_windows["fast_window"] >= parsed_windows["slow_window"]:
            errors.append(f"{kind}.fast_window must be smaller than slow_window.")
        if kind in {"rsi_below", "rsi_above"} and "threshold" in params:
            try:
                threshold = float(params["threshold"])
            except (TypeError, ValueError):
                errors.append(f"{kind}.threshold must be numeric.")
            else:
                if not 0.0 <= threshold <= 100.0:
                    errors.append(f"{kind}.threshold must be between 0 and 100.")

    sizing = normalized.get("position_sizing") if isinstance(normalized.get("position_sizing"), dict) else {}
    try:
        max_position = float(sizing.get("max_position_per_symbol", 0.0) or 0.0)
    except (TypeError, ValueError):
        max_position = 0.0
        errors.append("position_sizing.max_position_per_symbol must be numeric.")
    try:
        max_gross = float(sizing.get("max_gross_exposure", 0.0) or 0.0)
    except (TypeError, ValueError):
        max_gross = 0.0
        errors.append("position_sizing.max_gross_exposure must be numeric.")
    if sizing.get("method") != "equal_weight":
        errors.append("position_sizing.method must be equal_weight.")
    if max_position <= 0 or max_position > 1.0:
        errors.append("position_sizing.max_position_per_symbol must be in (0, 1].")
    if max_gross <= 0 or max_gross > 1.5:
        errors.append("position_sizing.max_gross_exposure must be in (0, 1.5].")
    if normalized.get("side") != "long_only":
        errors.append("The strategy-builder DSL currently supports long-only generated strategies only.")
    risk = normalized.get("risk_controls") if isinstance(normalized.get("risk_controls"), dict) else {}
    stop_loss = risk.get("stop_loss_pct")
    if stop_loss is not None:
        try:
            parsed_stop = float(stop_loss)
        except (TypeError, ValueError):
            errors.append("risk_controls.stop_loss_pct must be numeric or null.")
        else:
            if not 0 < parsed_stop <= 0.50:
                errors.append("risk_controls.stop_loss_pct must be null or between 0 and 50%.")
    take_profit = risk.get("take_profit_pct")
    if take_profit is not None:
        try:
            parsed_take_profit = float(take_profit)
        except (TypeError, ValueError):
            errors.append("risk_controls.take_profit_pct must be numeric or null.")
        else:
            if not 0 < parsed_take_profit <= 5.0:
                errors.append("risk_controls.take_profit_pct must be null or between 0 and 500%.")
    max_positions = risk.get("max_positions")
    if isinstance(max_positions, bool) or not isinstance(max_positions, int) or not 1 <= max_positions <= 12:
        errors.append("risk_controls.max_positions must be an integer between 1 and 12.")
    if stop_loss is None:
        warnings.append("No hard stop loss is configured; exits rely on rule logic.")

    universe = normalized.get("asset_universe") if isinstance(normalized.get("asset_universe"), dict) else {}
    if universe.get("type") != "explicit_symbols":
        errors.append("asset_universe.type must be explicit_symbols.")

    costs = normalized.get("costs") if isinstance(normalized.get("costs"), dict) else {}
    for key in ("commission_bps", "spread_bps", "slippage_bps", "market_impact_bps"):
        try:
            cost = float(costs.get(key, 0.0))
        except (TypeError, ValueError):
            errors.append(f"costs.{key} must be numeric.")
        else:
            if cost < 0 or cost > 10_000:
                errors.append(f"costs.{key} must be between 0 and 10000 basis points.")
    delay_bars = costs.get("delay_bars", 1)
    if isinstance(delay_bars, bool) or not isinstance(delay_bars, int) or not 1 <= delay_bars <= 20:
        errors.append("costs.delay_bars must be an integer between 1 and 20.")

    rebalancing = normalized.get("rebalancing") if isinstance(normalized.get("rebalancing"), dict) else {}
    expected_frequency = "intraday" if normalized.get("timeframe") == "short_term" else "daily"
    if rebalancing.get("frequency") != expected_frequency:
        errors.append(f"rebalancing.frequency must be {expected_frequency} for this timeframe.")
    if rebalancing.get("execution_timing") != "next_bar_close":
        errors.append("rebalancing.execution_timing must be next_bar_close.")
    compatibility = normalized.get("compatibility") if isinstance(normalized.get("compatibility"), dict) else {}
    if compatibility.get("supported") is not True:
        errors.append("compatibility.supported must be true.")
    if compatibility.get("engine") not in {None, "directional_ledger_v1"}:
        errors.append("compatibility.engine must be directional_ledger_v1.")
    if compatibility.get("execution_normalized") is True:
        warnings.append(
            "Requested signal-bar-close execution was normalized to next-bar-close execution to avoid look-ahead bias."
        )

    supported_editable_parameters = {
        "rsi_window", "rsi_entry_threshold", "rsi_exit_threshold",
        "macd_fast_window", "macd_slow_window", "macd_signal_window",
        "fast_window", "slow_window", "ma_window",
        "max_position_per_symbol", "max_gross_exposure", "max_positions",
        "stop_loss_pct", "take_profit_pct",
        "commission_bps", "spread_bps", "slippage_bps", "market_impact_bps", "delay_bars",
    }
    editable_names: set[str] = set()
    for parameter in normalized.get("editable_parameters") or []:
        name = str(parameter.get("name") or "")
        if name in editable_names:
            errors.append(f"Duplicate editable parameter: {name}")
        editable_names.add(name)
        if name not in supported_editable_parameters:
            errors.append(f"Editable strategy parameter {name} has no executable binding.")
        try:
            default = float(parameter.get("default"))
            minimum = float(parameter["min"]) if parameter.get("min") is not None else None
            maximum = float(parameter["max"]) if parameter.get("max") is not None else None
        except (TypeError, ValueError):
            errors.append(f"Editable strategy parameter {name} must have numeric default/min/max values.")
            continue
        if minimum is not None and maximum is not None and minimum > maximum:
            errors.append(f"Editable strategy parameter {name} has min greater than max.")
        if minimum is not None and default < minimum or maximum is not None and default > maximum:
            errors.append(f"Editable strategy parameter {name} default must be within its declared bounds.")

    return SpecValidation(ok=not errors, errors=errors, warnings=warnings, spec=normalized if not errors else None)


def apply_strategy_parameters(spec: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Apply only declared editable parameters to their executable DSL fields."""

    if not overrides:
        return json.loads(json.dumps(spec))
    updated = json.loads(json.dumps(spec))
    declared = {
        str(item.get("name")): item
        for item in updated.get("editable_parameters", [])
        if isinstance(item, dict) and item.get("name")
    }
    unknown = sorted(set(overrides).difference(declared))
    if unknown:
        raise ValueError(f"Unknown strategy parameter(s): {', '.join(unknown)}")

    def numeric_value(name: str, raw: Any) -> int | float:
        definition = declared[name]
        default = definition.get("default")
        try:
            value: int | float = int(raw) if isinstance(default, int) and not isinstance(default, bool) else float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Strategy parameter {name} must be numeric.") from exc
        minimum = definition.get("min")
        maximum = definition.get("max")
        if minimum is not None and value < float(minimum):
            raise ValueError(f"Strategy parameter {name} must be at least {minimum}.")
        if maximum is not None and value > float(maximum):
            raise ValueError(f"Strategy parameter {name} must be at most {maximum}.")
        return value

    rule_bindings = {
        "rsi_window": ("rsi", "window", "both"),
        "rsi_entry_threshold": ("rsi", "threshold", "entry"),
        "rsi_exit_threshold": ("rsi", "threshold", "exit"),
        "macd_fast_window": ("macd", "fast_window", "both"),
        "macd_slow_window": ("macd", "slow_window", "both"),
        "macd_signal_window": ("macd", "signal_window", "both"),
        "fast_window": ("cross", "fast_window", "both"),
        "slow_window": ("cross", "slow_window", "both"),
        "ma_window": ("price", "window", "both"),
    }
    for name, raw in overrides.items():
        value = numeric_value(name, raw)
        declared[name]["default"] = value
        if name in rule_bindings:
            family, key, group = rule_bindings[name]
            groups = ("entry_rules", "exit_rules") if group == "both" else (("entry_rules",) if group == "entry" else ("exit_rules",))
            for group_name in groups:
                for rule in updated.get(group_name, []):
                    kind = str(rule.get("kind") or "")
                    matches = (
                        (family == "rsi" and kind.startswith("rsi_"))
                        or (family == "macd" and kind.startswith("macd_"))
                        or (family == "cross" and "_cross_" in kind)
                        or (family == "price" and kind.startswith("price_"))
                    )
                    if matches:
                        rule.setdefault("parameters", {})[key] = value
        elif name in {"max_position_per_symbol", "max_gross_exposure"}:
            updated.setdefault("position_sizing", {})[name] = value
            if name == "max_position_per_symbol":
                symbol_count = len(updated.get("asset_universe", {}).get("symbols", []))
                existing_gross = float(updated["position_sizing"].get("max_gross_exposure", 1.0))
                updated["position_sizing"]["max_gross_exposure"] = min(existing_gross, float(value) * max(symbol_count, 1))
        elif name in {"stop_loss_pct", "take_profit_pct", "max_positions"}:
            updated.setdefault("risk_controls", {})[name] = value
        elif name in {"commission_bps", "spread_bps", "slippage_bps", "market_impact_bps", "delay_bars"}:
            updated.setdefault("costs", {})[name] = value
        else:
            raise ValueError(f"Editable strategy parameter {name} has no executable binding.")

    validation = validate_strategy_spec(updated)
    if not validation.ok or validation.spec is None:
        raise ValueError("; ".join(validation.errors) or "Strategy parameters are invalid.")
    return validation.spec


def dry_run_strategy_spec(spec: dict[str, Any]) -> dict[str, Any]:
    factory, min_history = build_rule_based_strategy_factory(spec)
    symbols = [str(symbol) for symbol in spec.get("asset_universe", {}).get("symbols", [])] or ["SYN"]
    train_bars = min_history + 20
    test_bars = 60
    index = pd.bdate_range("2000-01-03", periods=train_bars + test_bars)
    values = np.arange(len(index), dtype=float)
    prices = pd.DataFrame(
        {
            symbol: 100.0
            + np.sin((values + offset * 3.0) / (7.0 + offset)) * (4.0 + offset * 0.25)
            + values * (0.03 + offset * 0.002)
            for offset, symbol in enumerate(symbols)
        },
        index=index,
    )
    sizing = spec.get("position_sizing") if isinstance(spec.get("position_sizing"), dict) else {}
    risk_controls = spec.get("risk_controls") if isinstance(spec.get("risk_controls"), dict) else {}
    pipeline = DirectionalStrategyPipeline(
        strategy_factory=factory,
        portfolio_manager=PortfolioManager(
            max_leverage=float(sizing.get("max_gross_exposure", 1.0)),
            max_strategy_weight=float(sizing.get("max_position_per_symbol", 1.0)),
            allocation_method="equal_weight",
            max_active_positions=int(risk_controls.get("max_positions", len(symbols))),
        ),
        config=DirectionalPipelineConfig.from_symbols(symbols=symbols, min_history=min_history),
        name=str(spec.get("name") or "strategy_dry_run"),
    )
    output = pipeline.run_fold(prices.iloc[:train_bars], prices.iloc[train_bars:]).validate(
        extra_columns=("gross_return",)
    )
    frame = output.frame
    if frame.empty:
        raise ValueError("Synthetic dry-run produced no output rows.")
    required = {"signal", "position", "gross_return", "turnover"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Synthetic dry-run missing output columns: {', '.join(sorted(missing))}")
    return {
        "status": "passed",
        "rows": int(len(frame)),
        "nonzero_positions": int((frame["position"].abs() > 0).sum()),
        "required_train_bars": int(min_history),
        "symbols_checked": len(symbols),
        "checked_at_utc": utc_now_iso(),
    }


def risk_level_for_spec(spec: dict[str, Any]) -> str:
    sizing = spec.get("position_sizing") if isinstance(spec.get("position_sizing"), dict) else {}
    risk = spec.get("risk_controls") if isinstance(spec.get("risk_controls"), dict) else {}
    side = str(spec.get("side") or "long_only")
    max_gross = float(sizing.get("max_gross_exposure", 1.0) or 1.0)
    max_position = float(sizing.get("max_position_per_symbol", 1.0) or 1.0)
    stop_loss = risk.get("stop_loss_pct")
    timeframe = str(spec.get("timeframe") or "1d")
    costs = spec.get("costs") if isinstance(spec.get("costs"), dict) else {}
    total_cost_bps = sum(float(costs.get(key, 0.0) or 0.0) for key in ("commission_bps", "spread_bps", "slippage_bps", "market_impact_bps"))
    if side != "long_only" or max_gross > 1.0 or max_position > 0.75 or (stop_loss is not None and float(stop_loss) > 0.25):
        return "high"
    if stop_loss is None or max_gross > 0.5 or max_position > 0.35 or timeframe == "short_term" or total_cost_bps > 20:
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
                "generation_path": str(provenance.get("generation_path") or "")[:120] or None,
                "semantic_repair_count": max(0, min(int(provenance.get("semantic_repair_count") or 0), 1)),
                "generation_summary": str(provenance.get("generation_summary") or "")[:1_000] or None,
                "risk_analysis": provenance.get("risk_analysis") if isinstance(provenance.get("risk_analysis"), dict) else None,
                "interpreted_intent": provenance.get("interpreted_intent") if isinstance(provenance.get("interpreted_intent"), dict) else None,
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
        generation_summary: str | None = None
        risk_analysis: dict[str, Any] | None = None
        interpreted_intent: dict[str, Any] | None = None
        generation_path = "deterministic_rules"
        semantic_repair_count = 0
        provider_request_count = 0

        if generation_mode == "llm" and _detect_prompt_injection(user_text):
            generation_path = "security_precheck"
            spec, questions, state = None, precheck_questions, "rejected"
            validation = SpecValidation(ok=False, errors=questions, warnings=[])
        elif generation_mode == "llm":
            from .strategy_builder_generation import PROMPT_VERSION

            provider = self.settings.strategy_builder_llm_provider
            model = self.settings.strategy_builder_llm_model
            prompt_version = PROMPT_VERSION
            generation_path = "model_first"
            provider_messages = list(messages)
            # Hosted models interpret the conversation independently. A draft is
            # supplied only when it came from the user's editable review state;
            # the deterministic parser is not a semantic gate or hidden seed.
            candidate_seed = draft_spec
            if self.settings.strategy_builder_llm_provider == "ollama":
                candidate_seed = draft_spec or precheck_spec
            try:
                result = self.generation_service.generate(provider_messages, candidate_seed=candidate_seed)
                provider_request_count = 1
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
                generation_path = str(result.metadata.get("generation_path") or "model_first")[:80]
                questions = list(generated.clarification_questions)
                generation_summary = generated.assistant_summary
                risk_analysis = generated.risk_analysis.model_dump(mode="json") if generated.risk_analysis else None
                interpreted_intent = generated.interpretation.model_dump(mode="json")
                spec = generated.candidate_spec.model_dump(mode="json") if generated.candidate_spec else None
                state = generated.state
                validation = validate_strategy_spec(spec) if spec else SpecValidation(ok=False, errors=questions, warnings=[])
                if state == "ready_for_validation":
                    repair_feedback = list(validation.errors)
                    if validation.ok and validation.spec is not None:
                        try:
                            dry_run_strategy_spec(validation.spec)
                        except Exception:
                            repair_feedback.append("Synthetic engine dry-run rejected the compiled candidate.")

                    if repair_feedback and self.settings.strategy_builder_llm_provider != "ollama":
                        semantic_repair_count = 1
                        try:
                            repaired = self.generation_service.repair(
                                provider_messages,
                                previous=generated,
                                validator_feedback=repair_feedback,
                            )
                        except Exception:
                            provider_outcome = "repair_error"
                            provider_request_count = 2
                            generation_path = "model_first_semantic_repair_failed"
                            questions = [
                                "The model draft did not match the engine, and its bounded correction pass failed. Please restate the affected rules."
                            ]
                            state = "needs_clarification"
                            spec = None
                        else:
                            provider_request_count = 2
                            generation_path = str(
                                repaired.metadata.get("generation_path") or "model_first_semantic_repair"
                            )[:80]
                            latency_ms += max(0, int(repaired.latency_ms))
                            for key, value in repaired.usage.items():
                                if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool):
                                    usage[key[:40]] = min(100_000_000, usage.get(key[:40], 0) + max(0, value))
                            repaired_attempt = repaired.metadata.get("attempt", 1)
                            if isinstance(repaired_attempt, int):
                                retry_count += max(0, min(repaired_attempt - 1, 20))
                            generated = repaired.value
                            questions = list(generated.clarification_questions)
                            generation_summary = generated.assistant_summary
                            risk_analysis = generated.risk_analysis.model_dump(mode="json") if generated.risk_analysis else None
                            interpreted_intent = generated.interpretation.model_dump(mode="json")
                            spec = generated.candidate_spec.model_dump(mode="json") if generated.candidate_spec else None
                            state = generated.state
                            validation = validate_strategy_spec(spec) if spec else SpecValidation(
                                ok=False,
                                errors=questions,
                                warnings=[],
                            )

                    if state == "ready_for_validation" and validation.ok and validation.spec is not None:
                        try:
                            dry_run_strategy_spec(validation.spec)
                        except Exception:
                            questions = ["The candidate could not pass the safe synthetic dry run. Please revise the rules."]
                            state = "needs_clarification"
                            spec = None
                        else:
                            state = "ready_for_approval"
                            spec = validation.spec
                    elif state == "ready_for_validation":
                        questions = validation.errors or ["The generated candidate did not pass deterministic validation."]
                        state = "needs_clarification"
                        spec = None
                elif state == "rejected":
                    unsupported = list(generated.interpretation.unsupported_requirements)
                    questions = questions or unsupported
                    validation = SpecValidation(ok=False, errors=questions, warnings=[])
                    spec = None
                else:
                    questions = questions or list(generated.interpretation.missing_requirements)
                    state = "needs_clarification"
                    spec = None
            except Exception:
                provider_outcome = "error"
                provider_request_count = max(provider_request_count, 1)
                spec = None
                questions = ["The strategy generation provider is temporarily unavailable. Please try again later."]
                state = "needs_clarification"
                validation = SpecValidation(ok=False, errors=[], warnings=["Hosted strategy generation was unavailable."])
                latency_ms = None
        elif draft_spec:
            latest_user_text = next(
                (str(message.get("content") or "") for message in reversed(messages) if message.get("role") == "user"),
                "",
            )
            revised_draft = _revise_rule_draft(draft_spec, latest_user_text)
            validation = validate_strategy_spec(revised_draft)
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
        if generation_mode == "llm" and generation_summary:
            assistant_message = generation_summary

        if generation_mode == "llm":
            metric_provider = provider if provider in {"openai", "anthropic", "deepinfra", "nvidia", "ollama"} else "other"
            metric_model = re.sub(r"[^A-Za-z0-9._:-]", "_", str(model or "unknown"))[:80] or "unknown"
            labels = {"provider": metric_provider, "model": metric_model, "outcome": provider_outcome}
            METRICS.inc(
                "tradepilot_strategy_builder_provider_calls_total",
                labels,
                float(provider_request_count),
            )
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
                "semantic_repair_count": semantic_repair_count,
                "provider_request_count": provider_request_count,
                "generation_path": generation_path,
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
            "generation_path": generation_path,
            "semantic_repair_count": semantic_repair_count,
            "generation_summary": generation_summary,
            "risk_analysis": risk_analysis,
            "interpreted_intent": interpreted_intent,
        }
        return {
            "state": state,
            "assistant_message": assistant_message,
            "generation_summary": generation_summary,
            "risk_analysis": risk_analysis,
            "interpreted_intent": interpreted_intent,
            "generation_path": generation_path,
            "semantic_repair_count": semantic_repair_count,
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
        canonical_spec = json.dumps(validation.spec, sort_keys=True, separators=(",", ":"))
        existing = next(
            (
                item
                for item in self.store.list_user_strategies(
                    organization_id=organization_id,
                    owner_user_id=user_id,
                    active_only=True,
                )
                if json.dumps(item.get("spec") or {}, sort_keys=True, separators=(",", ":")) == canonical_spec
            ),
            None,
        )
        if existing is not None:
            return {
                "strategy": existing,
                "catalog_item": self.catalog_item(existing),
                "validation": {"warnings": validation.warnings, "dry_run": dry_run, "deduplicated": True},
            }
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
        community: list[dict[str, Any]] = []
        if self.settings.marketplace_enabled:
            for subscription in self.store.list_marketplace_subscriptions(subscriber_organization_id=organization_id):
                if subscription.get("status") != "active" or subscription.get("listing_status") not in {"published", "archived"}:
                    continue
                version = self.store.get_strategy_listing_version(version_id=subscription["pinned_listing_version_id"])
                if version is not None:
                    community.append(self.marketplace_catalog_item(subscription, version))
        return [*base_catalog, *[self.catalog_item(record) for record in strategies], *community]

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
        required_train_bars = max(80, max_rule_lookback(spec) * 3)
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
                "lookback_bars": max(360, required_train_bars + 10),
                "train_bars": required_train_bars,
                "trading_mode": "short_term" if spec.get("timeframe") == "short_term" else "daily",
                "interval": "1h" if spec.get("timeframe") == "short_term" else "1d",
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
            "required_train_bars": required_train_bars,
        }

    @staticmethod
    def marketplace_catalog_item(subscription: dict[str, Any], version: dict[str, Any]) -> dict[str, Any]:
        spec = version.get("strategy_spec") if isinstance(version.get("strategy_spec"), dict) else {}
        snapshot = version.get("catalog_snapshot") if isinstance(version.get("catalog_snapshot"), dict) else {}
        symbols = spec.get("asset_universe", {}).get("symbols", []) if isinstance(spec.get("asset_universe"), dict) else []
        params = {item.get("name"): item.get("default") for item in spec.get("editable_parameters", []) if isinstance(item, dict) and item.get("name")}
        return {
            "id": subscription["id"],
            "name": snapshot.get("name") or subscription.get("listing_title") or spec.get("name") or "Community strategy",
            "family": "Community",
            "difficulty": snapshot.get("difficulty") or f"Immutable v{version.get('version', 1)} / {version.get('risk_level', 'medium')} risk",
            "pipeline": marketplace_strategy_pipeline(subscription["id"]),
            "summary": snapshot.get("summary") or spec.get("summary") or "Subscribed immutable community strategy.",
            "how_it_works": snapshot.get("how_it_works") or "Executes the pinned, validated marketplace StrategySpec.",
            "best_for": "Tenant-scoped backtesting of an explicitly subscribed immutable version.",
            "watch_out": snapshot.get("watch_out") or "Community strategies require independent validation before paper use.",
            "key_parameters": tuple(params.keys()),
            "example_cli": "Available from the authenticated web backtesting UI only.",
            "paper_config_example": {
                "name": snapshot.get("name") or spec.get("name"),
                "pipeline": marketplace_strategy_pipeline(subscription["id"]),
                "symbols": symbols,
                "lookback_bars": max(360, max_rule_lookback(spec) * 3),
                "train_bars": max(80, max_rule_lookback(spec) * 3),
                "trading_mode": "short_term" if spec.get("timeframe") == "short_term" else "daily",
                "interval": "1h" if spec.get("timeframe") == "short_term" else "1d",
                "params": params,
            },
            "user_strategy": False,
            "community_strategy": True,
            "status": "active",
            "version": version.get("version"),
            "risk_level": version.get("risk_level"),
            "generation_mode": snapshot.get("generation_mode") or "rules",
            "generation_label": snapshot.get("generation_label") or "Community",
            "required_train_bars": max(80, max_rule_lookback(spec) * 3),
        }
