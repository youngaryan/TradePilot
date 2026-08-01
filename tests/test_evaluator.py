from __future__ import annotations

import pytest

from pairs_trading.features.evaluator import (
    MODEL_BUILDERS,
    EvaluationReport,
    ModelResult,
    compute_metrics,
    evaluate_model,
    report_to_dict,
    report_to_json,
    report_to_markdown,
    run_evaluation,
)


class TestComputeMetrics:
    def test_perfect_prediction(self):
        y_true = ["positive", "negative", "neutral", "positive"]
        y_pred = ["positive", "negative", "neutral", "positive"]
        m = compute_metrics(y_true, y_pred)
        assert m["accuracy"] == 1.0
        assert m["macro_f1"] == 1.0

    def test_all_wrong(self):
        y_true = ["positive", "positive", "positive"]
        y_pred = ["negative", "negative", "negative"]
        m = compute_metrics(y_true, y_pred)
        assert m["accuracy"] == 0.0

    def test_confusion_matrix_structure(self):
        y_true = ["positive", "negative", "neutral"]
        y_pred = ["positive", "negative", "neutral"]
        m = compute_metrics(y_true, y_pred)
        cm = m["confusion_matrix"]
        for t in ["positive", "negative", "neutral"]:
            for p in ["positive", "negative", "neutral"]:
                assert t in cm
                assert p in cm[t]

    def test_edge_no_samples_of_class(self):
        y_true = ["positive", "positive"]
        y_pred = ["positive", "negative"]
        m = compute_metrics(y_true, y_pred)
        assert m["precision"]["negative"] == 0.0
        assert m["recall"]["negative"] == 0.0


class TestModelResult:
    def test_dataclass_defaults(self):
        r = ModelResult(
            model_name="Test",
            accuracy=0.5,
            precision={"positive": 0.5},
            recall={"positive": 0.5},
            f1={"positive": 0.5},
            macro_precision=0.5,
            macro_recall=0.5,
            macro_f1=0.5,
            confusion_matrix={"positive": {"positive": 1.0}},
            timing_ms=100.5,
        )
        assert r.model_name == "Test"
        assert r.timing_ms == 100.5
        assert r.error is None

    def test_error_result(self):
        r = ModelResult(
            model_name="Test",
            accuracy=None,
            precision=None,
            recall=None,
            f1=None,
            macro_precision=None,
            macro_recall=None,
            macro_f1=None,
            confusion_matrix=None,
            error="Something broke",
        )
        assert r.error == "Something broke"


class TestEvaluationReport:
    def test_empty_report(self):
        report = EvaluationReport(
            dataset="Test",
            dataset_size=0,
            label_distribution={},
            evaluated_at="2025-01-01T00:00:00Z",
        )
        assert len(report.models) == 0

    def test_to_dict(self):
        report = EvaluationReport(
            dataset="Test",
            dataset_size=100,
            label_distribution={"positive": 50, "negative": 50},
            evaluated_at="2025-01-01T00:00:00Z",
            models=[
                ModelResult(
                    model_name="M1",
                    accuracy=0.9,
                    precision={"positive": 0.9, "negative": 0.9, "neutral": 0.0},
                    recall={"positive": 0.9, "negative": 0.9, "neutral": 0.0},
                    f1={"positive": 0.9, "negative": 0.9, "neutral": 0.0},
                    macro_precision=0.6,
                    macro_recall=0.6,
                    macro_f1=0.6,
                    confusion_matrix={"positive": {"positive": 1.0, "negative": 0.0, "neutral": 0.0}},
                    timing_ms=10.0,
                )
            ],
        )
        d = report_to_dict(report)
        assert d["dataset"] == "Test"
        assert len(d["models"]) == 1

    def test_to_json(self):
        report = EvaluationReport(
            dataset="Test",
            dataset_size=10,
            label_distribution={"pos": 5, "neg": 5},
            evaluated_at="2025-01-01T00:00:00Z",
        )
        j = report_to_json(report)
        assert '"Test"' in j

    def test_to_markdown(self):
        report = EvaluationReport(
            dataset="Test Dataset",
            dataset_size=100,
            label_distribution={"pos": 60, "neg": 40},
            evaluated_at="2025-01-01T00:00:00Z",
            models=[
                ModelResult(
                    model_name="Model A",
                    accuracy=0.85,
                    precision={"positive": 0.8, "negative": 0.9, "neutral": 0.0},
                    recall={"positive": 0.9, "negative": 0.8, "neutral": 0.0},
                    f1={"positive": 0.85, "negative": 0.85, "neutral": 0.0},
                    macro_precision=0.57,
                    macro_recall=0.57,
                    macro_f1=0.57,
                    confusion_matrix={"positive": {"positive": 1.0, "negative": 0.0, "neutral": 0.0}},
                    timing_ms=15.5,
                ),
                ModelResult(
                    model_name="Model B",
                    accuracy=None,
                    precision=None,
                    recall=None,
                    f1=None,
                    macro_precision=None,
                    macro_recall=None,
                    macro_f1=None,
                    confusion_matrix=None,
                    error="OOM",
                ),
            ],
        )
        md = report_to_markdown(report)
        assert "# Sentiment Model Evaluation: Test Dataset" in md
        assert "Model A" in md
        assert "ERROR: OOM" in md
        assert "85.0%" in md or "85%" in md


class TestRunEvaluation:
    def test_model_builders_registry(self):
        assert "finbert" in MODEL_BUILDERS
        assert "vader" in MODEL_BUILDERS
        assert "rule_based" in MODEL_BUILDERS
        assert "ensemble" in MODEL_BUILDERS

    def test_evaluate_model_fixed(self):
        texts = ["good earnings beat", "terrible guidance cut", "neutral outlook today"]
        labels = ["positive", "negative", "neutral"]
        result = evaluate_model("rule_based", texts, labels)
        assert result.accuracy is not None
        assert result.timing_ms is not None
        assert result.timing_ms >= 0

    def test_evaluate_model_unknown(self):
        texts = ["good"]
        labels = ["positive"]
        with pytest.raises(KeyError, match="nonexistent"):
            evaluate_model("nonexistent", texts, labels)

    def test_run_evaluation_invalid_dataset(self):
        with pytest.raises(KeyError, match="Unknown dataset"):
            run_evaluation(dataset_name="does_not_exist", max_samples=10, model_types=["rule_based"])
