from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np

from .sentiment import (
    BaseSentimentModel,
    EnsembleSentimentModel,
    FinBERTSentimentModel,
    RuleBasedFinancialSentimentModel,
    VaderSentimentModel,
)

logger = logging.getLogger(__name__)

CLASSES = ["positive", "negative", "neutral"]

DEFAULT_MODEL_TYPES = ["finbert", "vader", "rule_based", "ensemble"]


@dataclass
class ModelResult:
    model_name: str
    accuracy: float | None
    precision: dict[str, float] | None
    recall: dict[str, float] | None
    f1: dict[str, float] | None
    macro_precision: float | None
    macro_recall: float | None
    macro_f1: float | None
    confusion_matrix: dict[str, dict[str, float]] | None
    timing_ms: float | None = None
    error: str | None = None


@dataclass
class EvaluationReport:
    dataset: str
    dataset_size: int
    label_distribution: dict[str, int]
    evaluated_at: str
    models: list[ModelResult] = field(default_factory=list)


MODEL_BUILDERS: dict[str, tuple[str, Any]] = {
    "finbert": ("FinBERT", lambda: FinBERTSentimentModel(local_files_only=False)),
    "vader": ("VADER", lambda: VaderSentimentModel()),
    "rule_based": ("RuleBased", lambda: RuleBasedFinancialSentimentModel()),
    "ensemble": (
        "Ensemble (FinBERT+VADER)",
        lambda: EnsembleSentimentModel(
            primary=FinBERTSentimentModel(local_files_only=False),
            secondary=VaderSentimentModel(),
            primary_weight=0.8,
        ),
    ),
}


def _build_model(model_type: str) -> BaseSentimentModel:
    entry = MODEL_BUILDERS.get(model_type)
    if entry is None:
        raise ValueError(f"Unknown model type '{model_type}'. Available: {sorted(MODEL_BUILDERS)}")
    return entry[1]()


def _model_name(model_type: str) -> str:
    return MODEL_BUILDERS[model_type][0]


def compute_metrics(y_true: list[str], y_pred: list[str]) -> dict[str, Any]:
    labels = CLASSES
    yt = np.array(y_true)
    yp = np.array(y_pred)

    accuracy = float(np.mean(yt == yp))

    precision: dict[str, float] = {}
    recall: dict[str, float] = {}
    f1: dict[str, float] = {}

    for label in labels:
        tp = float(np.sum((yp == label) & (yt == label)))
        fp = float(np.sum((yp == label) & (yt != label)))
        fn = float(np.sum((yp != label) & (yt == label)))
        precision[label] = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall[label] = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1[label] = (
            2 * precision[label] * recall[label] / (precision[label] + recall[label])
            if (precision[label] + recall[label]) > 0
            else 0.0
        )

    confusion: dict[str, dict[str, float]] = {}
    for true_label in labels:
        confusion[true_label] = {}
        for pred_label in labels:
            confusion[true_label][pred_label] = float(np.sum((yt == true_label) & (yp == pred_label)))

    return {
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "macro_precision": float(np.mean(list(precision.values()))),
        "macro_recall": float(np.mean(list(recall.values()))),
        "macro_f1": float(np.mean(list(f1.values()))),
        "confusion_matrix": confusion,
    }


def evaluate_model(
    model_type: str,
    texts: list[str],
    labels: list[str],
) -> ModelResult:
    model_name = _model_name(model_type)
    try:
        model = _build_model(model_type)
        t0 = time.perf_counter()
        scores = model.score_texts(texts)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        y_pred = scores["label"].tolist()
        metrics = compute_metrics(labels, y_pred)
        return ModelResult(model_name=model_name, timing_ms=round(elapsed_ms, 1), **metrics)
    except Exception as exc:
        logger.warning("Model %s failed: %s", model_type, exc)
        return ModelResult(
            model_name=model_name,
            accuracy=None,
            precision=None,
            recall=None,
            f1=None,
            macro_precision=None,
            macro_recall=None,
            macro_f1=None,
            confusion_matrix=None,
            error=str(exc),
        )


def run_evaluation(
    dataset_name: str = "financial_phrasebank",
    max_samples: int | None = 500,
    model_types: list[str] | None = None,
) -> EvaluationReport:
    from .datasets import load_dataset

    df = load_dataset(dataset_name, max_samples=max_samples)
    texts = df["text"].tolist()
    labels = df["label"].tolist()

    unique = {str(k): int(v) for k, v in df["label"].value_counts().to_dict().items()}

    models_to_run = model_types or list(MODEL_BUILDERS.keys())
    results: list[ModelResult] = []
    for mt in models_to_run:
        results.append(evaluate_model(mt, texts, labels))

    spec_name = dataset_name
    try:
        from .datasets import get_dataset

        spec_name = get_dataset(dataset_name).name
    except KeyError:
        pass

    return EvaluationReport(
        dataset=spec_name,
        dataset_size=len(df),
        label_distribution=unique,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        models=results,
    )


def report_to_dict(report: EvaluationReport) -> dict[str, Any]:
    return asdict(report)


def report_to_json(report: EvaluationReport, indent: int = 2) -> str:
    import json

    return json.dumps(asdict(report), indent=indent)


def report_to_markdown(report: EvaluationReport) -> str:
    lines = [
        f"# Sentiment Model Evaluation: {report.dataset}",
        "",
        f"- **Samples**: {report.dataset_size}",
        f"- **Label distribution**: {report.label_distribution}",
        f"- **Evaluated at**: {report.evaluated_at}",
        "",
        "## Results",
        "",
        "| Model | Accuracy | Precision (macro) | Recall (macro) | F1 (macro) | Timing (ms) |",
        "|-------|----------|-------------------|----------------|------------|-------------|",
    ]
    for m in report.models:
        if m.error:
            lines.append(f"| {m.model_name} | ERROR: {m.error} | — | — | — | — |")
        else:
            acc = f"{m.accuracy:.1%}" if m.accuracy is not None else "—"
            mp = f"{m.macro_precision:.1%}" if m.macro_precision is not None else "—"
            mr = f"{m.macro_recall:.1%}" if m.macro_recall is not None else "—"
            mf = f"{m.macro_f1:.1%}" if m.macro_f1 is not None else "—"
            tm = f"{m.timing_ms:.0f}" if m.timing_ms is not None else "—"
            lines.append(f"| {m.model_name} | {acc} | {mp} | {mr} | {mf} | {tm} |")

    lines.extend(["", "### Per-class F1", "", "| Model | Positive | Negative | Neutral |", "|-------|----------|----------|---------|"])
    for m in report.models:
        if m.error or m.f1 is None:
            continue
        pos = f"{m.f1.get('positive', 0):.1%}"
        neg = f"{m.f1.get('negative', 0):.1%}"
        neu = f"{m.f1.get('neutral', 0):.1%}"
        lines.append(f"| {m.model_name} | {pos} | {neg} | {neu} |")

    return "\n".join(lines)
