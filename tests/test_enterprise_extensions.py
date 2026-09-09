import json
import os
import tempfile
from pathlib import Path

import httpx
import pytest

_db_path = Path(tempfile.gettempdir()) / "loktoken-enterprise-extension-tests.db"
os.environ["TOKEN_DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"
os.environ["TOKEN_ADMIN_TOKEN"] = "test-admin"
os.environ["TOKEN_MOCK_MODE"] = "true"
os.environ["TOKEN_SEED_BUILTIN_MODELS"] = "true"
os.environ["TOKEN_SCIM_ENABLED"] = "true"
os.environ["TOKEN_SCIM_BEARER_TOKEN"] = "scim-test"

from app.db import Base, engine
from app.main import app
from app.protocols import anthropic_to_openai, gemini_to_openai, responses_to_openai


@pytest.fixture(autouse=True)
def reset_db():
    from app.config import get_settings
    settings = get_settings()
    settings.scim_enabled = True
    settings.scim_bearer_token = "scim-test"
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def test_protocol_adapters_normalize_common_shapes():
    anthropic = anthropic_to_openai({"model": "m", "system": "be brief", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 12})
    assert anthropic["messages"][0] == {"role": "system", "content": "be brief"}
    assert anthropic["messages"][1]["content"] == "hi"
    gemini = gemini_to_openai({"contents": [{"role": "user", "parts": [{"text": "hi"}]}], "generationConfig": {"maxOutputTokens": 8}}, "m")
    assert gemini["messages"] == [{"role": "user", "content": "hi"}]
    responses = responses_to_openai({"model": "m", "input": "hi", "max_output_tokens": 8})
    assert responses["max_completion_tokens"] == 8


@pytest.mark.asyncio
async def test_scim_create_list_and_deactivate():
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer scim-test"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post("/scim/v2/Users", headers=headers, json={"userName": "scim-user", "displayName": "SCIM User", "externalId": "ext-1"})
        assert created.status_code == 200
        user_id = created.json()["id"]
        listed = await client.get("/scim/v2/Users", headers=headers)
        assert listed.status_code == 200 and listed.json()["totalResults"] == 1
        disabled = await client.patch(f"/scim/v2/Users/{user_id}", headers=headers, json={"active": False})
        assert disabled.status_code == 200 and disabled.json()["active"] is False


@pytest.mark.asyncio
async def test_invoice_lifecycle_requires_admin_finance_role():
    transport = httpx.ASGITransport(app=app)
    headers = {"X-Admin-Token": "test-admin"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        account = await client.post("/admin/accounts", headers=headers, json={"name": "Invoice Account", "external_user_id": "invoice-account"})
        invoice = await client.post("/admin/invoices", headers=headers, json={"account_id": account.json()["id"], "amount_micros": 1234})
        assert invoice.status_code == 200
        issued = await client.patch(f"/admin/invoices/{invoice.json()['id']}", headers=headers, json={"status": "issued", "file_url": "https://billing.example/inv.pdf"})
        assert issued.status_code == 200 and issued.json()["status"] == "issued"
