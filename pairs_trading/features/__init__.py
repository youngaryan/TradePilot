"""Feature overlays and alternative data transforms."""

from .datasets import DatasetSpec, get_dataset, list_datasets, load_dataset
from .evaluator import (
    EvaluationReport,
    ModelResult,
    evaluate_model,
    report_to_dict,
    report_to_json,
    report_to_markdown,
    run_evaluation,
)
from .lm_dict import LoughranMcDonaldScorer
from .regime_overlay import RegimeOverlayConfig, apply_regime_overlay, build_regime_overlay
from .sentiment import (
    BaseSentimentModel,
    EnsembleSentimentModel,
    FinBERTSentimentModel,
    NewsSentimentAggregator,
    RuleBasedFinancialSentimentModel,
    SentimentConfig,
    VaderSentimentModel,
    adjust_pair_rankings_with_sentiment,
    apply_sentiment_overlay,
    build_best_available_sentiment_model,
    build_pair_sentiment_overlay,
)

__all__ = [
    "BaseSentimentModel",
    "DatasetSpec",
    "EnsembleSentimentModel",
    "EvaluationReport",
    "FinBERTSentimentModel",
    "ModelResult",
    "NewsSentimentAggregator",
    "RuleBasedFinancialSentimentModel",
    "SentimentConfig",
    "VaderSentimentModel",
    "adjust_pair_rankings_with_sentiment",
    "apply_sentiment_overlay",
    "build_best_available_sentiment_model",
    "build_pair_sentiment_overlay",
    "evaluate_model",
    "get_dataset",
    "list_datasets",
    "load_dataset",
    "LoughranMcDonaldScorer",
    "RegimeOverlayConfig",
    "apply_regime_overlay",
    "build_regime_overlay",
    "report_to_dict",
    "report_to_json",
    "report_to_markdown",
    "run_evaluation",
]
