from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
import json
import math
import re
import time
from threading import Lock
from typing import Any, Callable, Protocol, Sequence, TypeVar

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .llm_providers import LLMCallResult, MockStructuredLLMProvider, StructuredLLMProvider
from .market_research_prompts import AGENT_PROMPTS, PROMPT_VERSION, RESEARCH_DISCLAIMER


T = TypeVar("T", bound=BaseModel)
TICKER_PATTERN = re.compile(r"^[A-Z0-9^][A-Z0-9.^=_-]{0,31}$")


class ResearchDecision(StrEnum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    AVOID = "AVOID"


class ResearchHorizon(StrEnum):
    INTRADAY = "intraday"
    SWING = "swing"
    LONG_TERM = "long-term"


class SignalDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class AgentStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def normalize_ticker(value: str) -> str:
    ticker = str(value or "").strip().upper()
    if not ticker or not TICKER_PATTERN.fullmatch(ticker):
        raise ValueError("ticker must be 1-32 uppercase letters, numbers, or common market symbol characters.")
    return ticker


class MarketResearchInput(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    ticker: str = Field(min_length=1, max_length=32)
    analysis_date: str = Field(default_factory=lambda: date.today().isoformat())
    horizon: ResearchHorizon = ResearchHorizon.SWING
    provider: str = "mock"
    model: str = "mock-research-v1"
    sentiment_dataset_id: str | None = None
    include_sentiment: bool = True
    include_financial_events: bool = True
    lookback_days: int | None = Field(default=None, ge=5, le=900)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ticker")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return normalize_ticker(value)

    @field_validator("analysis_date")
    @classmethod
    def normalize_analysis_date(cls, value: str) -> str:
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError as exc:
            raise ValueError("analysis_date must be formatted as YYYY-MM-DD.") from exc


class DataProvenance(BaseModel):
    source: str
    provider: str
    detail: str
    observed_at_utc: str = Field(default_factory=utc_now_iso)
    url: str | None = None


class PriceBar(BaseModel):
    date: str
    close: float


class NewsItem(BaseModel):
    timestamp: str
    headline: str
    source: str = "unknown"
    url: str | None = None
    sentiment_score: float | None = None


class SourceReference(BaseModel):
    id: str
    source: str
    provider: str
    title: str
    observed_at_utc: str = Field(default_factory=utc_now_iso)
    url: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    verified: bool = False


class MarketResearchContext(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    ticker: str
    analysis_date: str
    horizon: ResearchHorizon = ResearchHorizon.SWING
    price_history: list[PriceBar] = Field(default_factory=list)
    financial_events: list[dict[str, Any]] = Field(default_factory=list)
    financial_events_analysis: dict[str, Any] = Field(default_factory=dict)
    sentiment_matrix: list[dict[str, Any]] = Field(default_factory=list)
    sentiment_analysis: dict[str, Any] = Field(default_factory=dict)
    news: list[NewsItem] = Field(default_factory=list)
    provenance: list[DataProvenance] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)
    company_metadata: dict[str, Any] = Field(default_factory=dict)
    data_freshness: dict[str, str | None] = Field(default_factory=dict)
    confidence_levels: dict[str, float] = Field(default_factory=dict)
    missing_data_indicators: list[str] = Field(default_factory=list)
    data_quality_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ticker")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return normalize_ticker(value)


class ResearchSignal(BaseModel):
    label: str
    direction: SignalDirection
    strength: int = Field(ge=0, le=100)
    rationale: str
    evidence: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)


class AgentOutput(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    agent_name: str
    display_name: str
    version: str = "v1"
    prompt_version: str = PROMPT_VERSION
    summary: str
    signals: list[ResearchSignal] = Field(default_factory=list)
    confidence: int = Field(default=50, ge=0, le=100)
    warnings: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class AgentAuditEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    agent_name: str
    display_name: str
    status: AgentStatus
    prompt_version: str = PROMPT_VERSION
    started_at_utc: str
    finished_at_utc: str
    duration_ms: int
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class MarketResearchReport(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    ticker: str
    analysis_date: str
    decision: ResearchDecision
    confidence: int = Field(ge=0, le=100)
    time_horizon: ResearchHorizon
    summary: str
    bull_thesis: str
    bear_thesis: str
    technical_signals: list[ResearchSignal]
    fundamental_signals: list[ResearchSignal]
    news_sentiment_signals: list[ResearchSignal]
    risk_assessment: AgentOutput
    data_quality_notes: list[str]
    disclaimer: str = RESEARCH_DISCLAIMER
    sentiment_matrix: list[dict[str, Any]] = Field(default_factory=list)
    sentiment_analysis: dict[str, Any] = Field(default_factory=dict)
    financial_events_matrix: list[dict[str, Any]] = Field(default_factory=list)
    financial_events_analysis: dict[str, Any] = Field(default_factory=dict)
    source_references: list[SourceReference] = Field(default_factory=list)
    data_freshness: dict[str, str | None] = Field(default_factory=dict)
    confidence_levels: dict[str, float] = Field(default_factory=dict)
    missing_data_indicators: list[str] = Field(default_factory=list)
    raw_agent_outputs: list[AgentOutput]
    audit_trail: list[AgentAuditEvent]
    provenance: list[DataProvenance]
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at_utc: str = Field(default_factory=utc_now_iso)

    @field_validator("ticker")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return normalize_ticker(value)


class MarketResearchDataProvider(Protocol):
    def collect(self, request: MarketResearchInput, **kwargs: Any) -> MarketResearchContext:
        ...


class DemoMarketResearchDataProvider:
    """Offline provider that always returns enough deterministic evidence for a demo report."""

    provider_name = "demo"

    def collect(self, request: MarketResearchInput) -> MarketResearchContext:
        ticker = normalize_ticker(request.ticker)
        asof = date.fromisoformat(request.analysis_date)
        seed = int(sha256(ticker.encode("utf-8")).hexdigest()[:8], 16)
        drift = ((seed % 17) - 8) / 10_000.0
        amplitude = 1.8 + (seed % 7) * 0.12
        base = 80.0 + (seed % 70)
        bars: list[PriceBar] = []
        for index in range(90):
            day = asof - timedelta(days=89 - index)
            close = base + index * (0.08 + drift) + math.sin(index / 5.0) * amplitude
            bars.append(PriceBar(date=day.isoformat(), close=round(max(close, 1.0), 4)))

        news = [
            NewsItem(
                timestamp=f"{request.analysis_date}T14:00:00Z",
                headline=f"{ticker} sees steady institutional research interest in the demo feed",
                source="demo_news",
                sentiment_score=0.24,
            ),
            NewsItem(
                timestamp=f"{request.analysis_date}T12:15:00Z",
                headline=f"Analysts flag macro and valuation uncertainty around {ticker}",
                source="demo_news",
                sentiment_score=-0.16,
            ),
            NewsItem(
                timestamp=f"{request.analysis_date}T09:30:00Z",
                headline=f"{ticker} trading activity remains within normal demo liquidity bands",
                source="demo_news",
                sentiment_score=0.05,
            ),
        ]
        provenance = [
            DataProvenance(
                source="price_history",
                provider=self.provider_name,
                detail="Deterministic offline close-price series for local research demos.",
            ),
            DataProvenance(
                source="news",
                provider=self.provider_name,
                detail="Synthetic demo headlines with explicit sentiment placeholders.",
            ),
            DataProvenance(
                source="fundamentals",
                provider=self.provider_name,
                detail="No real fundamental provider configured; analyst must state limitations.",
            ),
        ]
        source_references = [
            SourceReference(
                id=f"demo-news-{ticker}-1",
                source="news",
                provider=self.provider_name,
                title=item.headline,
                url=item.url,
                confidence=0.5 if item.sentiment_score is not None else None,
                verified=False,
            )
            for item in news
        ]
        warning = "Demo provider used; connect a real market/news/fundamental provider before relying on live research."
        return MarketResearchContext(
            ticker=ticker,
            analysis_date=request.analysis_date,
            horizon=request.horizon,
            price_history=bars,
            news=news,
            provenance=provenance,
            source_references=source_references,
            company_metadata={"ticker": ticker, "data_mode": "demo"},
            data_freshness={"price_history": request.analysis_date, "news": request.analysis_date, "financial_events": None},
            confidence_levels={"price_history": 0.45, "news": 0.25, "financial_events": 0.0},
            missing_data_indicators=["fundamentals", "real_news_sentiment", "live_market_data"],
            data_quality_notes=[warning, "No brokerage integration is present or used by this research workflow."],
            warnings=[warning],
            provider_metadata={
                "provider": self.provider_name,
                "model": request.model,
                "prompt_version": PROMPT_VERSION,
                "offline_demo": True,
            },
        )


class ResearchAgent(Protocol):
    agent_name: str
    display_name: str
    version: str

    def run(self, context: MarketResearchContext, previous_outputs: Sequence[AgentOutput]) -> AgentOutput:
        ...


def _pct_change(first: float, last: float) -> float:
    return 0.0 if first == 0 else (last / first) - 1.0


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _direction_score(signal: ResearchSignal) -> float:
    if signal.direction == SignalDirection.BULLISH:
        return float(signal.strength)
    if signal.direction == SignalDirection.BEARISH:
        return -float(signal.strength)
    return 0.0


def _find_output(outputs: Sequence[AgentOutput], agent_name: str) -> AgentOutput | None:
    return next((output for output in outputs if output.agent_name == agent_name), None)


def _all_signals(outputs: Sequence[AgentOutput]) -> list[ResearchSignal]:
    signals: list[ResearchSignal] = []
    for output in outputs:
        signals.extend(output.signals)
    return signals


def _compute_rsi(prices: pd.Series, window: int = 14) -> pd.Series:
    delta = prices.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    avg_gain = gains.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = losses.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.mask((avg_loss == 0.0) & (avg_gain > 0.0), 100.0)
    rsi = rsi.mask((avg_gain == 0.0) & (avg_loss > 0.0), 0.0)
    rsi = rsi.mask((avg_gain == 0.0) & (avg_loss == 0.0), 50.0)
    return rsi.fillna(50.0)


def _ema(prices: pd.Series, span: int) -> pd.Series:
    return prices.ewm(span=span, adjust=False, min_periods=span).mean()


def _score_label(value: int) -> str:
    if value > 0:
        return "bullish"
    if value < 0:
        return "bearish"
    return "neutral"


@dataclass(frozen=True)
class TechnicalAnalyst:
    agent_name: str = "technical_analyst"
    display_name: str = "Technical Analyst"
    version: str = "v1"

    def run(self, context: MarketResearchContext, previous_outputs: Sequence[AgentOutput]) -> AgentOutput:
        del previous_outputs
        closes = [bar.close for bar in context.price_history if bar.close > 0]
        n = len(closes)

        if n < 5:
            return AgentOutput(
                agent_name=self.agent_name,
                display_name=self.display_name,
                version=self.version,
                summary="Price history is too sparse for a technical read.",
                confidence=15,
                warnings=["Fewer than 5 close prices were available."],
                signals=[
                    ResearchSignal(
                        label="price_data",
                        direction=SignalDirection.NEUTRAL,
                        strength=10,
                        rationale="Insufficient bars for trend or volatility estimates.",
                    )
                ],
            )

        prices = pd.Series(closes, dtype=float)
        latest = float(prices.iloc[-1])
        daily_returns = prices.pct_change().fillna(0.0)

        # ------------------------------------------------------------------
        # 1. Trend - SMA & EMA crossovers, regression slope
        # ------------------------------------------------------------------
        has_20 = n >= 20
        has_50 = n >= 50
        has_200 = n >= 200

        sma_20 = prices.rolling(20).mean() if has_20 else pd.Series(index=prices.index, dtype=float)
        sma_50 = prices.rolling(50).mean() if has_50 else None
        sma_200 = prices.rolling(200).mean() if has_200 else None

        ema_8 = _ema(prices, 8)
        ema_21 = _ema(prices, 21)

        ema_cross = 1 if n >= 21 and ema_8.iloc[-1] > ema_21.iloc[-1] else -1 if n >= 21 and ema_8.iloc[-1] < ema_21.iloc[-1] else 0
        price_vs_sma20 = 1 if has_20 and latest > sma_20.iloc[-1] else -1 if has_20 and latest < sma_20.iloc[-1] else 0
        sma50_vs_sma200 = (
            1
            if has_50 and has_200 and sma_50.iloc[-1] > sma_200.iloc[-1]
            else -1
            if has_50 and has_200 and sma_50.iloc[-1] < sma_200.iloc[-1]
            else 0
        )

        # Linear regression slope over last 20 bars (as % per bar)
        if n >= 20:
            x = np.arange(20, dtype=float)
            y = prices.iloc[-20:].values
            slope, _ = np.polyfit(x, y, 1)
            slope_pct = slope / float(np.mean(y)) * 100
        else:
            slope_pct = 0.0

        trend_score = ema_cross * 25 + price_vs_sma20 * 20 + sma50_vs_sma200 * 20
        slope_signal = min(15, max(-15, int(slope_pct * 30)))
        trend_score += slope_signal

        if trend_score >= 30:
            trend_dir = SignalDirection.BULLISH
            trend_str = min(90, int(30 + abs(trend_score) * 0.6))
        elif trend_score <= -30:
            trend_dir = SignalDirection.BEARISH
            trend_str = min(90, int(30 + abs(trend_score) * 0.6))
        else:
            trend_dir = SignalDirection.NEUTRAL
            trend_str = 40

        # ------------------------------------------------------------------
        # 2. Momentum - MACD, RSI, ROC
        # ------------------------------------------------------------------
        if n >= 35:
            macd_line = _ema(prices, 12) - _ema(prices, 26)
            macd_signal = macd_line.ewm(span=9, adjust=False, min_periods=9).mean()
            macd_hist_v = float(macd_line.iloc[-1] - macd_signal.iloc[-1])
            macd_hist = 0.0 if math.isnan(macd_hist_v) else macd_hist_v
            macd_dir = SignalDirection.BULLISH if macd_hist > 0 else SignalDirection.BEARISH if macd_hist < 0 else SignalDirection.NEUTRAL
        else:
            macd_hist = 0.0
            macd_dir = SignalDirection.NEUTRAL

        rsi_series = _compute_rsi(prices, 14) if n >= 14 else pd.Series(50.0, index=prices.index)
        rsi_val = float(rsi_series.iloc[-1])

        if rsi_val >= 70:
            rsi_dir = SignalDirection.BEARISH
        elif rsi_val <= 30:
            rsi_dir = SignalDirection.BULLISH
        else:
            rsi_dir = SignalDirection.NEUTRAL

        roc_10 = float(prices.pct_change(10).iloc[-1]) if n >= 11 else 0.0
        roc_21 = float(prices.pct_change(21).iloc[-1]) if n >= 22 else 0.0

        momentum_bull = (
            int(macd_dir == SignalDirection.BULLISH) * 20
            + int(rsi_dir == SignalDirection.BULLISH) * 10
            + int(roc_10 > 0.02) * 15
            + int(roc_21 > 0.03) * 15
        )
        momentum_bear = (
            int(macd_dir == SignalDirection.BEARISH) * 20
            + int(rsi_dir == SignalDirection.BEARISH) * 10
            + int(roc_10 < -0.02) * 15
            + int(roc_21 < -0.03) * 15
        )
        if momentum_bull > momentum_bear:
            mom_dir = SignalDirection.BULLISH
            mom_str = min(85, int(40 + (momentum_bull - momentum_bear) * 0.8))
        elif momentum_bear > momentum_bull:
            mom_dir = SignalDirection.BEARISH
            mom_str = min(85, int(40 + (momentum_bear - momentum_bull) * 0.8))
        else:
            mom_dir = SignalDirection.NEUTRAL
            mom_str = 30

        # ------------------------------------------------------------------
        # 3. Volatility - ATR proxy, Bollinger width, annualized vol
        # ------------------------------------------------------------------
        atr_14 = float(daily_returns.abs().rolling(14).mean().iloc[-1]) if n >= 14 else 0.0
        annualized_vol = float(daily_returns.std() * math.sqrt(252)) if n > 1 else 0.0

        bb_width = 0.0
        bb_position = 0.5
        if has_20:
            bb_mid = sma_20
            bb_std = prices.rolling(20).std(ddof=0)
            bb_upper = bb_mid + 2.0 * bb_std
            bb_lower = bb_mid - 2.0 * bb_std
            bb_width = float(((bb_upper - bb_lower) / bb_mid).iloc[-1])
            bb_pos_val = (latest - float(bb_lower.iloc[-1])) / max(float(bb_upper.iloc[-1] - bb_lower.iloc[-1]), 1e-10)
            bb_position = max(0.0, min(1.0, bb_pos_val))

        vol_high = annualized_vol > 0.40
        vol_dir = SignalDirection.BEARISH if vol_high else SignalDirection.NEUTRAL
        vol_str = min(85, int(annualized_vol * 150)) if vol_high else min(40, int(annualized_vol * 80))

        # ------------------------------------------------------------------
        # 4. Mean-reversion / exhaustion - z-score, Bollinger position
        # ------------------------------------------------------------------
        if has_20:
            zscore = float(((latest - float(sma_20.iloc[-1])) / max(float(prices.rolling(20).std(ddof=0).iloc[-1]), 1e-10)))
        else:
            zscore = 0.0

        if zscore >= 2.0:
            mr_dir = SignalDirection.BEARISH
            mr_str = min(70, int((zscore - 2.0) * 25 + 30))
        elif zscore <= -2.0:
            mr_dir = SignalDirection.BULLISH
            mr_str = min(70, int((abs(zscore) - 2.0) * 25 + 30))
        else:
            mr_dir = SignalDirection.NEUTRAL
            mr_str = int(abs(zscore) * 12)

        # ------------------------------------------------------------------
        # 5. Build signals
        # ------------------------------------------------------------------
        signals = []

        signals.append(ResearchSignal(
            label="trend",
            direction=trend_dir,
            strength=trend_str,
            rationale=(
                f"EMA8/21 cross={_score_label(ema_cross)}, price/SMA20={_score_label(price_vs_sma20)}, "
                f"SMA50/200={_score_label(sma50_vs_sma200)}, "
                f"regression slope={slope_pct:+.3f}%/bar."
            ),
            evidence=[f"last_close={latest:.2f}", f"sma20={float(sma_20.iloc[-1]):.2f}" if has_20 else "sma20=na",
                      f"slope_pct={slope_pct:+.4f}", f"trend_score={trend_score}"],
            provenance=["price_history"],
        ))

        signals.append(ResearchSignal(
            label="momentum",
            direction=mom_dir,
            strength=mom_str,
            rationale=(
                f"MACD hist={macd_hist:+.4f}, RSI={rsi_val:.0f}, "
                f"ROC10={roc_10:+.2%}, ROC21={roc_21:+.2%}."
            ),
            evidence=[f"macd_histogram={macd_hist:+.4f}", f"rsi_14={rsi_val:.1f}",
                      f"roc_10d={roc_10:+.4f}", f"roc_21d={roc_21:+.4f}"],
            provenance=["price_history"],
        ))

        signals.append(ResearchSignal(
            label="volatility",
            direction=vol_dir,
            strength=vol_str,
            rationale=(
                f"Annualized vol={annualized_vol:.1%}, ATR(14)={atr_14:.4f}, "
                f"Bollinger width={bb_width:.2%}."
            ),
            evidence=[f"annualized_vol={annualized_vol:.4f}", f"atr_14={atr_14:.6f}",
                      f"bb_width={bb_width:.4f}", f"bb_position={bb_position:.2f}"],
            provenance=["price_history"],
        ))

        signals.append(ResearchSignal(
            label="mean_reversion",
            direction=mr_dir,
            strength=mr_str,
            rationale=(
                f"20-bar z-score={zscore:+.2f}, Bollinger position={bb_position:.2%}, "
                f"RSI={rsi_val:.0f}."
            ),
            evidence=[f"zscore_20={zscore:+.4f}", f"bb_position={bb_position:.4f}"],
            provenance=["price_history"],
        ))

        # ------------------------------------------------------------------
        # 6. Composite - weighted consensus
        # ------------------------------------------------------------------
        composite = 0
        composite += trend_str * (1 if trend_dir == SignalDirection.BULLISH else -1 if trend_dir == SignalDirection.BEARISH else 0)
        composite += mom_str * (1 if mom_dir == SignalDirection.BULLISH else -1 if mom_dir == SignalDirection.BEARISH else 0)
        composite += mr_str * (1 if mr_dir == SignalDirection.BULLISH else -1 if mr_dir == SignalDirection.BEARISH else 0)
        composite -= vol_str * (1 if vol_dir == SignalDirection.BEARISH else 0)
        n_components = 4
        avg = composite / max(n_components, 1)

        if avg >= 15:
            composite_dir = SignalDirection.BULLISH
            composite_str = min(90, int(35 + abs(avg) * 1.2))
        elif avg <= -15:
            composite_dir = SignalDirection.BEARISH
            composite_str = min(90, int(35 + abs(avg) * 1.2))
        else:
            composite_dir = SignalDirection.NEUTRAL
            composite_str = int(35 + abs(avg))

        signals.append(ResearchSignal(
            label="technical_composite",
            direction=composite_dir,
            strength=composite_str,
            rationale=(
                f"Weighted consensus score={avg:+.1f}. Trend={trend_dir.value}({trend_str}), "
                f"Momentum={mom_dir.value}({mom_str}), Volatility={vol_dir.value}({vol_str}), "
                f"MeanRev={mr_dir.value}({mr_str})."
            ),
            evidence=[f"composite_score={avg:+.2f}", f"bar_count={n}",
                      f"annualized_vol={annualized_vol:.4f}"],
            provenance=["price_history"],
        ))

        # ------------------------------------------------------------------
        # 7. Confidence & warnings
        # ------------------------------------------------------------------
        data_quality = 0
        warnings: list[str] = []
        if n < 50:
            data_quality -= 10
            warnings.append("Price history has fewer than 50 bars; long-term indicators are unavailable.")
        if n < 20:
            data_quality -= 15
            warnings.append("Price history has fewer than 20 bars; most indicators are unreliable.")
        if annualized_vol > 0.50:
            data_quality -= 8
            warnings.append("Annualized volatility exceeds 50%; signals may be unreliable.")
        if n < 14:
            data_quality -= 5
            warnings.append("RSI computation may be incomplete due to short history.")

        bull_confirmations = sum(1 for s in signals if s.direction == SignalDirection.BULLISH)
        bear_confirmations = sum(1 for s in signals if s.direction == SignalDirection.BEARISH)
        confidence = min(90, max(25, 50 + int(abs(avg) * 1.5) + data_quality))

        summary = (
            f"Technical composite is {composite_dir.value} ({composite_str}); "
            f"{bull_confirmations} bullish / {bear_confirmations} bearish signals. "
            f"Price={latest:.2f}, RSI={rsi_val:.0f}, ATR(14)={atr_14:.4f}."
        )

        return AgentOutput(
            agent_name=self.agent_name,
            display_name=self.display_name,
            version=self.version,
            summary=summary,
            signals=signals,
            confidence=confidence,
            warnings=warnings,
            details={
                "last_close": round(latest, 4),
                "bar_count": n,
                "annualized_volatility": round(annualized_vol, 6),
                "rsi_14": round(rsi_val, 2),
                "macd_histogram": round(macd_hist, 6),
                "trend_score": trend_score,
                "composite_score": round(float(avg), 4),
                "sma_20": round(float(sma_20.iloc[-1]), 4) if has_20 else None,
                "ema_8": round(float(ema_8.iloc[-1]), 4) if n >= 8 else None,
                "ema_21": round(float(ema_21.iloc[-1]), 4) if n >= 21 else None,
                "atr_14": round(atr_14, 6),
                "bb_width": round(bb_width, 6),
                "bb_position": round(bb_position, 4),
                "zscore_20": round(zscore, 4),
                "roc_10d": round(roc_10, 6),
                "roc_21d": round(roc_21, 6),
            },
        )


@dataclass(frozen=True)
class FundamentalAnalyst:
    agent_name: str = "fundamental_analyst"
    display_name: str = "Fundamental Analyst"
    version: str = "v1"

    def run(self, context: MarketResearchContext, previous_outputs: Sequence[AgentOutput]) -> AgentOutput:
        del previous_outputs
        events = context.financial_events
        if not events:
            return AgentOutput(
                agent_name=self.agent_name,
                display_name=self.display_name,
                version=self.version,
                summary="No real fundamental or valuation data was available for this run.",
                signals=[
                    ResearchSignal(
                        label="fundamental_coverage",
                        direction=SignalDirection.MIXED,
                        strength=25,
                        rationale="The committee cannot verify valuation, earnings, revenue, debt, or guidance from configured providers.",
                        provenance=["fundamentals"],
                    )
                ],
                confidence=25,
                warnings=["Fundamental provider coverage is missing or demo-only."],
            )
        directions = [str(event.get("event_direction") or "neutral") for event in events]
        positive = directions.count("positive")
        negative = directions.count("negative")
        if positive > negative:
            direction = SignalDirection.BULLISH
        elif negative > positive:
            direction = SignalDirection.BEARISH
        else:
            direction = SignalDirection.MIXED
        examples = [str(event.get("event_title") or event.get("summary") or event.get("event_type")) for event in events[:3]]
        return AgentOutput(
            agent_name=self.agent_name,
            display_name=self.display_name,
            version=self.version,
            summary=f"Fundamental event evidence is {direction.value} across {len(events)} retrieved row(s).",
            signals=[
                ResearchSignal(
                    label="financial_events",
                    direction=direction,
                    strength=min(80, 35 + len(events) * 8),
                    rationale=f"Retrieved events: {positive} positive, {negative} negative, {len(events) - positive - negative} neutral.",
                    evidence=examples,
                    provenance=["financial_events"],
                )
            ],
            confidence=55,
            details={"event_count": len(events), "positive_events": positive, "negative_events": negative},
        )


@dataclass(frozen=True)
class NewsSentimentAnalyst:
    agent_name: str = "news_sentiment_analyst"
    display_name: str = "News/Sentiment Analyst"
    version: str = "v1"

    def run(self, context: MarketResearchContext, previous_outputs: Sequence[AgentOutput]) -> AgentOutput:
        del previous_outputs
        if not context.news:
            return AgentOutput(
                agent_name=self.agent_name,
                display_name=self.display_name,
                version=self.version,
                summary="No recent news or sentiment rows were available.",
                signals=[
                    ResearchSignal(
                        label="news_coverage",
                        direction=SignalDirection.NEUTRAL,
                        strength=20,
                        rationale="No headlines were returned by configured sources.",
                        provenance=["news"],
                    )
                ],
                confidence=20,
                warnings=["News/sentiment coverage is missing."],
            )
        scored = [item.sentiment_score for item in context.news if item.sentiment_score is not None]
        average = _mean([float(item) for item in scored])
        if average > 0.10:
            direction = SignalDirection.BULLISH
        elif average < -0.10:
            direction = SignalDirection.BEARISH
        else:
            direction = SignalDirection.MIXED
        evidence = [item.headline for item in context.news[:5]]
        return AgentOutput(
            agent_name=self.agent_name,
            display_name=self.display_name,
            version=self.version,
            summary=f"News sentiment is {direction.value}; average scored sentiment is {average:+.2f}.",
            signals=[
                ResearchSignal(
                    label="headline_sentiment",
                    direction=direction,
                    strength=min(85, 35 + int(abs(average) * 120)),
                    rationale=f"{len(context.news)} headline row(s), {len(scored)} with sentiment scores.",
                    evidence=evidence,
                    provenance=["news"],
                )
            ],
            confidence=55 if scored else 35,
            details={"headline_count": len(context.news), "average_sentiment": round(average, 4)},
        )


@dataclass(frozen=True)
class RiskAnalyst:
    agent_name: str = "risk_analyst"
    display_name: str = "Risk Analyst"
    version: str = "v1"

    def run(self, context: MarketResearchContext, previous_outputs: Sequence[AgentOutput]) -> AgentOutput:
        technical = _find_output(previous_outputs, "technical_analyst")
        annualized_vol = float((technical.details if technical else {}).get("annualized_volatility") or 0.0)
        risk_notes = list(context.data_quality_notes)
        if len(context.price_history) < 20:
            risk_notes.append("Price history has fewer than 20 bars.")
        if not context.financial_events:
            risk_notes.append("Fundamental and valuation evidence is incomplete.")
        if not context.news:
            risk_notes.append("News/sentiment evidence is incomplete.")
        high_vol = annualized_vol > 0.35
        direction = SignalDirection.BEARISH if high_vol or len(risk_notes) >= 3 else SignalDirection.NEUTRAL
        strength = min(90, 35 + len(risk_notes) * 10 + (20 if high_vol else 0))
        return AgentOutput(
            agent_name=self.agent_name,
            display_name=self.display_name,
            version=self.version,
            summary="Risk review highlights data quality and volatility caveats.",
            signals=[
                ResearchSignal(
                    label="research_risk",
                    direction=direction,
                    strength=strength,
                    rationale="Risk score reflects volatility, coverage gaps, and explicit data warnings.",
                    evidence=risk_notes[:6],
                    provenance=["risk_review"],
                )
            ],
            confidence=65,
            warnings=risk_notes,
            details={"annualized_volatility": annualized_vol, "risk_note_count": len(risk_notes), "high_volatility": high_vol},
        )


@dataclass(frozen=True)
class BullResearcher:
    agent_name: str = "bull_researcher"
    display_name: str = "Bull Researcher"
    version: str = "v1"

    def run(self, context: MarketResearchContext, previous_outputs: Sequence[AgentOutput]) -> AgentOutput:
        bullish = [signal for signal in _all_signals(previous_outputs) if signal.direction == SignalDirection.BULLISH]
        if bullish:
            thesis = "; ".join(signal.rationale for signal in bullish[:3])
            strength = min(90, int(_mean([signal.strength for signal in bullish])))
        else:
            thesis = "The bullish case is limited; no strong bullish evidence was available from configured sources."
            strength = 25
        return AgentOutput(
            agent_name=self.agent_name,
            display_name=self.display_name,
            version=self.version,
            summary=f"Bull thesis for {context.ticker}: {thesis}",
            signals=[
                ResearchSignal(
                    label="bull_thesis",
                    direction=SignalDirection.BULLISH if bullish else SignalDirection.NEUTRAL,
                    strength=strength,
                    rationale=thesis,
                    evidence=[item for signal in bullish[:3] for item in signal.evidence[:2]],
                    provenance=["analyst_synthesis"],
                )
            ],
            confidence=50 if bullish else 30,
            details={"thesis": thesis},
        )


@dataclass(frozen=True)
class BearResearcher:
    agent_name: str = "bear_researcher"
    display_name: str = "Bear Researcher"
    version: str = "v1"

    def run(self, context: MarketResearchContext, previous_outputs: Sequence[AgentOutput]) -> AgentOutput:
        bearish = [signal for signal in _all_signals(previous_outputs) if signal.direction == SignalDirection.BEARISH]
        warnings = [warning for output in previous_outputs for warning in output.warnings]
        if bearish:
            thesis = "; ".join(signal.rationale for signal in bearish[:3])
            strength = min(90, int(_mean([signal.strength for signal in bearish])))
        elif warnings:
            thesis = "The bearish case rests mainly on data-quality and uncertainty caveats."
            strength = 40
        else:
            thesis = "No strong bearish evidence was available from configured sources."
            strength = 25
        return AgentOutput(
            agent_name=self.agent_name,
            display_name=self.display_name,
            version=self.version,
            summary=f"Bear thesis for {context.ticker}: {thesis}",
            signals=[
                ResearchSignal(
                    label="bear_thesis",
                    direction=SignalDirection.BEARISH if bearish or warnings else SignalDirection.NEUTRAL,
                    strength=strength,
                    rationale=thesis,
                    evidence=[item for signal in bearish[:3] for item in signal.evidence[:2]] or warnings[:4],
                    provenance=["analyst_synthesis"],
                )
            ],
            confidence=50 if bearish or warnings else 30,
            details={"thesis": thesis},
        )


@dataclass(frozen=True)
class TraderSynthesizer:
    agent_name: str = "trader_synthesizer"
    display_name: str = "Trader/Synthesizer"
    version: str = "v1"

    def run(self, context: MarketResearchContext, previous_outputs: Sequence[AgentOutput]) -> AgentOutput:
        signals = _all_signals(previous_outputs)
        score = sum(_direction_score(signal) for signal in signals if signal.label != "research_risk")
        risk = _find_output(previous_outputs, "risk_analyst")
        risk_score = sum(signal.strength for signal in (risk.signals if risk else []) if signal.direction == SignalDirection.BEARISH)
        score -= risk_score * 0.55
        severe_data_gap = len(context.price_history) < 5
        if severe_data_gap:
            decision = ResearchDecision.AVOID
        elif score >= 65:
            decision = ResearchDecision.BUY
        elif score <= -65:
            decision = ResearchDecision.SELL
        else:
            decision = ResearchDecision.HOLD
        confidence = max(20, min(82, 42 + int(abs(score) / 4)))
        rationale = (
            f"Weighted committee score is {score:.1f}; simulated decision is {decision.value}. "
            "This is a research classification, not an order or investment advice."
        )
        return AgentOutput(
            agent_name=self.agent_name,
            display_name=self.display_name,
            version=self.version,
            summary=rationale,
            signals=[
                ResearchSignal(
                    label="simulated_decision",
                    direction=SignalDirection.BULLISH if decision == ResearchDecision.BUY else SignalDirection.BEARISH if decision == ResearchDecision.SELL else SignalDirection.NEUTRAL,
                    strength=confidence,
                    rationale=rationale,
                    provenance=["committee_synthesis"],
                )
            ],
            confidence=confidence,
            warnings=["Simulated decision only; no trade execution was performed."],
            details={"decision": decision.value, "score": round(score, 4), "rationale": rationale},
        )


@dataclass(frozen=True)
class PortfolioRiskManager:
    agent_name: str = "portfolio_risk_manager"
    display_name: str = "Portfolio/Risk Manager"
    version: str = "v1"

    def run(self, context: MarketResearchContext, previous_outputs: Sequence[AgentOutput]) -> AgentOutput:
        trader = _find_output(previous_outputs, "trader_synthesizer")
        risk = _find_output(previous_outputs, "risk_analyst")
        requested = ResearchDecision(str((trader.details if trader else {}).get("decision") or ResearchDecision.HOLD.value))
        confidence = int((trader.confidence if trader else 35) or 35)
        warnings = list(context.warnings)
        if risk:
            warnings.extend(risk.warnings)
        action = "approved"
        final = requested
        if len(context.price_history) < 5:
            final = ResearchDecision.AVOID
            confidence = min(confidence, 25)
            action = "vetoed"
            warnings.append("Portfolio review vetoed the recommendation because price data is insufficient.")
        elif confidence < 45 and requested in {ResearchDecision.BUY, ResearchDecision.SELL}:
            final = ResearchDecision.HOLD
            confidence = 45
            action = "downgraded"
            warnings.append("Portfolio review downgraded a low-confidence directional call to HOLD.")
        elif requested == ResearchDecision.BUY and risk and risk.details.get("high_volatility"):
            final = ResearchDecision.HOLD
            confidence = min(confidence, 55)
            action = "downgraded"
            warnings.append("Portfolio review downgraded BUY to HOLD because volatility risk is elevated.")
        rationale = f"Portfolio review {action} the trader decision: {requested.value} -> {final.value}."
        return AgentOutput(
            agent_name=self.agent_name,
            display_name=self.display_name,
            version=self.version,
            summary=rationale,
            signals=[
                ResearchSignal(
                    label="portfolio_review",
                    direction=SignalDirection.NEUTRAL,
                    strength=confidence,
                    rationale=rationale,
                    evidence=warnings[:6],
                    provenance=["risk_management"],
                )
            ],
            confidence=confidence,
            warnings=list(dict.fromkeys(warnings)),
            details={"decision": final.value, "review_action": action, "prior_decision": requested.value, "rationale": rationale},
        )


DEFAULT_AGENTS: tuple[ResearchAgent, ...] = (
    TechnicalAnalyst(),
    FundamentalAnalyst(),
    NewsSentimentAnalyst(),
    RiskAnalyst(),
    BullResearcher(),
    BearResearcher(),
    TraderSynthesizer(),
    PortfolioRiskManager(),
)


class MarketResearchOrchestrator:
    def __init__(
        self,
        agents: Sequence[ResearchAgent] | None = None,
        *,
        llm_provider: StructuredLLMProvider | None = None,
        per_agent_timeout_seconds: float = 8.0,
        max_llm_failures: int = 1,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.agents = tuple(agents or DEFAULT_AGENTS)
        self.llm_provider = llm_provider or MockStructuredLLMProvider()
        self.per_agent_timeout_seconds = max(0.1, float(per_agent_timeout_seconds))
        self.max_llm_failures = int(max_llm_failures)
        self.progress_callback = progress_callback
        self._state_lock = Lock()
        self._llm_failure_count = 0
        self._cancelled_agent_run_ids: set[str] = set()

    def run(self, context: MarketResearchContext) -> MarketResearchReport:
        outputs: list[AgentOutput] = []
        audit: list[AgentAuditEvent] = []
        for index, agent in enumerate(self.agents):
            output, event = self._run_agent(agent, context, outputs, agent_index=index, total_agents=len(self.agents))
            outputs.append(output)
            audit.append(event)
        return self._build_report(context, outputs, audit)

    def _emit_progress(
        self,
        event_type: str,
        agent: ResearchAgent | None = None,
        *,
        agent_index: int | None = None,
        total_agents: int | None = None,
        agent_run_id: str | None = None,
        **details: Any,
    ) -> None:
        if self.progress_callback is None:
            return
        if agent_run_id is not None and self._is_agent_run_cancelled(agent_run_id):
            return
        payload: dict[str, Any] = {
            "event_type": event_type,
            "timestamp_utc": utc_now_iso(),
            "provider": getattr(self.llm_provider, "provider_name", "unknown"),
            "model": getattr(self.llm_provider, "model_name", "unknown"),
        }
        if agent is not None:
            payload.update(
                {
                    "agent_name": agent.agent_name,
                    "display_name": agent.display_name,
                    "agent_version": getattr(agent, "version", "v1"),
                }
            )
        if agent_index is not None:
            payload["agent_index"] = int(agent_index)
        if total_agents is not None:
            payload["total_agents"] = int(total_agents)
        payload.update(details)
        self.progress_callback(payload)

    def _is_agent_run_cancelled(self, agent_run_id: str) -> bool:
        with self._state_lock:
            return agent_run_id in self._cancelled_agent_run_ids

    def _cancel_agent_run(self, agent_run_id: str) -> None:
        with self._state_lock:
            self._cancelled_agent_run_ids.add(agent_run_id)

    def _record_llm_failure(self) -> int:
        with self._state_lock:
            self._llm_failure_count += 1
            return self._llm_failure_count

    def _llm_failure_limit_reached(self) -> tuple[bool, int]:
        if self.max_llm_failures <= 0:
            with self._state_lock:
                return False, self._llm_failure_count
        with self._state_lock:
            count = self._llm_failure_count
        return count >= self.max_llm_failures, count

    def _run_agent(
        self,
        agent: ResearchAgent,
        context: MarketResearchContext,
        previous_outputs: Sequence[AgentOutput],
        *,
        agent_index: int,
        total_agents: int,
    ) -> tuple[AgentOutput, AgentAuditEvent]:
        started = utc_now_iso()
        start_time = time.perf_counter()
        agent_run_id = f"{agent.agent_name}:{time.perf_counter_ns()}"
        self._emit_progress("agent_started", agent, agent_index=agent_index, total_agents=total_agents)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"market-research-{agent.agent_name}")
        future: Future[AgentOutput] = executor.submit(
            self._run_agent_body,
            agent,
            context,
            list(previous_outputs),
            agent_index=agent_index,
            total_agents=total_agents,
            agent_run_id=agent_run_id,
        )
        try:
            output = future.result(timeout=self.per_agent_timeout_seconds)
            status = AgentStatus.COMPLETED
            error = None
            executor.shutdown(wait=True)
        except TimeoutError:
            future.cancel()
            self._cancel_agent_run(agent_run_id)
            executor.shutdown(wait=False, cancel_futures=True)
            status = AgentStatus.TIMEOUT
            error = (
                f"{agent.display_name} timed out after {self.per_agent_timeout_seconds:.1f}s while using "
                f"{self._provider_label()}."
            )
            if self._uses_hosted_llm():
                self._record_llm_failure()
            self._emit_progress(
                "agent_timeout",
                agent,
                agent_index=agent_index,
                total_agents=total_agents,
                error=error,
            )
            output = self._timeout_fallback_output(agent, context, previous_outputs, error)
        except Exception as exc:
            executor.shutdown(wait=False, cancel_futures=True)
            status = AgentStatus.FAILED
            error = str(exc)
            self._emit_progress(
                "agent_failed",
                agent,
                agent_index=agent_index,
                total_agents=total_agents,
                error=error,
            )
            output = self._failed_output(agent, error)
        finished = utc_now_iso()
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        audit = AgentAuditEvent(
            agent_name=agent.agent_name,
            display_name=agent.display_name,
            status=status,
            started_at_utc=started,
            finished_at_utc=finished,
            duration_ms=duration_ms,
            warnings=output.warnings,
            error=error,
        )
        self._emit_progress(
            "agent_completed",
            agent,
            agent_index=agent_index,
            total_agents=total_agents,
            status=status,
            duration_ms=duration_ms,
            confidence=output.confidence,
            signal_count=len(output.signals),
            warning_count=len(output.warnings),
        )
        return output, audit

    def _run_agent_body(
        self,
        agent: ResearchAgent,
        context: MarketResearchContext,
        previous_outputs: Sequence[AgentOutput],
        *,
        agent_index: int,
        total_agents: int,
        agent_run_id: str,
    ) -> AgentOutput:
        self._emit_progress(
            "deterministic_baseline_started",
            agent,
            agent_index=agent_index,
            total_agents=total_agents,
            agent_run_id=agent_run_id,
        )
        deterministic = agent.run(context, previous_outputs)
        self._emit_progress(
            "deterministic_baseline_completed",
            agent,
            agent_index=agent_index,
            total_agents=total_agents,
            agent_run_id=agent_run_id,
            confidence=deterministic.confidence,
            signal_count=len(deterministic.signals),
            warning_count=len(deterministic.warnings),
        )
        if not self._uses_hosted_llm():
            return deterministic
        limit_reached, failure_count = self._llm_failure_limit_reached()
        if limit_reached:
            warning = (
                f"Hosted LLM refinement skipped for {agent.display_name} after "
                f"{failure_count} prior provider failure(s); deterministic baseline used."
            )
            self._emit_progress(
                "llm_refinement_skipped",
                agent,
                agent_index=agent_index,
                total_agents=total_agents,
                agent_run_id=agent_run_id,
                error=warning,
                warning_count=len(deterministic.warnings) + 1,
            )
            return deterministic.model_copy(
                update={
                    "warnings": list(dict.fromkeys([*deterministic.warnings, warning])),
                    "details": {
                        **deterministic.details,
                        "llm_provider": getattr(self.llm_provider, "provider_name", "unknown"),
                        "llm_model": getattr(self.llm_provider, "model_name", "unknown"),
                        "llm_skipped_after_failures": failure_count,
                        "fallback_type": "deterministic_after_llm_fail_fast",
                    },
                }
            )
        return self._augment_with_llm(
            agent,
            context,
            previous_outputs,
            deterministic,
            agent_index=agent_index,
            total_agents=total_agents,
            agent_run_id=agent_run_id,
        )

    def _uses_hosted_llm(self) -> bool:
        provider_name = str(getattr(self.llm_provider, "provider_name", "mock")).lower()
        return provider_name not in {"mock", "disabled", "none"}

    def _provider_label(self) -> str:
        provider = getattr(self.llm_provider, "provider_name", "unknown")
        model = getattr(self.llm_provider, "model_name", "unknown")
        return f"{provider}/{model}"

    def _augment_with_llm(
        self,
        agent: ResearchAgent,
        context: MarketResearchContext,
        previous_outputs: Sequence[AgentOutput],
        deterministic: AgentOutput,
        *,
        agent_index: int,
        total_agents: int,
        agent_run_id: str,
    ) -> AgentOutput:
        prompt = self._agent_prompt(agent, context, previous_outputs, deterministic)
        try:
            self._emit_progress(
                "llm_refinement_started",
                agent,
                agent_index=agent_index,
                total_agents=total_agents,
                agent_run_id=agent_run_id,
            )
            result = self.llm_provider.generate_structured(
                prompt,
                AgentOutput,
                {
                    "system": (
                        "You are a research-only market analysis agent. Use only the verified context and "
                        "explicitly label inference. Do not provide personalized financial advice."
                    ),
                    "temperature": 0.2,
                    "max_output_tokens": 1600,
                },
            )
            llm_output = self._coerce_llm_result(result)
            llm_metadata = {}
            if isinstance(result, LLMCallResult):
                llm_metadata = {
                    "llm_latency_ms": result.latency_ms,
                    "llm_usage": result.usage,
                    "llm_response_metadata": result.metadata,
                    "llm_warnings": result.warnings,
                }
                self._emit_progress(
                    "llm_refinement_completed",
                    agent,
                    agent_index=agent_index,
                    total_agents=total_agents,
                    agent_run_id=agent_run_id,
                    latency_ms=result.latency_ms,
                    usage=result.usage,
                    warning_count=len(result.warnings),
                )
            else:
                self._emit_progress(
                    "llm_refinement_completed",
                    agent,
                    agent_index=agent_index,
                    total_agents=total_agents,
                    agent_run_id=agent_run_id,
                    warning_count=0,
                )
            llm_output = llm_output.model_copy(
                update={
                    "agent_name": agent.agent_name,
                    "display_name": agent.display_name,
                    "version": getattr(agent, "version", "v1"),
                    "prompt_version": PROMPT_VERSION,
                    "details": {
                        **deterministic.details,
                        **llm_output.details,
                        "llm_provider": getattr(self.llm_provider, "provider_name", "unknown"),
                        "llm_model": getattr(self.llm_provider, "model_name", "unknown"),
                        "deterministic_baseline": deterministic.summary,
                        **llm_metadata,
                    },
                }
            )
            if not llm_output.signals and deterministic.signals:
                llm_output = llm_output.model_copy(update={"signals": deterministic.signals})
            return llm_output
        except Exception as exc:
            if self._is_agent_run_cancelled(agent_run_id):
                return deterministic
            provider_name = str(getattr(self.llm_provider, "provider_name", "unknown")).lower()
            provider_label = "Ollama" if provider_name == "ollama" else "Hosted"
            failure_count = self._record_llm_failure()
            self._emit_progress(
                "llm_refinement_failed",
                agent,
                agent_index=agent_index,
                total_agents=total_agents,
                agent_run_id=agent_run_id,
                error=str(exc),
            )
            warnings = list(deterministic.warnings)
            warnings.append(f"{provider_label} LLM refinement failed for {agent.display_name}; deterministic fallback used: {exc}")
            limit_reached, _ = self._llm_failure_limit_reached()
            if limit_reached:
                warnings.append(
                    f"Hosted LLM refinement disabled for remaining agents after {failure_count} provider failure(s)."
                )
            return deterministic.model_copy(
                update={
                    "warnings": list(dict.fromkeys(warnings)),
                    "details": {
                        **deterministic.details,
                        "llm_provider": getattr(self.llm_provider, "provider_name", "unknown"),
                        "llm_model": getattr(self.llm_provider, "model_name", "unknown"),
                        "llm_error": str(exc),
                        "fallback_type": "deterministic_after_llm_error",
                    },
                }
            )

    @staticmethod
    def _coerce_llm_result(result: Any) -> AgentOutput:
        if isinstance(result, LLMCallResult):
            return result.value
        if isinstance(result, AgentOutput):
            return result
        return AgentOutput.model_validate(result)

    @staticmethod
    def _bounded_context(context: MarketResearchContext) -> dict[str, Any]:
        payload = context.model_dump(mode="json")
        payload["price_history"] = payload.get("price_history", [])[-80:]
        payload["news"] = payload.get("news", [])[:30]
        payload["financial_events"] = payload.get("financial_events", [])[:30]
        payload["sentiment_matrix"] = payload.get("sentiment_matrix", [])[-90:]
        payload["source_references"] = payload.get("source_references", [])[:50]
        return payload

    def _agent_prompt(
        self,
        agent: ResearchAgent,
        context: MarketResearchContext,
        previous_outputs: Sequence[AgentOutput],
        deterministic: AgentOutput,
    ) -> str:
        prompt = AGENT_PROMPTS.get(agent.agent_name, "Review the context and produce a schema-valid research output.")
        payload = {
            "agent": {"name": agent.agent_name, "display_name": agent.display_name},
            "instructions": prompt,
            "disclaimer": RESEARCH_DISCLAIMER,
            "research_boundary": "Do not place trades, promise returns, or provide personalized investment advice.",
            "context": self._bounded_context(context),
            "previous_outputs": [output.model_dump(mode="json") for output in previous_outputs[-6:]],
            "deterministic_baseline": deterministic.model_dump(mode="json"),
            "output_contract": (
                "Return an AgentOutput. Use evidence/provenance ids from context where possible. "
                "If data is missing, add warnings instead of inventing facts."
            ),
        }
        return json.dumps(payload, default=str, sort_keys=True)

    @staticmethod
    def _failed_output(agent: ResearchAgent, error: str) -> AgentOutput:
        return AgentOutput(
            agent_name=agent.agent_name,
            display_name=agent.display_name,
            version=getattr(agent, "version", "v1"),
            summary=f"{agent.display_name} could not complete. The orchestrator continued with remaining agents.",
            confidence=0,
            warnings=[error],
            details={"error": error, "fallback_type": "agent_failed"},
        )

    def _timeout_fallback_output(
        self,
        agent: ResearchAgent,
        context: MarketResearchContext,
        previous_outputs: Sequence[AgentOutput],
        error: str,
    ) -> AgentOutput:
        try:
            deterministic = agent.run(context, previous_outputs)
        except Exception:
            return self._failed_output(agent, error)
        warnings = list(deterministic.warnings)
        warnings.append(f"{error} Deterministic fallback was used where possible.")
        return deterministic.model_copy(
            update={
                "warnings": list(dict.fromkeys(warnings)),
                "details": {
                    **deterministic.details,
                    "llm_provider": getattr(self.llm_provider, "provider_name", "unknown"),
                    "llm_model": getattr(self.llm_provider, "model_name", "unknown"),
                    "llm_timeout": True,
                    "fallback_type": "deterministic_after_agent_timeout",
                },
            }
        )

    def _build_report(
        self,
        context: MarketResearchContext,
        outputs: Sequence[AgentOutput],
        audit: Sequence[AgentAuditEvent],
    ) -> MarketResearchReport:
        technical = _find_output(outputs, "technical_analyst") or self._placeholder("technical_analyst", "Technical Analyst")
        fundamental = _find_output(outputs, "fundamental_analyst") or self._placeholder("fundamental_analyst", "Fundamental Analyst")
        news = _find_output(outputs, "news_sentiment_analyst") or self._placeholder("news_sentiment_analyst", "News/Sentiment Analyst")
        risk = _find_output(outputs, "risk_analyst") or self._placeholder("risk_analyst", "Risk Analyst")
        bull = _find_output(outputs, "bull_researcher") or self._placeholder("bull_researcher", "Bull Researcher")
        bear = _find_output(outputs, "bear_researcher") or self._placeholder("bear_researcher", "Bear Researcher")
        trader = _find_output(outputs, "trader_synthesizer")
        manager = _find_output(outputs, "portfolio_risk_manager")
        decision_value = (manager.details if manager else {}).get("decision") or (trader.details if trader else {}).get("decision") or ResearchDecision.HOLD.value
        confidence = int((manager.confidence if manager else trader.confidence if trader else 35) or 35)
        decision = ResearchDecision(str(decision_value))
        warnings = list(context.warnings)
        for output in outputs:
            warnings.extend(output.warnings)
        for event in audit:
            if event.status != AgentStatus.COMPLETED and event.error:
                warnings.append(event.error)
        provider_name = str(getattr(self.llm_provider, "provider_name", "unknown")).lower()
        if provider_name == "mock":
            warnings.append("Mock LLM provider used; no local or hosted model was called.")
        elif provider_name == "disabled":
            warnings.append("Market research LLM provider is disabled; deterministic agent outputs were used.")
        fallback_agents = [
            output.agent_name
            for output in outputs
            if output.details.get("llm_error") or output.details.get("llm_timeout")
        ]
        if provider_name == "ollama" and fallback_agents:
            warnings.append(
                "Ollama provider failed or timed out for "
                + ", ".join(fallback_agents)
                + "; deterministic fallback was used where possible."
            )
        data_quality_notes = list(dict.fromkeys([*context.data_quality_notes, *risk.warnings]))
        summary = (
            f"{context.ticker} committee report for {context.analysis_date}: {decision.value} "
            f"with {confidence}/100 confidence over a {context.horizon} horizon. {RESEARCH_DISCLAIMER}"
        )
        metadata = {
            "prompt_version": PROMPT_VERSION,
            "agent_prompt_hashes": {
                name: sha256(prompt.encode("utf-8")).hexdigest()
                for name, prompt in AGENT_PROMPTS.items()
            },
            "llm_provider": getattr(self.llm_provider, "provider_name", "unknown"),
            "llm_model": getattr(self.llm_provider, "model_name", "unknown"),
            "llm_agent_latency_ms": {
                output.agent_name: output.details.get("llm_latency_ms")
                for output in outputs
                if output.details.get("llm_latency_ms") is not None
            },
            "llm_agent_usage": {
                output.agent_name: output.details.get("llm_usage")
                for output in outputs
                if output.details.get("llm_usage")
            },
            "llm_fallback_agents": fallback_agents,
            "agent_statuses": {event.agent_name: str(event.status) for event in audit},
            "agent_versions": {output.agent_name: output.version for output in outputs},
            "provider_metadata": context.provider_metadata,
            "trade_execution": "disabled",
        }
        return MarketResearchReport(
            ticker=context.ticker,
            analysis_date=context.analysis_date,
            decision=decision,
            confidence=confidence,
            time_horizon=context.horizon,
            summary=summary,
            bull_thesis=str(bull.details.get("thesis") or bull.summary),
            bear_thesis=str(bear.details.get("thesis") or bear.summary),
            technical_signals=technical.signals,
            fundamental_signals=fundamental.signals,
            news_sentiment_signals=news.signals,
            risk_assessment=risk,
            data_quality_notes=data_quality_notes or ["No data-quality warnings were recorded."],
            sentiment_matrix=context.sentiment_matrix,
            sentiment_analysis=context.sentiment_analysis,
            financial_events_matrix=context.financial_events,
            financial_events_analysis=context.financial_events_analysis,
            source_references=context.source_references,
            data_freshness=context.data_freshness,
            confidence_levels=context.confidence_levels,
            missing_data_indicators=context.missing_data_indicators,
            raw_agent_outputs=list(outputs),
            audit_trail=list(audit),
            provenance=context.provenance,
            warnings=list(dict.fromkeys(warnings)),
            metadata=metadata,
        )

    @staticmethod
    def _placeholder(agent_name: str, display_name: str) -> AgentOutput:
        return AgentOutput(
            agent_name=agent_name,
            display_name=display_name,
            summary=f"{display_name} did not produce an output.",
            confidence=0,
            warnings=[f"{display_name} output missing from audit trail."],
        )
