from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from .market_research_agents import MarketResearchContext, MarketResearchReport, PriceBar, ResearchSignal


class DataQualityValidator:
    def validate(self, context: MarketResearchContext) -> dict[str, Any]:
        scores: dict[str, float] = {}
        warnings: list[str] = []
        metrics: dict[str, Any] = {}

        n_bars = len(context.price_history)
        if n_bars >= 500:
            scores["price_history"] = 1.0
        elif n_bars >= 252:
            scores["price_history"] = 0.9
        elif n_bars >= 100:
            scores["price_history"] = 0.7
        elif n_bars >= 50:
            scores["price_history"] = 0.5
        elif n_bars >= 20:
            scores["price_history"] = 0.3
        else:
            scores["price_history"] = 0.1
            warnings.append(f"Price history too short ({n_bars} bars)")

        if n_bars > 0:
            closes = [b.close for b in context.price_history if math.isfinite(b.close) and b.close > 0]
            if closes:
                returns = np.diff(np.log(closes))
                nan_ratio = float((~np.isfinite(returns)).mean()) if len(returns) > 0 else 0
                scores["data_continuity"] = max(0.0, 1.0 - nan_ratio * 5)
                if nan_ratio > 0.1:
                    warnings.append(f"High ratio of NaN/inf values in returns ({nan_ratio:.1%})")
                metrics["return_nan_ratio"] = round(nan_ratio, 4)

                if len(closes) > 1:
                    daily_vol = float(np.std(returns))
                    annual_vol = daily_vol * math.sqrt(252)
                    metrics["daily_volatility"] = round(daily_vol, 6)
                    metrics["annualized_volatility"] = round(annual_vol, 6)
                    if annual_vol > 0.8:
                        scores["volatility"] = 0.3
                        warnings.append(f"Very high annualized volatility ({annual_vol:.1%})")
                    elif annual_vol > 0.4:
                        scores["volatility"] = 0.6
                    else:
                        scores["volatility"] = 1.0
                else:
                    scores["volatility"] = 0.5
            else:
                scores["data_continuity"] = 0.0
                scores["volatility"] = 0.0
        else:
            scores["data_continuity"] = 0.0
            scores["volatility"] = 0.0

        has_news = len(context.news) > 0 and any(n.sentiment_score is not None for n in context.news)
        has_fundamentals = context.fundamentals is not None or len(context.financial_events) > 0
        has_sentiment = len(context.sentiment_matrix) > 0

        scores["news_coverage"] = 1.0 if has_news else 0.2
        scores["fundamental_coverage"] = 1.0 if has_fundamentals else 0.2
        scores["sentiment_coverage"] = 1.0 if has_sentiment else 0.2

        if not has_news:
            warnings.append("No news/sentiment coverage available")
        if not has_fundamentals:
            warnings.append("No fundamental event coverage available")

        overall = sum(scores.values()) / max(len(scores), 1)
        metrics["component_scores"] = scores
        metrics["overall_quality_score"] = round(overall, 4)
        metrics["warning_count"] = len(warnings)

        return {
            "overall_quality_score": round(overall, 4),
            "component_scores": scores,
            "warnings": warnings,
            "metrics": metrics,
        }

    def validate_report(self, report: MarketResearchReport) -> dict[str, Any]:
        warnings: list[str] = []
        scores: dict[str, float] = {}

        if not report.technical_signals:
            scores["technical"] = 0.0
            warnings.append("No technical signals produced")
        else:
            scores["technical"] = 1.0

        if not report.fundamental_signals:
            scores["fundamental"] = 0.0
            warnings.append("No fundamental signals produced")
        else:
            scores["fundamental"] = 1.0

        if not report.news_sentiment_signals:
            scores["sentiment"] = 0.0
            warnings.append("No news/sentiment signals produced")
        else:
            scores["sentiment"] = 1.0

        agent_completion = sum(1 for a in report.audit_trail if a.status == "completed")
        total_agents = len(report.audit_trail)
        scores["agent_completion"] = agent_completion / max(total_agents, 1)

        if report.data_quality_notes:
            scores["data_quality_notes"] = max(0.0, 1.0 - len(report.data_quality_notes) * 0.15)
        else:
            scores["data_quality_notes"] = 1.0

        overall = sum(scores.values()) / max(len(scores), 1)
        return {
            "overall_quality_score": round(overall, 4),
            "component_scores": scores,
            "warnings": warnings,
            "agent_completion_rate": f"{agent_completion}/{total_agents}",
        }


class DecisionEvaluationMetrics:
    def evaluate(self, report: MarketResearchReport, context: MarketResearchContext | None = None) -> dict[str, Any]:
        metrics: dict[str, Any] = {}

        all_signals: list[ResearchSignal] = []
        for output in report.raw_agent_outputs:
            all_signals.extend(output.signals)

        signal_strengths = [s.strength for s in all_signals if s.direction in ("bullish", "bearish")]
        if signal_strengths:
            metrics["avg_signal_strength"] = round(float(np.mean(signal_strengths)), 2)
            metrics["max_signal_strength"] = max(signal_strengths)
            metrics["min_signal_strength"] = min(signal_strengths)
        else:
            metrics["avg_signal_strength"] = 0
            metrics["max_signal_strength"] = 0
            metrics["min_signal_strength"] = 0

        bullish = sum(1 for s in all_signals if s.direction == "bullish")
        bearish = sum(1 for s in all_signals if s.direction == "bearish")
        neutral = sum(1 for s in all_signals if s.direction in ("neutral", "mixed"))
        total = len(all_signals)
        metrics["signal_breakdown"] = {
            "bullish": bullish,
            "bearish": bearish,
            "neutral": neutral,
            "total": total,
        }
        metrics["bullish_ratio"] = round(bullish / max(total, 1), 4)
        metrics["bearish_ratio"] = round(bearish / max(total, 1), 4)

        agent_confidences = [o.confidence for o in report.raw_agent_outputs]
        if agent_confidences:
            metrics["avg_agent_confidence"] = round(float(np.mean(agent_confidences)), 2)
            metrics["min_agent_confidence"] = min(agent_confidences)
            metrics["max_agent_confidence"] = max(agent_confidences)
            metrics["confidence_std"] = round(float(np.std(agent_confidences)), 2)
        else:
            metrics["avg_agent_confidence"] = 0

        completed = sum(1 for a in report.audit_trail if a.status == "completed")
        failed = sum(1 for a in report.audit_trail if a.status in ("failed", "timeout"))
        metrics["agent_reliability"] = {
            "completed": completed,
            "failed": failed,
            "total": len(report.audit_trail),
            "completion_rate": round(completed / max(len(report.audit_trail), 1), 4),
        }

        if context and context.price_history:
            closes = [b.close for b in context.price_history if math.isfinite(b.close) and b.close > 0]
            if len(closes) > 20:
                returns = np.diff(np.log(closes))
                volatility = float(np.std(returns))
                sharpe = float(np.mean(returns) / volatility * math.sqrt(252)) if volatility > 0 else 0
                metrics["historical_sharpe_ratio"] = round(sharpe, 4)
                max_dd = self._max_drawdown(closes)
                metrics["historical_max_drawdown"] = round(max_dd, 4)
                annual_return = float(np.exp(np.mean(returns) * 252) - 1)
                metrics["historical_annualized_return"] = round(annual_return, 4)
                metrics["calmar_ratio"] = round(annual_return / abs(max_dd), 4) if max_dd != 0 else 0

        metrics["decision_confidence"] = report.confidence
        metrics["decision"] = report.decision

        return metrics

    @staticmethod
    def _max_drawdown(prices: Sequence[float]) -> float:
        if not prices:
            return 0.0
        peak = float(prices[0])
        max_dd = 0.0
        for p in prices:
            if p > peak:
                peak = p
            dd = (p - peak) / peak
            if dd < max_dd:
                max_dd = dd
        return max_dd
