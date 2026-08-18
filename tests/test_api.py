import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

os.environ["TOKEN_DATABASE_URL"] = "sqlite:///./test-token.db"
os.environ["TOKEN_ADMIN_TOKEN"] = "test-admin"
os.environ["TOKEN_MOCK_MODE"] = "true"
os.environ["TOKEN_SEED_BUILTIN_MODELS"] = "true"
os.environ["TOKEN_PAYMENT_WEBHOOK_SECRET"] = "test-webhook"
os.environ["TOKEN_TRIAL_SIGNING_SECRET"] = "test-trial-secret"
os.environ["TOKEN_PUBLIC_BASE_URL"] = "http://testserver"

from app.db import Base, SessionLocal, engine
from app.config import Settings, validate_startup_settings
from app.guardrails import rate_limiter
from app.main import app
from app.config import get_settings
from app.models import ApiKey, ExternalIdentity, UsageRecord


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    rate_limiter.reset()


def test_mock_mode_seeds_builtin_models() -> None:
    from app.db import init_db
    from app.models import ModelConfig

    init_db()
    with SessionLocal() as db:
        names = set(db.query(ModelConfig.public_name).all())
    assert {"lok-chat", "lok-reason", "lok-vision"} <= {item[0] for item in names}


@pytest.mark.asyncio
async def test_seeded_builtin_model_is_directly_callable() -> None:
    from app.db import init_db

    init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        key_response = await client.post("/admin/api-keys", headers={"X-Admin-Token": "test-admin"}, json={"name": "builtin-model-key"})
        assert key_response.status_code == 200
        await client.post(
            f"/admin/api-keys/{key_response.json()['id']}/balance",
            headers={"X-Admin-Token": "test-admin"},
            json={"amount_micros": 100_000, "idempotency_key": "builtin-model-topup"},
        )
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key_response.json()['key']}"},
            json={"model": "lok-chat", "messages": [{"role": "user", "content": "hello"}]},
        )
    assert response.status_code == 200
    assert response.json()["model"] == "lok-chat"


@pytest.mark.asyncio
async def test_channel_health_check_updates_model_marketplace() -> None:
    from app.db import init_db

    init_db()
    transport = httpx.ASGITransport(app=app)
    admin_headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        account = await client.post("/admin/accounts", headers=admin_headers, json={"external_user_id": "health-user", "name": "Health User"})
        trial = await client.post("/admin/trial-links", headers=admin_headers, json={"account_id": account.json()["id"], "expires_in_seconds": 3600})
        health = await client.post("/admin/models/health-check", headers=admin_headers, json={})
        models = await client.get("/portal/models", headers={"Authorization": f"Bearer {trial.json()['access_token']}"})
    assert health.status_code == 200
    assert health.json()["checked"] >= 3
    assert health.json()["healthy"] == health.json()["checked"]
    assert models.status_code == 200
    assert {item["health_status"] for item in models.json()["data"]} == {"healthy"}


@pytest.mark.asyncio
async def test_registered_user_can_complete_first_call_journey() -> None:
    from app.db import init_db

    init_db()
    transport = httpx.ASGITransport(app=app)
    admin_headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        benefit = await client.post(
            "/admin/redemption-codes",
            headers=admin_headers,
            json={"label": "新用户试用额度", "amount_micros": 100_000, "code": "NEWUSER-2026"},
        )
        registered = await client.post(
            "/auth/register",
            json={"login_id": "first-call-user", "name": "First Call User", "password": "correct-horse"},
        )
        portal_headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

        models = await client.get("/portal/models", headers=portal_headers)
        order = await client.post(
            "/portal/payment-orders",
            headers=portal_headers,
            json={"account_id": registered.json()["account"]["id"], "amount_micros": 10_000, "provider": "manual"},
        )
        redeemed = await client.post("/portal/redemption-codes/redeem", headers=portal_headers, json={"code": benefit.json()["code"]})
        key = await client.post("/portal/api-keys", headers=portal_headers, json={"name": "first-service"})
        called = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key.json()['key']}"},
            json={"model": "lok-chat", "messages": [{"role": "user", "content": "hello"}]},
        )
        requests = await client.get("/portal/usage/records", headers=portal_headers)
        orders = await client.get("/portal/payment-orders", headers=portal_headers)
        user_guide = await client.get("/guide/user")
        admin_guide = await client.get("/guide/admin")

    assert benefit.status_code == 200
    assert registered.status_code == 200
    assert models.status_code == 200 and any(item["public_name"] == "lok-chat" for item in models.json()["data"])
    assert order.status_code == 200 and order.json()["status"] == "pending"
    assert redeemed.status_code == 200 and redeemed.json()["balance_micros"] == 100_000
    assert key.status_code == 200
    assert called.status_code == 200
    assert requests.status_code == 200 and requests.json()["total"] == 1
    assert orders.status_code == 200 and orders.json()["data"][0]["status"] == "pending"
    assert user_guide.status_code == 200 and admin_guide.status_code == 200


@pytest.mark.asyncio
async def test_portal_registration_and_login() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        registered = await client.post(
            "/auth/register",
            json={"login_id": "new-user", "name": "New User", "password": "correct-horse"},
        )
        assert registered.status_code == 200
        assert registered.json()["access_token"].startswith("usr_")
        profile = await client.get(
            "/portal/profile",
            headers={"Authorization": f"Bearer {registered.json()['access_token']}"},
        )
        assert profile.status_code == 200
        assert profile.json()["name"] == "New User"
        duplicate = await client.post(
            "/auth/register",
            json={"login_id": "new-user", "name": "Duplicate", "password": "correct-horse"},
        )
        assert duplicate.status_code == 409
        login = await client.post("/auth/login", json={"login_id": "NEW-USER", "password": "correct-horse"})
        assert login.status_code == 200
        invalid = await client.post("/auth/login", json={"login_id": "new-user", "password": "wrong-password"})
        assert invalid.status_code == 401


@pytest.mark.asyncio
async def test_trial_bound_api_key_expires_with_trial(monkeypatch) -> None:
    from app.models import BillingAccount

    transport = httpx.ASGITransport(app=app)
    admin_headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        account = await client.post("/admin/accounts", headers=admin_headers, json={"external_user_id": "trial-expiry-user", "name": "Trial Expiry"})
        trial = await client.post("/admin/trial-links", headers=admin_headers, json={"account_id": account.json()["id"], "expires_in_seconds": 3600})
        portal_headers = {"Authorization": f"Bearer {trial.json()['access_token']}"}
        key = await client.post("/portal/api-keys", headers=portal_headers, json={"name": "trial-bound"})
        assert key.status_code == 200
        with SessionLocal() as db:
            account_record = db.get(BillingAccount, account.json()["id"])
            api_key = db.query(ApiKey).filter(ApiKey.account_id == account_record.id, ApiKey.name == "trial-bound").one()
            api_key.trial_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        response = await client.get("/v1/models", headers={"Authorization": f"Bearer {key.json()['key']}"})
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_key_model_and_chat() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        key_response = await client.post("/admin/api-keys", headers={"X-Admin-Token": "test-admin"}, json={"name": "demo"})
        assert key_response.status_code == 200
        api_key = key_response.json()["key"]

        model_response = await client.post(
            "/admin/models",
            headers={"X-Admin-Token": "test-admin"},
            json={"public_name": "demo-model", "upstream_model": "demo-upstream", "input_price_micros_per_1k": 1000, "output_price_micros_per_1k": 2000},
        )
        assert model_response.status_code == 200
        topup_response = await client.post(
            f"/admin/api-keys/{key_response.json()['id']}/balance",
            headers={"X-Admin-Token": "test-admin"},
            json={"amount_micros": 10000, "idempotency_key": "topup-demo"},
        )
        assert topup_response.status_code == 200
        repeated_topup = await client.post(
            f"/admin/api-keys/{key_response.json()['id']}/balance",
            headers={"X-Admin-Token": "test-admin"},
            json={"amount_micros": 10000, "idempotency_key": "topup-demo"},
        )
        assert repeated_topup.json()["balance_micros"] == 10000

        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "X-Request-ID": "req_test"},
            json={"model": "demo-model", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "req_test"
        assert response.json()["usage"]["total_tokens"] > 0

        account = await client.get("/v1/account", headers={"Authorization": f"Bearer {api_key}"})
        assert account.status_code == 200
        assert 0 < account.json()["balance_micros"] < 10000

        transactions = await client.get(
            f"/admin/api-keys/{key_response.json()['id']}/transactions",
            headers={"X-Admin-Token": "test-admin"},
        )
        assert transactions.status_code == 200
        assert {item["type"] for item in transactions.json()["data"]} == {"topup", "reservation", "settlement"}

        duplicate = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "X-Request-ID": "req_test"},
            json={"model": "demo-model", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert duplicate.status_code == 409

        second_key = await client.post("/admin/api-keys", headers={"X-Admin-Token": "test-admin"}, json={"name": "empty"})
        empty_call = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {second_key.json()['key']}"},
            json={"model": "demo-model", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert empty_call.status_code == 402

        usage = await client.get("/admin/usage", headers={"X-Admin-Token": "test-admin"})
        assert usage.status_code == 200
        assert usage.json()["request_count"] == 2


@pytest.mark.asyncio
async def test_account_balance_is_shared_by_multiple_keys() -> None:
    transport = httpx.ASGITransport(app=app)
    admin_headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        account = await client.post(
            "/admin/accounts",
            headers=admin_headers,
            json={"external_user_id": "lok-user-001", "name": "Lok User"},
        )
        assert account.status_code == 200
        account_id = account.json()["id"]

        first_key = await client.post(
            "/admin/api-keys",
            headers=admin_headers,
            json={"name": "first", "account_id": account_id},
        )
        second_key = await client.post(
            "/admin/api-keys",
            headers=admin_headers,
            json={"name": "second", "account_id": account_id},
        )
        await client.post(
            f"/admin/accounts/{account_id}/balance",
            headers=admin_headers,
            json={"amount_micros": 10000, "idempotency_key": "shared-topup"},
        )
        await client.post(
            "/admin/models",
            headers=admin_headers,
            json={"public_name": "shared-model", "upstream_model": "shared", "input_price_micros_per_1k": 1000, "output_price_micros_per_1k": 2000},
        )

        for index, key_response in enumerate((first_key, second_key), start=1):
            response = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {key_response.json()['key']}", "X-Request-ID": f"req_shared_{index}"},
                json={"model": "shared-model", "messages": [{"role": "user", "content": "hello"}]},
            )
            assert response.status_code == 200

        first_account = await client.get(
            "/v1/account",
            headers={"Authorization": f"Bearer {first_key.json()['key']}"},
        )
        second_account = await client.get(
            "/v1/account",
            headers={"Authorization": f"Bearer {second_key.json()['key']}"},
        )
        assert first_account.json()["account_id"] == account_id
        assert first_account.json()["balance_micros"] == second_account.json()["balance_micros"] == 9982

        transactions = await client.get(f"/admin/accounts/{account_id}/transactions", headers=admin_headers)
        assert len(transactions.json()["data"]) == 5


@pytest.mark.asyncio
async def test_admin_console_queries_and_toggles() -> None:
    transport = httpx.ASGITransport(app=app)
    admin_headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        homepage = await client.get("/")
        assert homepage.status_code == 200
        assert "LokToken管理控制台" in homepage.text

        account = await client.post(
            "/admin/accounts",
            headers=admin_headers,
            json={"external_user_id": "console-user", "name": "Console User"},
        )
        account_id = account.json()["id"]
        api_key = await client.post(
            "/admin/api-keys",
            headers=admin_headers,
            json={"name": "console-key", "account_id": account_id},
        )
        model = await client.post(
            "/admin/models",
            headers=admin_headers,
            json={"public_name": "console-model", "upstream_model": "console-upstream"},
        )

        for path in ("/admin/overview", "/admin/accounts", "/admin/api-keys", "/admin/models", "/admin/usage/records"):
            response = await client.get(path, headers=admin_headers)
            assert response.status_code == 200

        providers = await client.get("/admin/payment-providers", headers=admin_headers)
        assert providers.status_code == 200
        assert next(item for item in providers.json()["data"] if item["id"] == "manual")["available"] is True
        assert next(item for item in providers.json()["data"] if item["id"] == "wechat")["available"] is False

        unavailable_order = await client.post(
            "/admin/payment-orders",
            headers=admin_headers,
            json={"account_id": account_id, "amount_micros": 1_000_000, "provider": "wechat"},
        )
        assert unavailable_order.status_code == 422

        model_id = model.json()["id"]
        primary_channels = await client.get(f"/admin/models/{model_id}/channels", headers=admin_headers)
        assert primary_channels.status_code == 200
        assert [item["name"] for item in primary_channels.json()["data"]] == ["Primary"]
        backup = await client.post(
            f"/admin/models/{model_id}/channels",
            headers=admin_headers,
            json={
                "name": "Console Backup",
                "provider_base_url": "https://backup.example/v1",
                "upstream_model": "console-backup",
                "priority": 200,
                "weight": 50,
            },
        )
        assert backup.status_code == 200
        updated = await client.patch(
            f"/admin/channels/{backup.json()['id']}",
            headers=admin_headers,
            json={"priority": 150, "weight": 80},
        )
        assert updated.json()["priority"] == 150
        health = await client.post(f"/admin/channels/{backup.json()['id']}/check", headers=admin_headers)
        assert health.status_code == 200
        assert health.json()["healthy"] is True
        listed_model = next(item for item in (await client.get("/admin/models", headers=admin_headers)).json()["data"] if item["id"] == model_id)
        assert listed_model["channel_count"] == 2
        assert listed_model["healthy_channel_count"] == 1

        assert (await client.patch(f"/admin/accounts/{account_id}", headers=admin_headers, json={"active": False})).json()["active"] is False
        assert (await client.patch(f"/admin/api-keys/{api_key.json()['id']}", headers=admin_headers, json={"active": False})).json()["active"] is False
        assert (await client.patch(f"/admin/models/{model.json()['id']}", headers=admin_headers, json={"active": False})).json()["active"] is False


@pytest.mark.asyncio
async def test_payment_confirmation_is_idempotent_and_refundable() -> None:
    transport = httpx.ASGITransport(app=app)
    admin_headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        account = await client.post("/admin/accounts", headers=admin_headers, json={"external_user_id": "payer", "name": "Payer"})
        account_id = account.json()["id"]
        api_key = await client.post("/admin/api-keys", headers=admin_headers, json={"name": "payer-key", "account_id": account_id})
        order = await client.post(
            "/admin/payment-orders",
            headers=admin_headers,
            json={"account_id": account_id, "amount_micros": 5_000_000, "provider": "manual"},
        )
        order_id = order.json()["id"]

        first_confirm = await client.post(f"/admin/payment-orders/{order_id}/confirm", headers=admin_headers, json={})
        second_confirm = await client.post(f"/admin/payment-orders/{order_id}/confirm", headers=admin_headers, json={})
        assert first_confirm.json()["status"] == second_confirm.json()["status"] == "paid"

        auth_headers = {"Authorization": f"Bearer {api_key.json()['key']}"}
        balance = await client.get("/v1/account", headers=auth_headers)
        assert balance.json()["balance_micros"] == 5_000_000

        first_refund = await client.post(f"/admin/payment-orders/{order_id}/refund", headers=admin_headers)
        second_refund = await client.post(f"/admin/payment-orders/{order_id}/refund", headers=admin_headers)
        assert first_refund.json()["status"] == second_refund.json()["status"] == "refunded"
        balance = await client.get("/v1/account", headers=auth_headers)
        assert balance.json()["balance_micros"] == 0

        transactions = await client.get(f"/admin/accounts/{account_id}/transactions", headers=admin_headers)
        assert [item["type"] for item in transactions.json()["data"]] == ["refund", "payment"]


@pytest.mark.asyncio
async def test_signed_payment_webhook_rejects_invalid_signature_and_is_idempotent() -> None:
    transport = httpx.ASGITransport(app=app)
    admin_headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        account = await client.post("/admin/accounts", headers=admin_headers, json={"external_user_id": "webhook-user", "name": "Webhook User"})
        account_id = account.json()["id"]
        api_key = await client.post("/admin/api-keys", headers=admin_headers, json={"name": "webhook-key", "account_id": account_id})
        order = await client.post(
            "/admin/payment-orders",
            headers=admin_headers,
            json={"account_id": account_id, "amount_micros": 2_000_000, "provider": "manual"},
        )
        payload = {
            "event_id": "evt-001",
            "order_no": order.json()["order_no"],
            "provider_order_id": "wechat-order-001",
            "status": "paid",
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        invalid = await client.post("/payments/webhook", content=body, headers={"Content-Type": "application/json", "X-Token-Signature": "bad"})
        assert invalid.status_code == 401

        signature = hmac.new(b"test-webhook", body, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "X-Token-Signature": f"sha256={signature}"}
        first = await client.post("/payments/webhook", content=body, headers=headers)
        second = await client.post("/payments/webhook", content=body, headers=headers)
        assert first.status_code == second.status_code == 200

        balance = await client.get("/v1/account", headers={"Authorization": f"Bearer {api_key.json()['key']}"})
        assert balance.json()["balance_micros"] == 2_000_000


@pytest.mark.asyncio
async def test_trial_portal_and_streaming_user_flow() -> None:
    transport = httpx.ASGITransport(app=app)
    admin_headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        portal_page = await client.get("/portal")
        assert portal_page.status_code == 200
        assert "LokToken用户中心" in portal_page.text
        assert '<span>密钥管理</span>' in portal_page.text
        assert 'src="/static/portal.js?v=portal-20260818-6"' in portal_page.text
        assert portal_page.text.index('id="portal-integration-guide"') < portal_page.text.index('class="overview-quickbar panel"')
        assert '<strong>LokToken</strong>' in portal_page.text
        assert '<p class="sidebar-section-label">工作台</p>' in portal_page.text
        assert 'class="topbar-actions"' in portal_page.text
        assert 'id="portal-workspace-manager"' in portal_page.text
        assert 'id="portal-security"' in portal_page.text
        assert 'id="portal-refresh"' in portal_page.text
        assert "LokSystem 一键注册 / 登录" in portal_page.text
        assert 'id="loksystem-login-button"' in portal_page.text
        assert 'id="portal-integration-guide"' in portal_page.text
        assert "重置密码" not in portal_page.text
        assert "查看用户文档" not in portal_page.text
        assert "试用令牌（trl_ 开头）" in portal_page.text
        portal_script = await client.get("/static/portal.js?v=portal-20260818-6")
        assert portal_script.headers["cache-control"] == "no-store"
        assert "复制并前往 LokSystem" in portal_script.text
        assert "loksystem://add-provider?platform=LokToken" in portal_script.text
        assert "应用接入" in portal_script.text
        assert "在应用中完成配置" in portal_script.text
        assert 'keys: "密钥管理"' in portal_script.text
        assert "API管理" not in portal_page.text
        assert "创建 API Key" not in portal_script.text

        account = await client.post(
            "/admin/accounts",
            headers=admin_headers,
            json={"external_user_id": "trial-user", "name": "Trial User"},
        )
        other_account = await client.post(
            "/admin/accounts",
            headers=admin_headers,
            json={"external_user_id": "other-user", "name": "Other User"},
        )
        account_id = account.json()["id"]
        other_key = await client.post(
            "/admin/api-keys",
            headers=admin_headers,
            json={"name": "other-key", "account_id": other_account.json()["id"]},
        )
        trial_link = await client.post(
            "/admin/trial-links",
            headers=admin_headers,
            json={"account_id": account_id, "expires_in_seconds": 3600},
        )
        assert trial_link.status_code == 200
        assert trial_link.json()["portal_url"].startswith("http://testserver/portal#access_token=trl_")
        portal_headers = {"Authorization": f"Bearer {trial_link.json()['access_token']}"}

        profile = await client.get("/portal/profile", headers=portal_headers)
        assert profile.status_code == 200
        assert profile.json()["external_user_id"] == "trial-user"

        portal_key = await client.post(
            "/portal/api-keys",
            headers=portal_headers,
            json={"name": "trial-key", "expires_in_days": 30, "spending_limit_micros": 10000},
        )
        assert portal_key.status_code == 200
        assert portal_key.json()["key"].startswith("tok_")
        dated_key = await client.post(
            "/portal/api-keys",
            headers=portal_headers,
            json={"name": "dated-key", "expires_at": "2099-01-02T23:59:59Z"},
        )
        assert dated_key.status_code == 200
        dated_key_list = await client.get("/portal/api-keys", headers=portal_headers)
        dated_key_expiry = next(item for item in dated_key_list.json()["data"] if item["id"] == dated_key.json()["id"])["expires_at"]
        assert dated_key_expiry < "2099-01-02T00:00:00"
        forbidden = await client.patch(
            f"/portal/api-keys/{other_key.json()['id']}", headers=portal_headers, json={"active": False}
        )
        assert forbidden.status_code == 404

        await client.post(
            f"/admin/accounts/{account_id}/balance",
            headers=admin_headers,
            json={"amount_micros": 10000, "idempotency_key": "trial-topup"},
        )
        await client.post(
            "/admin/models",
            headers=admin_headers,
            json={"public_name": "trial-model", "upstream_model": "trial-upstream", "input_price_micros_per_1k": 1000, "output_price_micros_per_1k": 2000},
        )
        stream = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {portal_key.json()['key']}", "X-Request-ID": "req_trial_stream"},
            json={"model": "trial-model", "messages": [{"role": "user", "content": "hello"}], "stream": True},
        )
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert '"content":"TOKEN "' in stream.text
        assert '"content":"mock response"' in stream.text
        assert "data: [DONE]" in stream.text

        usage = await client.get("/portal/usage", headers=portal_headers)
        records = await client.get("/portal/usage/records", headers=portal_headers)
        assert usage.json()["request_count"] == 1
        assert records.json()["data"][0]["request_id"] == "req_trial_stream"
        dashboard = await client.get("/portal/dashboard?days=7&model=trial-model", headers=portal_headers)
        assert dashboard.status_code == 200, dashboard.text
        assert dashboard.json()["period"]["request_count"] == 1
        assert dashboard.json()["period"]["total_tokens"] == 5
        assert dashboard.json()["model_ranking"][0]["model"] == "trial-model"
        assert dashboard.json()["activity_summary"]["longest_streak_days"] == 1
        assert sum(item["request_count"] for item in dashboard.json()["activity"]) == 1
        isolated_dashboard = await client.get(
            f"/portal/dashboard?days=7&api_key_id={other_key.json()['id']}", headers=portal_headers
        )
        assert isolated_dashboard.json()["period"]["request_count"] == 0
        assert isolated_dashboard.json()["model_ranking"] == []
        assert (await client.get("/portal/dashboard?days=14", headers=portal_headers)).status_code == 422
        listed_keys = await client.get("/portal/api-keys", headers=portal_headers)
        listed_trial_key = next(item for item in listed_keys.json()["data"] if item["id"] == portal_key.json()["id"])
        assert listed_trial_key["spending_limit_micros"] == 10000
        assert listed_trial_key["spent_micros"] == 9
        assert listed_trial_key["expires_at"] is not None
        assert listed_trial_key["last_used_at"] is not None

        limited_key = await client.post(
            "/portal/api-keys",
            headers=portal_headers,
            json={"name": "limited-key", "spending_limit_micros": 1000},
        )
        limited_call = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {limited_key.json()['key']}", "X-Request-ID": "req_trial_limited"},
            json={"model": "trial-model", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert limited_call.status_code == 402
        assert limited_call.json()["detail"] == "api key spending limit exceeded"

        request_summary = await client.get("/portal/usage", headers=portal_headers)
        assert request_summary.json()["request_count"] == 2
        assert request_summary.json()["success_count"] == 1
        assert request_summary.json()["failed_count"] == 1
        assert request_summary.json()["success_rate"] == 50.0
        assert request_summary.json()["average_latency_ms"] >= 0
        successful_records = await client.get(
            "/portal/usage/records?status=success&request_id=trial_stream&page=1&page_size=10",
            headers=portal_headers,
        )
        assert successful_records.status_code == 200
        assert successful_records.json()["total"] == 1
        assert successful_records.json()["total_pages"] == 1
        request_detail = await client.get(
            "/portal/usage/records/req_trial_stream", headers=portal_headers
        )
        assert request_detail.status_code == 200
        assert request_detail.json()["trace_id"] == "req_trial_stream"
        assert request_detail.json()["total_tokens"] == 5
        analytics = await client.get(
            "/portal/usage/analytics?granularity=hour", headers=portal_headers
        )
        assert analytics.status_code == 200
        assert analytics.json()["granularity"] == "hour"
        assert sum(item["request_count"] for item in analytics.json()["trend"]) == 2
        assert analytics.json()["model_distribution"][0]["name"] == "trial-model"
        assert {item["name"] for item in analytics.json()["key_distribution"]} == {"trial-key", "limited-key"}
        other_trial = await client.post(
            "/admin/trial-links",
            headers=admin_headers,
            json={"account_id": other_account.json()["id"], "expires_in_seconds": 3600},
        )
        other_portal_headers = {"Authorization": f"Bearer {other_trial.json()['access_token']}"}
        isolated_detail = await client.get(
            "/portal/usage/records/req_trial_stream", headers=other_portal_headers
        )
        assert isolated_detail.status_code == 404

        future_usage = await client.get(
            "/portal/usage?from=2099-01-01T00:00:00Z", headers=portal_headers
        )
        assert future_usage.json()["request_count"] == 0
        exported = await client.get(
            f"/portal/usage/export?api_key_id={portal_key.json()['id']}", headers=portal_headers
        )
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith("text/csv")
        assert "req_trial_stream" in exported.text
        assert "req_trial_limited" not in exported.text

        with SessionLocal() as db:
            expiring_key = db.get(ApiKey, portal_key.json()["id"])
            expiring_key.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        expired_call = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {portal_key.json()['key']}"},
            json={"model": "trial-model", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert expired_call.status_code == 401

        order = await client.post(
            "/portal/payment-orders",
            headers=portal_headers,
            json={"account_id": account_id, "amount_micros": 1_000_000, "provider": "manual"},
        )
        assert order.status_code == 200
        assert order.json()["status"] == "pending"
        assert len((await client.get("/portal/payment-orders", headers=portal_headers)).json()["data"]) == 1

        tampered = trial_link.json()["access_token"][:-1] + ("A" if trial_link.json()["access_token"][-1] != "A" else "B")
        assert (await client.get("/portal/profile", headers={"Authorization": f"Bearer {tampered}"})).status_code == 401


@pytest.mark.asyncio
async def test_portal_redemption_code_balance_and_history() -> None:
    transport = httpx.ASGITransport(app=app)
    admin_headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        account = await client.post(
            "/admin/accounts", headers=admin_headers,
            json={"external_user_id": "redeem-user", "name": "Redeem User"},
        )
        other_account = await client.post(
            "/admin/accounts", headers=admin_headers,
            json={"external_user_id": "redeem-other", "name": "Redeem Other"},
        )
        trial = await client.post(
            "/admin/trial-links", headers=admin_headers,
            json={"account_id": account.json()["id"], "expires_in_seconds": 3600},
        )
        other_trial = await client.post(
            "/admin/trial-links", headers=admin_headers,
            json={"account_id": other_account.json()["id"], "expires_in_seconds": 3600},
        )
        portal_headers = {"Authorization": f"Bearer {trial.json()['access_token']}"}
        other_portal_headers = {"Authorization": f"Bearer {other_trial.json()['access_token']}"}

        created = await client.post(
            "/admin/redemption-codes", headers=admin_headers,
            json={"label": "试用福利", "amount_micros": 2_500_000, "code": "WELCOME-TOKEN-2026", "max_redemptions": 1},
        )
        assert created.status_code == 200
        assert created.json()["code"] == "WELCOME-TOKEN-2026"
        listed = await client.get("/admin/redemption-codes", headers=admin_headers)
        assert listed.status_code == 200
        assert "code" not in listed.json()["data"][0]
        assert listed.json()["data"][0]["code_prefix"] == "WELCOME-TOKE"
        code_id = created.json()["id"]
        disabled = await client.patch(f"/admin/redemption-codes/{code_id}", headers=admin_headers, json={"active": False})
        assert disabled.status_code == 200
        assert disabled.json()["active"] is False
        assert (await client.patch(f"/admin/redemption-codes/{code_id}", headers=admin_headers, json={"active": True})).json()["active"] is True

        before = await client.get("/portal/balance-summary", headers=portal_headers)
        assert before.json()["balance_micros"] == 0
        redeemed = await client.post(
            "/portal/redemption-codes/redeem", headers=portal_headers,
            json={"code": "WELCOME-TOKEN-2026"},
        )
        assert redeemed.status_code == 200
        assert redeemed.json()["amount_micros"] == 2_500_000
        assert redeemed.json()["balance_micros"] == 2_500_000
        assert (await client.post(
            "/portal/redemption-codes/redeem", headers=portal_headers,
            json={"code": "WELCOME-TOKEN-2026"},
        )).status_code == 409
        assert (await client.post(
            "/portal/redemption-codes/redeem", headers=other_portal_headers,
            json={"code": "WELCOME-TOKEN-2026"},
        )).status_code == 422

        summary = await client.get("/portal/balance-summary", headers=portal_headers)
        assert summary.json()["total_credit_micros"] == 2_500_000
        assert summary.json()["transaction_count"] == 1
        history = await client.get("/portal/redemptions", headers=portal_headers)
        assert history.json()["data"][0]["label"] == "试用福利"
        transactions = await client.get("/portal/transactions", headers=portal_headers)
        assert transactions.json()["data"][0]["type"] == "redemption"
        audits = await client.get("/admin/audit-events", headers=admin_headers)
        audit_actions = {item["action"] for item in audits.json()["data"]}
        assert {"redemption_code.created", "redemption_code.status_updated", "redemption_code.claimed"} <= audit_actions


@pytest.mark.asyncio
async def test_batch_model_import_pricing_and_api_rate_limit(monkeypatch) -> None:
    transport = httpx.ASGITransport(app=app)
    admin_headers = {"X-Admin-Token": "test-admin"}
    settings = get_settings()
    monkeypatch.setattr(settings, "api_rate_limit_requests", 1)
    monkeypatch.setattr(settings, "api_rate_limit_window_seconds", 60)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        imported = await client.post(
            "/admin/models/batch", headers=admin_headers,
            json={
                "provider_base_url": "https://gateway.example/v1",
                "provider_api_key_env": "OPENAI_API_KEY",
                "models": [
                    {"public_name": "lok-chat", "upstream_model": "gpt-chat", "input_price_micros_per_1k": 1000, "output_price_micros_per_1k": 2000},
                    {"public_name": "lok-reason", "upstream_model": "gpt-reason", "input_price_micros_per_1k": 3000, "output_price_micros_per_1k": 6000},
                ],
            },
        )
        assert imported.status_code == 200
        assert [item["public_name"] for item in imported.json()["data"]] == ["lok-chat", "lok-reason"]
        duplicate = await client.post(
            "/admin/models/batch", headers=admin_headers,
            json={"provider_base_url": "https://gateway.example/v1", "models": [{"public_name": "lok-chat", "upstream_model": "duplicate"}]},
        )
        assert duplicate.status_code == 409
        updated = await client.patch(
            f"/admin/models/{imported.json()['data'][0]['id']}", headers=admin_headers,
            json={"input_price_micros_per_1k": 1500, "output_price_micros_per_1k": 2500},
        )
        assert updated.status_code == 200
        assert updated.json()["input_price_micros_per_1k"] == 1500
        audits = await client.get("/admin/audit-events", headers=admin_headers)
        assert audits.status_code == 200
        assert {item["action"] for item in audits.json()["data"]} >= {"model.batch_imported", "model.updated"}

        account = await client.post("/admin/accounts", headers=admin_headers, json={"external_user_id": "batch-user", "name": "Batch User"})
        key = await client.post("/admin/api-keys", headers=admin_headers, json={"name": "batch-key", "account_id": account.json()["id"]})
        await client.post(
            f"/admin/accounts/{account.json()['id']}/balance", headers=admin_headers,
            json={"amount_micros": 100_000, "idempotency_key": "batch-import-topup"},
        )
        headers = {"Authorization": f"Bearer {key.json()['key']}"}
        first_call = await client.post("/v1/chat/completions", headers=headers, json={"model": "lok-chat", "messages": [{"role": "user", "content": "hello"}]})
        assert first_call.status_code == 200
        limited_call = await client.post("/v1/chat/completions", headers=headers, json={"model": "lok-chat", "messages": [{"role": "user", "content": "again"}]})
        assert limited_call.status_code == 429
        assert limited_call.headers["retry-after"]


def test_production_startup_configuration_rejects_unsafe_defaults() -> None:
    with pytest.raises(RuntimeError, match="TOKEN_MOCK_MODE"):
        validate_startup_settings(Settings(environment="production"))
    with pytest.raises(RuntimeError, match="TOKEN_LOKSYSTEM_SSO_ENABLED"):
        validate_startup_settings(Settings(
            environment="production",
            auto_create_schema=False,
            mock_mode=False,
            admin_token="a" * 32,
            payment_webhook_secret="b" * 32,
            trial_signing_secret="c" * 32,
            public_base_url="https://token.example.com",
            security_delivery_mode="webhook",
            security_delivery_webhook_url="https://security.example.com/events",
            security_delivery_webhook_secret="d" * 32,
        ))
    validate_startup_settings(Settings(
        environment="production",
        auto_create_schema=False,
        mock_mode=False,
        admin_token="a" * 32,
        payment_webhook_secret="b" * 32,
        trial_signing_secret="c" * 32,
        public_base_url="https://token.example.com",
        security_delivery_mode="webhook",
        security_delivery_webhook_url="https://security.example.com/events",
        security_delivery_webhook_secret="d" * 32,
        loksystem_sso_enabled=False,
    ))


@pytest.mark.asyncio
async def test_admin_accounts_roles_and_revocable_sessions() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        bootstrap = await client.post(
            "/admin/auth/bootstrap",
            headers={"X-Admin-Token": "test-admin"},
            json={"login_id": "root-admin", "password": "correct-horse", "role": "operator"},
        )
        assert bootstrap.status_code == 200
        admin_headers = {"Authorization": f"Bearer {bootstrap.json()['access_token']}"}
        assert (await client.get("/admin/overview", headers={"X-Admin-Token": "test-admin"})).status_code == 401
        created = await client.post(
            "/admin/users", headers=admin_headers,
            json={"login_id": "read-only", "password": "correct-horse", "role": "auditor"},
        )
        assert created.status_code == 200
        login = await client.post("/admin/auth/login", json={"login_id": "read-only", "password": "correct-horse"})
        auditor_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert (await client.get("/admin/audit-events", headers=auditor_headers)).status_code == 200
        assert (await client.post("/admin/accounts", headers=auditor_headers, json={"external_user_id": "blocked", "name": "Blocked"})).status_code == 403
        assert (await client.post("/admin/auth/logout", headers=admin_headers)).status_code == 200
        assert (await client.get("/admin/overview", headers=admin_headers)).status_code == 401


@pytest.mark.asyncio
async def test_password_reset_key_rotation_and_security_sessions() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        registered = await client.post("/auth/register", json={"login_id": "security-user", "name": "Security User", "password": "correct-horse"})
        assert registered.status_code == 200
        first_token = registered.json()["access_token"]
        reset = await client.post("/auth/password-reset/request", json={"login_id": "security-user"})
        assert reset.status_code == 200 and reset.json()["development_reset_token"].startswith("rst_")
        reset_done = await client.post("/auth/password-reset/confirm", json={"reset_token": reset.json()["development_reset_token"], "password": "new-correct-horse"})
        assert reset_done.status_code == 200
        assert (await client.get("/portal/profile", headers={"Authorization": f"Bearer {first_token}"})).status_code == 401
        portal_headers = {"Authorization": f"Bearer {reset_done.json()['access_token']}"}
        key = await client.post("/portal/api-keys", headers=portal_headers, json={"name": "rotating-key"})
        rotated = await client.post(f"/portal/api-keys/{key.json()['id']}/rotate", headers=portal_headers)
        assert rotated.status_code == 200 and rotated.json()["key"].startswith("tok_")
        listed = await client.get("/portal/api-keys", headers=portal_headers)
        old_key = next(item for item in listed.json()["data"] if item["id"] == key.json()["id"])
        assert old_key["active"] is False
        bound = await client.put("/portal/security/contact", headers=portal_headers, json={"contact": "security@example.com", "password": "new-correct-horse"})
        assert bound.status_code == 200
        notices = await client.get("/portal/security-notifications", headers=portal_headers)
        assert {item["event_type"] for item in notices.json()["data"]} >= {"api_key_rotated", "security_contact_bound"}
        assert (await client.post("/portal/security/logout-all", headers=portal_headers)).status_code == 200
        assert (await client.get("/portal/profile", headers=portal_headers)).status_code == 401


@pytest.mark.asyncio
async def test_model_preflight_reports_streaming_token_usage() -> None:
    from app.db import init_db

    init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        models = await client.get("/admin/models", headers={"X-Admin-Token": "test-admin"})
        model = next(item for item in models.json()["data"] if item["public_name"] == "lok-chat")
        report = await client.post(
            f"/admin/models/{model['id']}/preflight",
            headers={"X-Admin-Token": "test-admin"},
            json={"chat_probe": True, "stream_probe": True},
        )
    assert report.status_code == 200
    assert report.json()["chat_probe"]["ok"] is True
    assert report.json()["stream_probe"]["ok"] is True
    assert report.json()["stream_probe"]["token_usage_reported"] is True


@pytest.mark.asyncio
async def test_provider_presets_install_disabled_candidates_without_credentials() -> None:
    transport = httpx.ASGITransport(app=app)
    admin_headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        presets = await client.get("/admin/provider-presets", headers=admin_headers)
        assert presets.status_code == 200
        deepseek = next(item for item in presets.json()["data"] if item["id"] == "deepseek")
        assert deepseek["models"][0]["official_pricing"]["currency"] == "CNY"
        assert deepseek["models"][0]["platform_input_price_micros_per_1k"] == 1584
        assert deepseek["models"][0]["platform_output_price_micros_per_1k"] == 4752
        installed = await client.post(
            "/admin/provider-presets/deepseek/install",
            headers=admin_headers,
            json={"model_ids": deepseek["model_ids"]},
        )
        assert installed.status_code == 200
        assert all(item["active"] is False for item in installed.json()["data"])
        listed = await client.get("/admin/models", headers=admin_headers)
        candidates = {item["public_name"]: item for item in listed.json()["data"] if item["public_name"].startswith("deepseek-v4-")}
        assert set(candidates) == {"deepseek-v4-flash", "deepseek-v4-pro"}
        assert all(item["active"] is False for item in candidates.values())
        duplicate = await client.post(
            "/admin/provider-presets/deepseek/install",
            headers=admin_headers,
            json={"model_ids": [deepseek["model_ids"][0]]},
        )
        assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_oidc_login_links_stable_loksystem_identity(monkeypatch) -> None:
    import app.portal as portal_module

    settings = get_settings()
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "oidc_issuer_url", "https://auth.lokai.example")
    monkeypatch.setattr(settings, "oidc_client_id", "loktoken-web")
    monkeypatch.setattr(settings, "oidc_client_secret", "test-oidc-client-secret")
    monkeypatch.setattr(settings, "oidc_authorization_endpoint", "https://auth.lokai.example/oauth/authorize")
    monkeypatch.setattr(settings, "oidc_token_endpoint", "https://auth.lokai.example/oauth/token")
    monkeypatch.setattr(settings, "oidc_userinfo_endpoint", "https://auth.lokai.example/oauth/userinfo")
    monkeypatch.setattr(settings, "oidc_redirect_uri", "http://testserver/auth/oidc/callback")
    monkeypatch.setattr(settings, "oidc_frontend_redirect_url", "http://testserver/portal")

    class ProviderResponse:
        def __init__(self, payload: dict[str, object]):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.payload

    class ProviderClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, _url: str, **_kwargs) -> ProviderResponse:
            return ProviderResponse({"access_token": "provider-access-token"})

        async def get(self, _url: str, **_kwargs) -> ProviderResponse:
            return ProviderResponse({"sub": "lok-subject-1", "lok_user_id": "lok-user-oidc-1", "name": "Lok OIDC User", "email": "oidc@example.com", "email_verified": True})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
        assert (await client.get("/auth/oidc/status")).json()["enabled"] is True
        started = await client.get("/auth/oidc/start")
        assert started.status_code == 302
        state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
        monkeypatch.setattr(portal_module.httpx, "AsyncClient", ProviderClient)
        completed = await client.get("/auth/oidc/callback", params={"code": "provider-code", "state": state})
        assert completed.status_code == 302, completed.text
        access_token = parse_qs(urlparse(completed.headers["location"]).fragment)["access_token"][0]
        profile = await client.get("/portal/profile", headers={"Authorization": f"Bearer {access_token}"})
        assert profile.status_code == 200
        assert profile.json()["external_user_id"] == "lok-user-oidc-1"

    with SessionLocal() as db:
        assert db.query(ExternalIdentity).filter_by(issuer="https://auth.lokai.example", subject="lok-subject-1").count() == 1


@pytest.mark.asyncio
async def test_loksystem_desktop_sso_creates_and_reuses_a_loktoken_account(monkeypatch) -> None:
    import app.portal as portal_module

    settings = get_settings()
    monkeypatch.setattr(settings, "loksystem_sso_enabled", True)
    monkeypatch.setattr(settings, "loksystem_sso_base_url", "http://127.0.0.1:25809")
    monkeypatch.setattr(settings, "loksystem_sso_issuer", "loksystem://desktop")

    class LokSystemResponse:
        def __init__(self, payload: dict[str, object]):
            self.payload = payload
            self.status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.payload

    class LokSystemClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url: str, **kwargs) -> LokSystemResponse:
            if url.endswith("/tickets"):
                return LokSystemResponse({"success": True, "ticket": "single-use-ticket"})
            assert kwargs["json"] == {"ticket": "single-use-ticket"}
            return LokSystemResponse({"success": True, "user": {"id": "lok-user-sso-1", "username": "Lok Desktop User", "email": "desktop@example.com"}})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
        assert (await client.get("/auth/loksystem/status")).json()["enabled"] is True
        monkeypatch.setattr(portal_module.httpx, "AsyncClient", LokSystemClient)
        first = await client.get("/auth/loksystem/start")
        second = await client.get("/auth/loksystem/start")
        assert first.status_code == 302
        assert second.status_code == 302
        first_token = parse_qs(urlparse(first.headers["location"]).fragment)["access_token"][0]
        second_token = parse_qs(urlparse(second.headers["location"]).fragment)["access_token"][0]
        first_profile = await client.get("/portal/profile", headers={"Authorization": f"Bearer {first_token}"})
        second_profile = await client.get("/portal/profile", headers={"Authorization": f"Bearer {second_token}"})
        assert first_profile.json()["external_user_id"] == "loksystem-lok-user-sso-1"
        assert second_profile.json()["id"] == first_profile.json()["id"]

    with SessionLocal() as db:
        assert db.query(ExternalIdentity).filter_by(issuer="loksystem://desktop", subject="lok-user-sso-1").count() == 1


@pytest.mark.asyncio
async def test_personal_workspaces_organizations_projects_and_key_attribution() -> None:
    from app.db import init_db

    init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        owner = await client.post("/auth/register", json={"login_id": "workspace-owner", "name": "Workspace Owner", "password": "correct-horse"})
        member = await client.post("/auth/register", json={"login_id": "workspace-member", "name": "Workspace Member", "password": "correct-horse"})
        assert owner.status_code == 200 and member.status_code == 200
        owner_headers = {"Authorization": f"Bearer {owner.json()['access_token']}"}
        member_headers = {"Authorization": f"Bearer {member.json()['access_token']}"}

        personal = await client.get("/portal/workspaces", headers=owner_headers)
        assert personal.status_code == 200
        assert personal.json()["data"][0]["type"] == "personal"

        organization = await client.post("/portal/organizations", headers=owner_headers, json={"name": "Lok Team"})
        assert organization.status_code == 200
        workspace_id = organization.json()["workspace_id"]
        added = await client.post(
            f"/portal/workspaces/{workspace_id}/members", headers=owner_headers,
            json={"login_id": "workspace-member", "role": "viewer"},
        )
        assert added.status_code == 200
        member_spaces = await client.get("/portal/workspaces", headers=member_headers)
        assert any(item["id"] == workspace_id and item["role"] == "viewer" for item in member_spaces.json()["data"])
        assert (await client.post(
            f"/portal/workspaces/{workspace_id}/projects", headers=member_headers, json={"name": "Blocked project"},
        )).status_code == 403

        project = await client.post(
            f"/portal/workspaces/{workspace_id}/projects", headers=owner_headers, json={"name": "生产环境", "slug": "production"},
        )
        assert project.status_code == 200
        project_id = project.json()["id"]
        key = await client.post("/portal/api-keys", headers=owner_headers, json={"name": "production-key", "project_id": project_id})
        assert key.status_code == 200
        assert key.json()["project_id"] == project_id

        admin_headers = {"X-Admin-Token": "test-admin"}
        owner_profile = await client.get("/portal/profile", headers=owner_headers)
        await client.post(
            f"/admin/accounts/{owner_profile.json()['id']}/balance", headers=admin_headers,
            json={"amount_micros": 100_000, "idempotency_key": "workspace-test-topup"},
        )
        called = await client.post(
            "/v1/chat/completions", headers={"Authorization": f"Bearer {key.json()['key']}"},
            json={"model": "lok-chat", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert called.status_code == 200

    with SessionLocal() as db:
        record = db.query(UsageRecord).one()
        assert record.project_id == project_id
        assert record.workspace_id == workspace_id


class FakeProviderResponse:
    def __init__(self, status_code: int, payload: dict | None = None, lines: list[str] | None = None):
        self.status_code = status_code
        self.is_error = status_code >= 400
        self._payload = payload or {}
        self._lines = lines or []
        self.text = json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload

    async def aread(self) -> bytes:
        return self.text.encode()

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeProviderStream:
    def __init__(self, response: FakeProviderResponse):
        self.response = response

    async def __aenter__(self) -> FakeProviderResponse:
        return self.response

    async def __aexit__(self, *_args) -> None:
        return None


class FakeProviderClient:
    calls: list[str] = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def post(self, endpoint: str, **_kwargs) -> FakeProviderResponse:
        self.calls.append(endpoint)
        if "primary.invalid" in endpoint:
            return FakeProviderResponse(503, {"error": "primary unavailable"})
        return FakeProviderResponse(200, {
            "id": "chatcmpl-fallback",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "fallback"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
        })

    def stream(self, _method: str, endpoint: str, **_kwargs) -> FakeProviderStream:
        self.calls.append(endpoint)
        if "primary.invalid" in endpoint:
            return FakeProviderStream(FakeProviderResponse(503, {"error": "primary unavailable"}))
        lines = [
            'data: {"id":"chatcmpl-stream","choices":[{"index":0,"delta":{"content":"fallback"},"finish_reason":null}]}',
            'data: {"id":"chatcmpl-stream","choices":[],"usage":{"prompt_tokens":2,"completion_tokens":2,"total_tokens":4}}',
            "data: [DONE]",
        ]
        return FakeProviderStream(FakeProviderResponse(200, lines=lines))


@pytest.mark.asyncio
async def test_channel_failover_opens_circuit_and_uses_backup(monkeypatch) -> None:
    import app.services as services

    settings = get_settings()
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(settings, "channel_failure_threshold", 1)
    FakeProviderClient.calls = []
    admin_headers = {"X-Admin-Token": "test-admin"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        monkeypatch.setattr(services.httpx, "AsyncClient", FakeProviderClient)
        key = await client.post("/admin/api-keys", headers=admin_headers, json={"name": "failover-key"})
        await client.post(
            f"/admin/api-keys/{key.json()['id']}/balance",
            headers=admin_headers,
            json={"amount_micros": 10000, "idempotency_key": "failover-topup"},
        )
        model = await client.post(
            "/admin/models",
            headers=admin_headers,
            json={"public_name": "failover-model", "upstream_model": "primary", "provider_base_url": "https://primary.invalid/v1"},
        )
        model_id = model.json()["id"]
        backup = await client.post(
            f"/admin/models/{model_id}/channels",
            headers=admin_headers,
            json={
                "name": "Backup",
                "upstream_model": "backup",
                "provider_base_url": "https://backup.invalid/v1",
                "priority": 200,
                "weight": 100,
            },
        )
        assert backup.status_code == 200

        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key.json()['key']}"},
            json={"model": "failover-model", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "fallback"
        assert FakeProviderClient.calls == [
            "https://primary.invalid/v1/chat/completions",
            "https://backup.invalid/v1/chat/completions",
        ]

        channels = (await client.get(f"/admin/models/{model_id}/channels", headers=admin_headers)).json()["data"]
        primary = next(item for item in channels if item["name"] == "Primary")
        backup_data = next(item for item in channels if item["name"] == "Backup")
        assert primary["status"] == "unhealthy"
        assert primary["consecutive_failures"] == 1
        assert primary["circuit_open_until"] is not None
        assert backup_data["status"] == "healthy"


@pytest.mark.asyncio
async def test_streaming_failover_only_before_first_chunk(monkeypatch) -> None:
    import app.services as services

    settings = get_settings()
    monkeypatch.setattr(settings, "mock_mode", False)
    FakeProviderClient.calls = []
    admin_headers = {"X-Admin-Token": "test-admin"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        monkeypatch.setattr(services.httpx, "AsyncClient", FakeProviderClient)
        key = await client.post("/admin/api-keys", headers=admin_headers, json={"name": "stream-failover-key"})
        await client.post(
            f"/admin/api-keys/{key.json()['id']}/balance",
            headers=admin_headers,
            json={"amount_micros": 10000, "idempotency_key": "stream-failover-topup"},
        )
        model = await client.post(
            "/admin/models",
            headers=admin_headers,
            json={"public_name": "stream-failover-model", "upstream_model": "primary", "provider_base_url": "https://primary.invalid/v1"},
        )
        await client.post(
            f"/admin/models/{model.json()['id']}/channels",
            headers=admin_headers,
            json={
                "name": "Backup",
                "upstream_model": "backup",
                "provider_base_url": "https://backup.invalid/v1",
                "priority": 200,
                "weight": 100,
            },
        )
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key.json()['key']}"},
            json={"model": "stream-failover-model", "messages": [{"role": "user", "content": "hello"}], "stream": True},
        )
        assert response.status_code == 200
        assert response.text.count('"content":"fallback"') == 1
        assert response.text.count("data: [DONE]") == 1
        assert FakeProviderClient.calls == [
            "https://primary.invalid/v1/chat/completions",
            "https://backup.invalid/v1/chat/completions",
        ]
