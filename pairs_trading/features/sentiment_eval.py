from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

CLASSES = ["positive", "negative", "neutral"]


def run_sentiment_evaluation(
    max_samples: int | None = 500,
    dataset: str = "financial_phrasebank",
    model_types: list[str] | None = None,
) -> dict[str, Any]:
    from .datasets import list_datasets
    from .evaluator import MODEL_BUILDERS, report_to_dict, run_evaluation

    if model_types is None:
        model_types = list(MODEL_BUILDERS.keys())

    report = run_evaluation(
        dataset_name=dataset,
        max_samples=max_samples,
        model_types=model_types,
    )
    return report_to_dict(report)


def list_available_datasets() -> list[dict[str, Any]]:
    from .datasets import REGISTRY

    return [
        {
            "name": ds.name,
            "key": key,
            "description": ds.description,
            "max_samples_default": ds.max_samples_default,
        }
        for key, ds in REGISTRY.items()
    ]


def list_available_models() -> list[dict[str, Any]]:
    from .evaluator import MODEL_BUILDERS

    return [
        {"name": name, "type": model_type, "default": True}
        for model_type, (name, _) in MODEL_BUILDERS.items()
    ]
