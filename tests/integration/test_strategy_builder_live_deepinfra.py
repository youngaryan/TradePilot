from __future__ import annotations

from dataclasses import replace
import math
import os
from pathlib import Path

import pytest

from pairs_trading.backend.schemas import BacktestRunRequest
from pairs_trading.backend.services import BacktestService
from pairs_trading.backend.strategy_builder import StrategyBuilderService
from pairs_trading.backend.config import secret_env_value
from tests.test_strategy_builder_provider_backtests import PROMPT, _seed_prices, _settings


pytestmark = pytest.mark.integration


def test_live_deepinfra_build_analysis_approval_and_walk_forward_backtest(tmp_path: Path) -> None:
    model = str(os.getenv("TEST_DEEPINFRA_MODEL") or "").strip()
    if not model:
        pytest.skip("TEST_DEEPINFRA_MODEL is not configured; live DeepInfra strategy-builder test skipped")
    if not str(secret_env_value("DEEPINFRA_API_KEY", allow_dotenv=True) or "").strip():
        pytest.skip("DEEPINFRA_API_KEY is not configured; live DeepInfra strategy-builder test skipped")

    base_url = str(
        os.getenv("TEST_DEEPINFRA_BASE_URL") or "https://api.deepinfra.com/v1/openai"
    ).strip()
    settings = replace(
        _settings(tmp_path, "deepinfra", model),
        strategy_builder_deepinfra_api_key_ref="env:DEEPINFRA_API_KEY",
        strategy_builder_deepinfra_base_url=base_url,
        strategy_builder_llm_timeout_seconds=180.0,
        strategy_builder_llm_max_retries=1,
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
    assert generated["provider"] == "deepinfra"
    assert generated["model"] == model
    assert generated["generation_summary"]
    assert generated["generation_path"] in {"model_first", "model_first_semantic_repair"}
    assert generated["interpreted_intent"]["objective"]
    assert generated["interpreted_intent"]["requirement_trace"]
    assert not generated["interpreted_intent"]["unsupported_requirements"]
    assert not generated["interpreted_intent"]["missing_requirements"]
    risk = generated["risk_analysis"]
    assert risk["overall_risk"] in {"low", "medium", "high"}
    assert risk["overview"]
    assert risk["key_risks"]
    assert risk["mitigations"]
    assert risk["validation_priorities"]
    costs = generated["draft_spec"]["costs"]
    assert sum(float(costs[name]) for name in (
        "commission_bps", "spread_bps", "slippage_bps", "market_impact_bps"
    )) == pytest.approx(3.0)

    approved = builder.approve(
        organization_id=organization_id,
        user_id=user_id,
        spec=generated["draft_spec"],
        approval_text="Approved during live DeepInfra integration verification.",
        provenance_token=generated["provenance_token"],
    )
    assert approved["validation"]["dry_run"]["status"] == "passed"
    assert approved["strategy"]["risk_level"] == "medium"

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
    assert result["fold_metrics_tail"]
    for metric in ("total_return", "annualized_return", "sharpe", "max_drawdown", "avg_turnover"):
        assert math.isfinite(float(result["summary"][metric]))
    artifact_dir = Path(result["artifact_dir"])
    assert (artifact_dir / "summary.json").is_file()
    assert (artifact_dir / "validation.json").is_file()
    assert (artifact_dir / "fold_metrics.parquet").is_file()
