from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from pairs_trading.backend.app import create_app
from pairs_trading.backend.config import BackendSettings


def _settings(root: Path, *, enabled: bool = True) -> BackendSettings:
    return BackendSettings(
        metadata_db_path=root / "metadata.sqlite3",
        paper_state_dir=root / "state",
        paper_artifact_root=root / "runs",
        paper_job_state_dir=root / "paper_jobs",
        backtest_job_state_dir=root / "backtest_jobs",
        sentiment_job_state_dir=root / "sentiment_jobs",
        default_paper_config=root / "missing.json",
        marketplace_enabled=enabled,
    )


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {
        "X-Organization-Id": response.json()["active_organization_id"],
        "X-CSRF-Token": client.cookies.get("quantops_csrf") or "",
    }


def _approved_strategy(client: TestClient, headers: dict[str, str], prompt: str | None = None) -> dict:
    chat = client.post(
        "/api/strategies/builder/chat",
        headers=headers,
        json={"messages": [{"role": "user", "content": prompt or "Trade SPY and QQQ on daily bars. Buy equal weight when RSI 14 is below 30, exit above 55, use a 10% stop loss and 3 bps costs."}]},
    )
    assert chat.status_code == 200, chat.text
    body = chat.json()
    approved = client.post(
        "/api/strategies/builder/approve",
        headers=headers,
        json={"approved": True, "approval_text": "Reviewed for marketplace test", "spec": body["draft_spec"], "provenance_token": body["provenance_token"]},
    )
    assert approved.status_code == 200, approved.text
    return approved.json()["strategy"]


def test_marketplace_persists_immutable_versions_and_tenant_subscriptions() -> None:
    with TemporaryDirectory(prefix="tradepilot-marketplace-") as temp:
        app = create_app(_settings(Path(temp)))
        publisher = TestClient(app)
        subscriber = TestClient(app)
        publisher_headers = _login(publisher, "demo@quantops.local", "quantops-demo")
        subscriber_headers = _login(subscriber, "user@quantops.local", "quantops-user")
        strategy = _approved_strategy(publisher, publisher_headers)

        created = publisher.post(
            "/api/marketplace/listings",
            headers=publisher_headers,
            json={"source_strategy_id": strategy["id"], "title": "Reviewed RSI Reversion", "summary": "A reviewed immutable paper-research strategy for liquid index funds.", "visibility": "public"},
        )
        assert created.status_code == 200, created.text
        listing_id = created.json()["id"]
        published = publisher.post(f"/api/marketplace/listings/{listing_id}/publish", headers=publisher_headers, json={})
        assert published.status_code == 200, published.text
        version_id = published.json()["current_version_id"]

        public = subscriber.get("/api/marketplace/listings").json()
        assert [item["id"] for item in public] == [listing_id]
        assert public[0]["version"] == 1
        assert "approval" not in public[0]

        self_subscribe = publisher.post(
            f"/api/marketplace/listings/{listing_id}/subscribe",
            headers=publisher_headers,
            json={"idempotency_key": "publisher-self-subscribe"},
        )
        assert self_subscribe.status_code == 400

        first = subscriber.post(
            f"/api/marketplace/listings/{listing_id}/subscribe",
            headers=subscriber_headers,
            json={"idempotency_key": "subscriber-stable-key"},
        )
        replay = subscriber.post(
            f"/api/marketplace/listings/{listing_id}/subscribe",
            headers=subscriber_headers,
            json={"idempotency_key": "subscriber-stable-key"},
        )
        assert first.status_code == replay.status_code == 200
        assert first.json()["id"] == replay.json()["id"]
        assert first.json()["pinned_listing_version_id"] == version_id

        replacement = _approved_strategy(
            publisher,
            publisher_headers,
            "Trade SPY and QQQ on daily bars. Buy equal weight when the 50-day SMA crosses above the 200-day SMA, exit on a cross below, use a 12% stop loss and 4 bps costs.",
        )
        created_version = publisher.post(
            f"/api/marketplace/listings/{listing_id}/versions",
            headers=publisher_headers,
            json={"source_strategy_id": replacement["id"]},
        )
        assert created_version.status_code == 200, created_version.text
        assert created_version.json()["version"] == 2
        republished = publisher.post(f"/api/marketplace/listings/{listing_id}/publish", headers=publisher_headers, json={})
        assert republished.status_code == 200
        next_version_id = republished.json()["current_version_id"]
        assert next_version_id != version_id

        pinned = subscriber.get("/api/marketplace/me/subscriptions", headers=subscriber_headers).json()[0]
        assert pinned["pinned_listing_version_id"] == version_id
        assert pinned["strategy_spec"]
        upgraded = subscriber.post(
            f"/api/marketplace/listings/{listing_id}/upgrade",
            headers=subscriber_headers,
            json={"idempotency_key": "subscriber-explicit-upgrade", "version_id": next_version_id},
        )
        assert upgraded.status_code == 200
        assert upgraded.json()["pinned_listing_version_id"] == next_version_id

        archived = publisher.post(f"/api/marketplace/listings/{listing_id}/archive", headers=publisher_headers, json={})
        assert archived.status_code == 200
        still_present = subscriber.get("/api/marketplace/me/subscriptions", headers=subscriber_headers).json()
        assert still_present[0]["pinned_listing_version_id"] == next_version_id
        assert still_present[0]["execution_access"] is True


def test_marketplace_is_safely_disabled_by_default() -> None:
    with TemporaryDirectory(prefix="tradepilot-marketplace-disabled-") as temp:
        client = TestClient(create_app(_settings(Path(temp), enabled=False)))
        assert client.get("/api/marketplace/listings").json() == []
