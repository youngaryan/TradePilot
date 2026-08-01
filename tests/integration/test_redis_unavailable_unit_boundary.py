from __future__ import annotations

from types import SimpleNamespace
import sys
from unittest.mock import patch

import pytest

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.job_queue import enqueue_quant_job
from tests.integration.conftest import _required_test_url


def test_redis_unavailability_is_exercised_as_a_unit_boundary() -> None:
    class UnavailableQueue:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def fetch_job(self, _job_id: str) -> None:
            raise ConnectionError("simulated Redis outage")

    fake_modules = {
        "redis": SimpleNamespace(Redis=SimpleNamespace(from_url=lambda _url: object())),
        "rq": SimpleNamespace(Queue=UnavailableQueue),
        "rq.exceptions": SimpleNamespace(DuplicateJobError=RuntimeError),
        "rq.serializers": SimpleNamespace(JSONSerializer=object),
    }
    with patch.dict(sys.modules, fake_modules):
        with pytest.raises(ConnectionError, match="simulated Redis outage"):
            enqueue_quant_job(
                BackendSettings(redis_url="redis://unit.invalid/0"),
                kind="backtest",
                job_id="unavailable-unit-boundary",
            )


def test_required_integration_gate_fails_instead_of_skipping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_REDIS_URL", raising=False)
    monkeypatch.setenv("REQUIRE_REAL_INTEGRATION", "true")

    with pytest.raises(pytest.fail.Exception, match="required for the real-infrastructure integration gate"):
        _required_test_url("TEST_REDIS_URL")
