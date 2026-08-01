from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path

import pytest

from pairs_trading.backend.schemas import BacktestRunRequest
from pairs_trading.backend.services import BacktestService
from pairs_trading.backend.strategy_builder import StrategyBuilderService
from tests.test_strategy_builder_provider_backtests import PROMPT, _seed_prices, _settings


pytestmark = pytest.mark.integration


def test_live_ollama_build_approval_and_walk_forward_backtest(tmp_path: Path) -> None:
    model = str(os.getenv("TEST_OLLAMA_MODEL") or "").strip()
    if not model:
        pytest.skip("TEST_OLLAMA_MODEL is not configured; live Ollama strategy-builder test skipped")
    base_url = str(os.getenv("TEST_OLLAMA_BASE_URL") or "http://127.0.0.1:11434").strip()
    settings = replace(
        _settings(tmp_path, "ollama", model),
        strategy_builder_ollama_base_url=base_url,
        strategy_builder_llm_timeout_seconds=180.0,
        strategy_builder_llm_max_retries=0,
        strategy_builder_llm_max_concurrency=1,
    )
    builder = StrategyBuilderService(settings)
    workspace = builder.store.ensure_demo_workspace()
    organization_id = str(workspace["organization_id"])
    user_id = str(workspace["user_id"])
    generated = builder.chat(
        organization_id=organization_id,
        user_id=user_id,
        messages=[{"role": "user", "content": PROMPT}],
    )
    assert generated["state"] == "ready_for_approval", generated
    assert generated["provider"] == "ollama"
    assert generated["model"] == model
    approved = builder.approve(
        organization_id=organization_id,
        user_id=user_id,
        spec=generated["draft_spec"],
        approval_text="Approved during live Ollama integration verification.",
        provenance_token=generated["provenance_token"],
    )

    start, end = "2020-01-01", "2022-06-01"
    _seed_prices(settings.price_cache_dir, start=start, end=end)
    result = BacktestService(settings).run_backtest(
        BacktestRunRequest(
            pipeline=approved["catalog_item"]["pipeline"],
            symbols=["SPY", "QQQ"],
            start=start,
            end=end,
            train_bars=180,
            test_bars=45,
            step_bars=45,
            purge_bars=5,
            pbo_partitions=4,
        ),
        organization_id=organization_id,
        user_id=user_id,
    )
    assert result["summary"]["folds"] >= 5
    assert result["equity_curve_points"]
    assert Path(result["artifact_dir"], "summary.json").is_file()
