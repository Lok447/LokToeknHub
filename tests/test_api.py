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
from app.models import ApiKey, ExternalIdentity, ModelChannel, UsageRecord


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


def test_provider_catalogue_is_seeded_once_with_disabled_channels() -> None:
    from app.db import init_db
    from app.models import ModelChannel, ModelConfig

    init_db()
    init_db()
    expected = {
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "qwen/qwen-plus",
        "qwen/qwen-image-plus",
        "qwen/wan2.1-t2v-turbo",
        "glm/glm-4.5",
        "glm/cogview-4",
        "glm/cogvideox-flash",
        "kimi/kimi-k2-0905-preview",
        "minimax/MiniMax-M2.1",
        "minimax/image-01",
        "minimax/video-01",
        "doubao/doubao-seed-1-6",
        "doubao/doubao-seedream-4-0",
        "doubao/doubao-seedance-1-0-pro",
    }
    with SessionLocal() as db:
        seeded = db.query(ModelConfig).filter(ModelConfig.public_name.in_(expected)).all()
        assert {model.public_name for model in seeded} == expected
        assert len(seeded) == len(expected)
        assert all(model.active is False for model in seeded)
        channels = db.query(ModelChannel).filter(ModelChannel.model_config_id.in_([model.id for model in seeded])).all()
        assert len(channels) == len(expected)
        assert all(channel.active is False for channel in channels)
        metadata = {model.public_name: json.loads(model.catalog_metadata_json or "{}") for model in seeded}
        assert metadata["qwen/qwen-image-plus"]["api_type"] == "images_generations"
        assert metadata["doubao/doubao-seedance-1-0-pro"]["api_type"] == "video_generations"


def test_provider_catalogue_enriches_existing_model_without_overwriting_routing() -> None:
    from app.db import init_db
    from app.models import ModelConfig

    with SessionLocal() as db:
        db.add(ModelConfig(
            public_name="deepseek-v4-flash",
            upstream_model="existing-route",
            provider_base_url="https://existing.invalid/v1",
            input_price_micros_per_1k=123,
            output_price_micros_per_1k=456,
            active=True,
        ))
        db.commit()
    init_db()
    with SessionLocal() as db:
        model = db.query(ModelConfig).filter(ModelConfig.public_name == "deepseek-v4-flash").one()
        assert model.upstream_model == "existing-route"
        assert model.provider_base_url == "https://existing.invalid/v1"
        assert model.input_price_micros_per_1k == 123
        assert model.output_price_micros_per_1k == 456
        assert model.active is True
        assert json.loads(model.catalog_metadata_json or "{}")["provider"] == "DeepSeek"
        assert json.loads(model.official_pricing_json or "{}")["currency"] == "CNY"


def test_real_mode_removes_legacy_mock_models(monkeypatch) -> None:
    from app.db import init_db
    from app.models import ModelConfig

    settings = get_settings()
    monkeypatch.setattr(settings, "mock_mode", False)
    legacy_names = {
        "lok-chat",
        "lok-reason",
        "lok-vision",
        "smoke-model",
        "deepseek/deepseek-chat",
        "deepseek/deepseek-reasoner",
    }
    with SessionLocal() as db:
        for name in legacy_names:
            db.add(ModelConfig(
                public_name=name,
                upstream_model=name,
                provider_base_url="https://mock.invalid/v1",
                input_price_micros_per_1k=1,
                output_price_micros_per_1k=1,
            ))
        db.add(ModelConfig(
            public_name="deepseek-v4-flash",
            upstream_model="deepseek-v4-flash",
            provider_base_url="https://api.deepseek.com/v1",
            input_price_micros_per_1k=3_000_000,
            output_price_micros_per_1k=9_000_000,
            active=True,
        ))
        db.commit()
    init_db()
    with SessionLocal() as db:
        remaining = db.query(ModelConfig.public_name).filter(ModelConfig.public_name.in_(legacy_names)).all()
        normalized = db.query(ModelConfig).filter(ModelConfig.public_name == "deepseek-v4-flash").one()
    assert remaining == []
    assert (normalized.input_price_micros_per_1k, normalized.output_price_micros_per_1k) == (3000, 9000)


@pytest.mark.asyncio
async def test_admin_runtime_and_model_publication_state_are_explicit() -> None:
    from app.db import init_db

    init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        runtime = await client.get("/admin/runtime", headers={"X-Admin-Token": "test-admin"})
        assert runtime.status_code == 200
        assert runtime.json()["data_mode"] == "mock"
        assert runtime.json()["mock_published_model_count"] >= 1
        assert runtime.json()["release_ready"] is True
        models = await client.get("/admin/models", headers={"X-Admin-Token": "test-admin"})
        assert models.status_code == 200
        assert any(item["publication_state"] == "mock_published" for item in models.json()["data"])


@pytest.mark.asyncio
async def test_provider_secret_cannot_be_stored_as_environment_variable_name() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/admin/models",
            headers={"X-Admin-Token": "test-admin"},
            json={
                "public_name": "unsafe-secret-model",
                "upstream_model": "unsafe",
                "provider_base_url": "https://provider.invalid/v1",
                "provider_api_key_env": "sk-raw-secret-must-not-be-stored",
            },
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_channel_health_explains_missing_provider_environment_variable(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.delenv("MISSING_PROVIDER_API_KEY", raising=False)
    transport = httpx.ASGITransport(app=app)
    headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        model = await client.post(
            "/admin/models",
            headers=headers,
            json={
                "public_name": "missing-credential-model",
                "upstream_model": "missing",
                "provider_base_url": "https://provider.invalid/v1",
                "provider_api_key_env": "MISSING_PROVIDER_API_KEY",
            },
        )
        channels = await client.get(f"/admin/models/{model.json()['id']}/channels", headers=headers)
        checked = await client.post(f"/admin/channels/{channels.json()['data'][0]['id']}/check", headers=headers)
    assert checked.status_code == 200
    assert checked.json()["healthy"] is False
    assert checked.json()["detail"] == "供应商密钥环境变量未配置: MISSING_PROVIDER_API_KEY"


@pytest.mark.asyncio
async def test_channel_health_rejects_unknown_upstream_model(monkeypatch) -> None:
    import app.services as services
    from app.models import ModelChannel, ModelConfig

    class ModelListResponse:
        is_error = False

        def json(self) -> dict[str, object]:
            return {"object": "list", "data": [{"id": "supported-model"}]}

    class ModelListClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, *_args, **_kwargs) -> ModelListResponse:
            return ModelListResponse()

    settings = get_settings()
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(settings, "default_provider_api_key", "test-provider-key")
    monkeypatch.setattr(services.httpx, "AsyncClient", ModelListClient)
    with SessionLocal() as db:
        model = ModelConfig(
            public_name="catalogue-mismatch",
            upstream_model="missing-model",
            provider_base_url="https://provider.invalid/v1",
            input_price_micros_per_1k=1000,
            output_price_micros_per_1k=2000,
        )
        db.add(model)
        db.flush()
        channel = ModelChannel(
            model_config_id=model.id,
            name="Primary",
            provider_base_url=model.provider_base_url,
            upstream_model=model.upstream_model,
        )
        db.add(channel)
        db.commit()
        result = await services.check_channel_health(db, channel)

    assert result["healthy"] is False
    assert "供应商模型目录不包含上游模型: missing-model" in result["detail"]


@pytest.mark.asyncio
async def test_console_managed_provider_key_is_encrypted_and_never_returned() -> None:
    transport = httpx.ASGITransport(app=app)
    headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        model = await client.post(
            "/admin/models",
            headers=headers,
            json={"public_name": "console-secret-model", "upstream_model": "secret-model", "provider_base_url": "https://provider.invalid/v1"},
        )
        channel = await client.post(
            f"/admin/models/{model.json()['id']}/channels",
            headers=headers,
            json={
                "name": "Console credential",
                "upstream_model": "secret-model",
                "provider_base_url": "https://provider.invalid/v1",
                "provider_api_key": "provider-secret-value",
            },
        )
    assert channel.status_code == 200
    assert channel.json()["credentials_configured"] is True
    assert channel.json()["credential_source"] == "console"
    assert "provider-secret-value" not in channel.text
    from app.models import ModelChannel
    with SessionLocal() as db:
        stored = db.query(ModelChannel).filter(ModelChannel.id == channel.json()["id"]).one()
        assert stored.encrypted_api_key
        assert stored.encrypted_api_key != "provider-secret-value"


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
async def test_sidebar_navigation_contract_and_backing_endpoints() -> None:
    from app.db import init_db

    init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        admin_page = (await client.get("/")).text
        portal_page = (await client.get("/portal")).text
        admin_script = (await client.get("/static/app.js")).text
        portal_script = (await client.get("/static/portal.js")).text

        assert "查看管理文档" not in admin_page
        assert "首次初始化管理员" not in admin_page
        assert 'id="show-bootstrap"' not in admin_page
        assert 'id="bootstrap-form"' not in admin_page
        assert "show-bootstrap" not in admin_script
        assert "bootstrap-form" not in admin_script
        admin_sidebar = admin_page.split('<aside class="sidebar">', 1)[1].split("</aside>", 1)[0]
        portal_sidebar = portal_page.split('<aside class="sidebar">', 1)[1].split("</aside>", 1)[0]
        assert "管理文档" not in admin_sidebar and "用户文档" not in portal_sidebar
        assert 'id="admin-account-trigger"' in admin_sidebar and 'id="admin-account-menu"' in admin_sidebar
        assert 'id="portal-account-trigger"' in portal_sidebar and 'id="portal-account-menu"' in portal_sidebar
        assert "个人空间" in admin_sidebar and "个人空间" not in portal_sidebar
        assert '<section class="sidebar-workspace"' not in portal_sidebar
        portal_account_menu = portal_sidebar.split('id="portal-account-menu"', 1)[1]
        assert 'class="account-menu-title"' not in portal_account_menu
        assert 'id="portal-workspace"' not in portal_account_menu
        assert 'id="portal-workspace-manager"' in portal_account_menu
        assert 'id="portal-security"' in portal_account_menu
        assert 'id="workspace-manager-select"' in portal_script
        assert 'id="admin-guide-link"' in admin_page and 'id="portal-guide-link"' in portal_page
        assert 'href="/guide/admin"' in admin_page and 'href="/guide/user"' in portal_page
        assert 'id="portal-register-link"' in portal_page and "无法登录" not in portal_page
        assert 'data-auth-mode="register"' not in portal_page
        assert 'id="portal-register-form"' in portal_page
        assert 'setAuthMode("register")' in portal_script
        assert 'document.getElementById("admin-guide-link").hidden = view !== "overview";' in admin_script
        assert 'document.getElementById("portal-guide-link").hidden = view !== "overview";' in portal_script

        admin_nav = ["管理概览", "模型管理", "账户管理", "API管理", "订单管理", "福利管理", "用量管理", "安全审计"]
        portal_nav = ["用户概览", "模型广场", "额度管理", "密钥管理", "请求记录", "订单管理", "兑换福利"]
        admin_nav_markup = admin_page.split('<nav aria-label="主导航">', 1)[1].split("</nav>", 1)[0]
        portal_nav_markup = portal_page.split('<nav aria-label="用户导航">', 1)[1].split("</nav>", 1)[0]
        assert [admin_nav_markup.index(label) for label in admin_nav] == sorted(admin_nav_markup.index(label) for label in admin_nav)
        assert [portal_nav_markup.index(label) for label in portal_nav] == sorted(portal_nav_markup.index(label) for label in portal_nav)
        assert all(f'{key}: "{label}"' in admin_script for key, label in {
            "overview": "管理概览", "models": "模型管理", "accounts": "账户管理", "keys": "API管理",
            "payments": "订单管理", "redemptions": "福利管理", "usage": "用量管理", "audit": "安全审计",
        }.items())
        assert all(f'{key}: "{label}"' in portal_script for key, label in {
            "overview": "用户概览", "models": "模型广场", "quota": "额度管理", "keys": "密钥管理",
            "usage": "请求记录", "orders": "订单管理", "redeem": "兑换福利",
        }.items())

        bootstrap = await client.post(
            "/admin/auth/bootstrap",
            headers={"X-Admin-Token": "test-admin"},
            json={"login_id": "navigation-admin", "password": "correct-horse"},
        )
        admin_headers = {"Authorization": f"Bearer {bootstrap.json()['access_token']}"}
        for path in (
            "/admin/overview", "/admin/models", "/admin/accounts", "/admin/api-keys",
            "/admin/payment-orders", "/admin/redemption-codes", "/admin/usage",
            "/admin/usage/records", "/admin/audit-events", "/admin/runtime",
        ):
            response = await client.get(path, headers=admin_headers)
            assert response.status_code == 200, f"{path}: {response.text}"

        registered = await client.post(
            "/auth/register",
            json={"login_id": "navigation-user", "name": "Navigation User", "password": "correct-horse"},
        )
        portal_headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
        for path in (
            "/portal/profile", "/portal/workspaces", "/portal/models", "/portal/balance-summary",
            "/portal/api-keys", "/portal/usage", "/portal/usage/records?page=1&page_size=20",
            "/portal/payment-orders", "/portal/redemptions",
        ):
            response = await client.get(path, headers=portal_headers)
            assert response.status_code == 200, f"{path}: {response.text}"


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

        model_detail = await client.get("/v1/models/demo-model", headers={"Authorization": f"Bearer {api_key}"})
        assert model_detail.status_code == 200
        assert model_detail.json() == {"id": "demo-model", "object": "model", "owned_by": "token"}

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
        assert empty_call.json()["error"]["code"] == "insufficient_balance"
        assert "账户余额不足" in empty_call.json()["error"]["message"]

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
        assert '<span>API管理</span>' not in portal_page.text
        assert 'src="/static/portal.js?v=portal-20260819-11"' in portal_page.text
        assert '<button type="button" class="active" data-auth-mode="login">账号登录</button>' in portal_page.text
        assert 'id="portal-forgot-password"' in portal_page.text
        assert 'id="portal-register-contact"' in portal_page.text
        assert 'data-auth-mode="trial">试用入口</button>' in portal_page.text
        assert portal_page.text.index('id="portal-integration-guide"') < portal_page.text.index('class="overview-quickbar panel"')
        assert '<strong>LokToken</strong>' in portal_page.text
        assert '<p class="sidebar-section-label">工作台</p>' not in portal_page.text
        assert 'class="topbar-actions"' in portal_page.text
        assert 'id="portal-workspace-manager"' in portal_page.text
        assert 'id="portal-security"' in portal_page.text
        assert 'id="portal-refresh"' in portal_page.text
        for local_refresh_id in ("keys-refresh", "quota-refresh", "orders-refresh", "redeem-refresh", "usage-refresh"):
            assert f'id="{local_refresh_id}"' not in portal_page.text
        assert "LokSystem 一键注册 / 登录" in portal_page.text
        assert 'id="loksystem-login-button"' in portal_page.text
        assert portal_page.text.index('id="portal-login-form"') < portal_page.text.index('id="loksystem-login-button"') < portal_page.text.index('id="portal-register-form"')
        assert 'id="portal-integration-guide"' in portal_page.text
        assert "重置密码" not in portal_page.text
        assert "查看用户文档" not in portal_page.text
        assert "试用令牌（trl_ 开头）" in portal_page.text
        portal_script = await client.get("/static/portal.js?v=portal-20260819-11")
        assert portal_script.headers["cache-control"] == "no-store"
        assert "复制并前往 LokSystem" in portal_script.text
        assert "loksystem://add-provider?platform=LokToken" in portal_script.text
        assert "应用接入" in portal_script.text
        assert "在应用中完成配置" in portal_script.text
        assert 'keys: "密钥管理"' in portal_script.text
        for local_refresh_id in ("keys-refresh", "quota-refresh", "orders-refresh", "redeem-refresh", "usage-refresh"):
            assert local_refresh_id not in portal_script.text
        assert "if (loksystemStatus.enabled) loksystemLoginButton.hidden = false;" in portal_script.text
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
            provider_secrets_key="p" * 32,
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
        provider_secrets_key="p" * 32,
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
        registered = await client.post("/auth/register", json={"login_id": "security-user", "name": "Security User", "password": "correct-horse", "security_contact": "security@example.com"})
        assert registered.status_code == 200
        first_token = registered.json()["access_token"]
        profile = await client.get("/portal/profile", headers={"Authorization": f"Bearer {first_token}"})
        assert profile.json()["security_contact"] == "security@example.com"
        assert profile.json()["security_contact_verified_at"] is None
        unverified_reset = await client.post("/auth/password-reset/request", json={"login_id": "security-user"})
        assert unverified_reset.status_code == 200 and "development_reset_token" not in unverified_reset.json()
        verification = await client.post(
            "/portal/security/contact/confirm",
            headers={"Authorization": f"Bearer {first_token}"},
            json={"verification_token": registered.json()["development_verification_token"]},
        )
        assert verification.status_code == 200 and verification.json()["security_contact_verified_at"]
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
        rebound = await client.post(
            "/portal/security/contact/confirm",
            headers=portal_headers,
            json={"verification_token": bound.json()["development_verification_token"]},
        )
        assert rebound.status_code == 200
        notices = await client.get("/portal/security-notifications", headers=portal_headers)
        assert {item["event_type"] for item in notices.json()["data"]} >= {"api_key_rotated", "security_contact_verified"}
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
    monkeypatch.setattr(settings, "default_provider_api_key", "test-provider")
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
            json={"public_name": "failover-model", "upstream_model": "primary", "provider_base_url": "https://primary.invalid/v1", "input_price_micros_per_1k": 1000, "output_price_micros_per_1k": 2000},
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
        from app.models import ModelChannel
        with SessionLocal() as db:
            for channel in db.query(ModelChannel).filter(ModelChannel.model_config_id == model_id).all():
                channel.status = "healthy"
                channel.health_source = "provider"
            db.commit()

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
async def test_p0_api_metadata_and_provider_route_cost_are_auditable(monkeypatch) -> None:
    import app.services as services

    settings = get_settings()
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(settings, "default_provider_api_key", "test-provider")

    class AuditedProviderClient(FakeProviderClient):
        async def post(self, endpoint: str, **kwargs) -> FakeProviderResponse:
            assert kwargs["json"]["max_tokens"] == 12
            assert kwargs["json"]["response_format"] == {"type": "json_object"}
            return FakeProviderResponse(200, {
                "id": "provider-request-audited",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                    "prompt_cache_hit_tokens": 2,
                    "prompt_cache_miss_tokens": 8,
                    "completion_tokens_details": {"reasoning_tokens": 1},
                },
            })

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        admin_headers = {"X-Admin-Token": "test-admin"}
        key = await client.post("/admin/api-keys", headers=admin_headers, json={"name": "p0-audit-key"})
        await client.post(f"/admin/api-keys/{key.json()['id']}/balance", headers=admin_headers, json={"amount_micros": 100_000, "idempotency_key": "p0-audit-topup"})
        model = await client.post("/admin/models", headers=admin_headers, json={"public_name": "p0-audit-model", "upstream_model": "provider-model", "provider_base_url": "https://provider.invalid/v1", "input_price_micros_per_1k": 1000, "output_price_micros_per_1k": 2000})
        channel_id = (await client.get(f"/admin/models/{model.json()['id']}/channels", headers=admin_headers)).json()["data"][0]["id"]
        await client.patch(f"/admin/channels/{channel_id}", headers=admin_headers, json={"status": "healthy", "provider_input_cost_micros_per_1k": 500, "provider_output_cost_micros_per_1k": 1500})
        # The status is intentionally set through the test DB below; the public
        # channel update API does not allow operators to forge health state.
        with SessionLocal() as db:
            channel = db.get(ModelChannel, channel_id)
            channel.status = "healthy"
            channel.health_source = "provider"
            db.commit()
        monkeypatch.setattr(services.httpx, "AsyncClient", AuditedProviderClient)
        response = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {key.json()['key']}"}, json={"model": "p0-audit-model", "messages": [{"role": "user", "content": "hello"}], "max_completion_tokens": 12, "response_format": {"type": "json_object"}})
        models = await client.get("/v1/models", headers={"Authorization": f"Bearer {key.json()['key']}"})
        metrics = await client.get("/metrics")

    assert response.status_code == 200
    assert models.status_code == 200 and models.json()["data"][-1]["supported_parameters"]
    assert metrics.status_code == 200 and "loktoken_http_requests_total" in metrics.text
    with SessionLocal() as db:
        record = db.query(UsageRecord).filter(UsageRecord.request_id == response.headers["X-Request-ID"]).one()
        assert record.provider_request_id == "provider-request-audited"
        assert record.provider_channel_id == channel_id
        assert record.provider_cost_micros == 11
        assert record.input_cache_hit_tokens == 2
        assert record.input_cache_miss_tokens == 8
        assert record.reasoning_tokens == 1


@pytest.mark.asyncio
async def test_p1_operational_alerts_surface_release_blockers() -> None:
    from app.models import BillingAccount, ModelConfig, PaymentOrder

    settings = get_settings()
    settings.alert_low_balance_micros = 1_000_000
    settings.alert_lookback_minutes = 15
    settings.alert_failure_rate_percent = 20
    settings.alert_min_request_count = 5
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        account = BillingAccount(external_user_id="alert-user", login_id="alert-user", name="Alert User", balance_micros=100)
        db.add(account)
        db.flush()
        db.add_all([
            ApiKey(name="expired", account_id=account.id, key_prefix="expired", key_hash="expired-alert-key", expires_at=now - timedelta(minutes=1)),
            ApiKey(name="expiring", account_id=account.id, key_prefix="expiring", key_hash="expiring-alert-key", expires_at=now + timedelta(days=3)),
        ])
        model = ModelConfig(public_name="alert-model", upstream_model="alert-model", provider_base_url="https://provider.invalid/v1", input_price_micros_per_1k=1000, output_price_micros_per_1k=1000)
        db.add(model)
        db.flush()
        db.add(ModelChannel(model_config_id=model.id, name="Alert primary", provider_base_url=model.provider_base_url, upstream_model=model.upstream_model, active=True, status="unhealthy", consecutive_failures=3))
        for index in range(5):
            db.add(UsageRecord(request_id=f"alert-{index}", trace_id=f"trace-alert-{index}", account_id=account.id, api_key_id=1, model=model.public_name, upstream_model=model.upstream_model, input_tokens=1, output_tokens=1, total_tokens=2, amount_micros=10, provider_cost_micros=20 if index == 0 else 0, status="success" if index == 0 else "error", latency_ms=10, created_at=now))
        db.add(PaymentOrder(order_no="alert-order", account_id=account.id, amount_micros=1000, provider="manual", status="pending"))
        db.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/admin/alerts", headers={"X-Admin-Token": "test-admin"})
        overview = await client.get("/admin/overview", headers={"X-Admin-Token": "test-admin"})

    assert response.status_code == 200
    payload = response.json()
    codes = {item["code"] for item in payload["data"]}
    assert {"low_balance", "expired_keys", "expiring_keys", "unhealthy_channels", "failure_rate", "cost_anomaly", "pending_orders"} <= codes
    assert payload["release_blocking_count"] >= 3
    assert overview.status_code == 200
    assert overview.json()["alert_count"] == payload["count"]


@pytest.mark.asyncio
async def test_alert_webhook_is_deduplicated_and_recovery_is_delivered(monkeypatch) -> None:
    import app.main as main_module
    from app.models import BillingAccount, PaymentOrder

    delivered: list[dict[str, object]] = []

    class WebhookResponse:
        def raise_for_status(self) -> None:
            return None

    def fake_post(_url: str, *, content: bytes, headers: dict[str, str], timeout: int) -> WebhookResponse:
        assert timeout == 10
        assert headers["X-LokToken-Signature"]
        delivered.append(json.loads(content))
        return WebhookResponse()

    settings = get_settings()
    monkeypatch.setattr(settings, "security_delivery_mode", "webhook")
    monkeypatch.setattr(settings, "security_delivery_webhook_url", "https://security.example.com/events")
    monkeypatch.setattr(settings, "security_delivery_webhook_secret", "alert-webhook-secret-long-enough")
    monkeypatch.setattr(main_module.httpx, "post", fake_post)
    with SessionLocal() as db:
        account = BillingAccount(external_user_id="alert-webhook-user", name="Alert Webhook User", balance_micros=2_000_000)
        db.add(account)
        db.flush()
        order = PaymentOrder(order_no="alert-webhook-order", account_id=account.id, amount_micros=1000, provider="manual", status="pending")
        db.add(order)
        db.commit()
        order_id = order.id

    transport = httpx.ASGITransport(app=app)
    headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = await client.post("/admin/alerts/evaluate", headers=headers)
        second = await client.post("/admin/alerts/evaluate", headers=headers)
        with SessionLocal() as db:
            db.get(PaymentOrder, order_id).status = "rejected"
            db.commit()
        recovered = await client.post("/admin/alerts/evaluate", headers=headers)
        final = await client.post("/admin/alerts/evaluate", headers=headers)

    assert first.status_code == second.status_code == recovered.status_code == final.status_code == 200
    assert [item["event"] for item in delivered] == ["alert_opened", "alert_recovered"]
    assert delivered[0]["fingerprint"] == delivered[1]["fingerprint"]


@pytest.mark.asyncio
async def test_provider_bill_import_reconciles_each_line_and_is_idempotent() -> None:
    from app.models import BillingAccount

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        account = BillingAccount(external_user_id="bill-user", name="Bill User", balance_micros=1_000_000)
        db.add(account)
        db.flush()
        api_key = ApiKey(name="bill-key", account_id=account.id, key_prefix="bill", key_hash="bill-key-hash")
        db.add(api_key)
        db.flush()
        db.add_all([
            UsageRecord(request_id="bill-platform-1", trace_id="bill-platform-1", account_id=account.id, api_key_id=api_key.id, model="bill-model", upstream_model="bill-model", input_tokens=10, output_tokens=5, total_tokens=15, amount_micros=50, provider_cost_micros=30, provider_request_id="provider-bill-1", status="success", latency_ms=10, created_at=now),
            UsageRecord(request_id="bill-platform-2", trace_id="bill-platform-2", account_id=account.id, api_key_id=api_key.id, model="bill-model", upstream_model="bill-model", input_tokens=20, output_tokens=6, total_tokens=26, amount_micros=80, provider_cost_micros=40, provider_request_id="provider-bill-2", status="success", latency_ms=10, created_at=now),
        ])
        db.commit()

    payload = {
        "provider": "DeepSeek",
        "source_name": "deepseek-2026-08-20.json",
        "lines": [
            {"provider_request_id": "provider-bill-1", "input_tokens": 10, "output_tokens": 5, "billed_cost_micros": 30},
            {"provider_request_id": "provider-bill-2", "input_tokens": 20, "output_tokens": 6, "billed_cost_micros": 55},
            {"provider_request_id": "provider-missing", "input_tokens": 1, "output_tokens": 1, "billed_cost_micros": 10},
        ],
    }
    transport = httpx.ASGITransport(app=app)
    headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        imported = await client.post("/admin/provider-bills/import", headers=headers, json=payload)
        duplicate = await client.post("/admin/provider-bills/import", headers=headers, json=payload)
        listed = await client.get("/admin/provider-bills", headers=headers)
        detail = await client.get(f"/admin/provider-bills/{imported.json()['import']['id']}", headers=headers)

    assert imported.status_code == 200
    summary = imported.json()["import"]
    assert (summary["matched_count"], summary["mismatch_count"], summary["unmatched_count"]) == (1, 1, 1)
    assert summary["difference_micros"] == 25
    assert duplicate.status_code == 200 and duplicate.json()["duplicate"] is True
    assert len(listed.json()["data"]) == 1
    assert [item["status"] for item in detail.json()["lines"]] == ["matched", "mismatch", "unmatched"]

@pytest.mark.asyncio
async def test_streaming_failover_only_before_first_chunk(monkeypatch) -> None:
    import app.services as services

    settings = get_settings()
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(settings, "default_provider_api_key", "test-provider")
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
            json={"public_name": "stream-failover-model", "upstream_model": "primary", "provider_base_url": "https://primary.invalid/v1", "input_price_micros_per_1k": 1000, "output_price_micros_per_1k": 2000},
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
        from app.models import ModelChannel
        with SessionLocal() as db:
            for channel in db.query(ModelChannel).filter(ModelChannel.model_config_id == model.json()["id"]).all():
                channel.status = "healthy"
                channel.health_source = "provider"
            db.commit()
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
