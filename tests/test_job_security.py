from __future__ import annotations

from copy import deepcopy

from pairs_trading.backend.job_security import (
    collect_secret_values,
    redact_secret_values,
    sanitize_job_data,
)


def test_sanitizes_nested_sentiment_paper_and_backtest_data() -> None:
    payload = {
        "sentiment": {
            "newsapi_api_key": "news-secret",
            "stocktwits_access_token": "stocktwits-secret",
            "providers": ["newsapi", "stocktwits"],
        },
        "paper": {
            "deployment_config": {
                "strategies": (
                    {"name": "event", "params": {"password": "paper-secret", "window": 20}},
                )
            }
        },
        "backtest": {
            "parameters": [
                {"BENZINGA_API_KEY": "benzinga-secret"},
                {"nested": {"credential": "credential-secret", "lookback": 30}},
            ]
        },
    }

    sanitized = sanitize_job_data(payload)

    assert sanitized == {
        "sentiment": {"providers": ["newsapi", "stocktwits"]},
        "paper": {
            "deployment_config": {
                "strategies": ({"name": "event", "params": {"window": 20}},)
            }
        },
        "backtest": {"parameters": [{}, {"nested": {"lookback": 30}}]},
    }
    assert collect_secret_values(payload) == {
        "news-secret",
        "stocktwits-secret",
        "paper-secret",
        "benzinga-secret",
        "credential-secret",
    }


def test_does_not_flag_innocent_tokenizer_fields() -> None:
    payload = {
        "tokenizer": "finbert-tokenizer",
        "tokenizer_name": "ProsusAI/finbert",
        "access_tokens": 128,
        "model": {"token": "real-secret"},
    }

    assert sanitize_job_data(payload) == {
        "tokenizer": "finbert-tokenizer",
        "tokenizer_name": "ProsusAI/finbert",
        "access_tokens": 128,
        "model": {},
    }
    assert collect_secret_values(payload) == {"real-secret"}


def test_sanitization_does_not_mutate_input() -> None:
    payload = {
        "items": [{"api_key": "secret", "symbols": ["AAPL"]}],
        "tuple": ({"secret": "nested"}, "unchanged"),
    }
    original = deepcopy(payload)

    sanitized = sanitize_job_data(payload)

    assert payload == original
    assert sanitized is not payload
    assert sanitized["items"] is not payload["items"]
    assert sanitized["items"][0] is not payload["items"][0]
    assert sanitized == {"items": [{"symbols": ["AAPL"]}], "tuple": ({}, "unchanged")}


def test_redacts_secret_values_from_exception_text() -> None:
    payload = {
        "newsapi_api_key": "short-secret",
        "nested": {"access_token": "longer-short-secret-value"},
    }
    message = (
        "Provider rejected short-secret; request URL contained "
        "longer-short-secret-value and short-secret again."
    )

    redacted = redact_secret_values(message, collect_secret_values(payload))

    assert redacted == "Provider rejected [REDACTED]; request URL contained [REDACTED] and [REDACTED] again."
    assert "short-secret" not in redacted
