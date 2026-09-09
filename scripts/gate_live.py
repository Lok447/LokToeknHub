"""Run destructive end-to-end checks against an isolated LokToken Gate stack."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid


BASE_URL = os.getenv("GATE_BASE_URL", "http://127.0.0.1:18000").rstrip("/")
ADMIN_TOKEN = os.getenv("GATE_ADMIN_TOKEN", "")


def request(
    method: str,
    path: str,
    *,
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    expected: int | tuple[int, ...] = 200,
) -> tuple[int, object]:
    expected_codes = (expected,) if isinstance(expected, int) else expected
    data = json.dumps(body).encode() if body is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE_URL + path, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    if status not in expected_codes:
        text = raw.decode(errors="replace")
        raise AssertionError(f"{method} {path}: expected {expected_codes}, got {status}: {text[:500]}")
    if not raw:
        return status, {}
    content_type = ""
    try:
        content_type = response.headers.get("content-type", "")  # type: ignore[possibly-undefined]
    except UnboundLocalError:
        pass
    if "json" in content_type or raw.lstrip().startswith((b"{", b"[")):
        return status, json.loads(raw)
    return status, raw.decode(errors="replace")


def require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    if len(ADMIN_TOKEN) < 24:
        raise RuntimeError("GATE_ADMIN_TOKEN must contain the isolated stack bootstrap token")

    suffix = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    admin_login = f"gate-root-{suffix}"
    user_login = f"gate-user-{suffix}"
    member_login = f"gate-member-{suffix}"
    password = "Gate-only-password-2026"
    bootstrap_headers = {"X-Admin-Token": ADMIN_TOKEN}

    _, health = request("GET", "/healthz")
    _, ready = request("GET", "/readyz")
    require(health == {"status": "ok"} and ready == {"status": "ready"}, "health checks failed")

    _, bootstrap = request(
        "POST",
        "/admin/auth/bootstrap",
        headers=bootstrap_headers,
        body={"login_id": admin_login, "password": password, "role": "superadmin"},
    )
    admin_headers = {"Authorization": f"Bearer {bootstrap['access_token']}"}
    request("GET", "/admin/overview", headers=bootstrap_headers, expected=401)
    _, current_admin = request("GET", "/admin/auth/me", headers=admin_headers)
    require(current_admin["admin"]["role"] == "superadmin", "bootstrap administrator is not superadmin")

    _, auditor = request(
        "POST",
        "/admin/users",
        headers=admin_headers,
        body={"login_id": f"gate-auditor-{suffix}", "password": password, "role": "auditor"},
    )
    _, auditor_login = request(
        "POST",
        "/admin/auth/login",
        body={"login_id": auditor["login_id"], "password": password},
    )
    auditor_headers = {"Authorization": f"Bearer {auditor_login['access_token']}"}
    request("GET", "/admin/audit-events", headers=auditor_headers)
    request(
        "POST",
        "/admin/accounts",
        headers=auditor_headers,
        body={"external_user_id": f"forbidden-{suffix}", "name": "Forbidden"},
        expected=403,
    )

    _, registered = request(
        "POST", "/auth/register", body={"login_id": user_login, "name": "Gate User", "password": password}
    )
    account = registered["account"]
    portal_headers = {"Authorization": f"Bearer {registered['access_token']}"}
    _, member = request(
        "POST", "/auth/register", body={"login_id": member_login, "name": "Gate Member", "password": password}
    )
    member_headers = {"Authorization": f"Bearer {member['access_token']}"}

    admin_gets = (
        "/admin/overview", "/admin/models", "/admin/accounts", "/admin/api-keys",
        "/admin/payment-orders", "/admin/redemption-codes", "/admin/usage",
        "/admin/usage/records", "/admin/audit-events", "/admin/runtime",
        "/admin/reconciliation", "/admin/provider-presets",
    )
    for path in admin_gets:
        request("GET", path, headers=admin_headers)

    portal_gets = (
        "/portal/profile", "/portal/workspaces", "/portal/models", "/portal/balance-summary",
        "/portal/api-keys", "/portal/usage", "/portal/usage/records?page=1&page_size=20",
        "/portal/usage/analytics?granularity=hour", "/portal/dashboard?days=7",
        "/portal/payment-orders", "/portal/redemptions", "/portal/security-notifications",
    )
    for path in portal_gets:
        request("GET", path, headers=portal_headers)

    _, organization = request(
        "POST", "/portal/organizations", headers=portal_headers, body={"name": f"Gate Team {suffix}"}
    )
    workspace_id = organization["workspace_id"]
    request(
        "POST",
        f"/portal/workspaces/{workspace_id}/projects",
        headers=portal_headers,
        body={"name": "Gate Project", "slug": f"gate-{uuid.uuid4().hex[:12]}"},
    )
    request(
        "POST",
        f"/portal/workspaces/{workspace_id}/members",
        headers=portal_headers,
        body={"login_id": member_login, "role": "member"},
    )
    _, member_workspaces = request("GET", "/portal/workspaces", headers=member_headers)
    require(any(item["id"] == workspace_id for item in member_workspaces["data"]), "team member lacks workspace access")

    request(
        "POST",
        f"/admin/accounts/{account['id']}/balance",
        headers=admin_headers,
        body={"amount_micros": 1_000_000, "idempotency_key": f"gate-topup-{suffix}"},
    )
    _, duplicate_topup = request(
        "POST",
        f"/admin/accounts/{account['id']}/balance",
        headers=admin_headers,
        body={"amount_micros": 1_000_000, "idempotency_key": f"gate-topup-{suffix}"},
    )
    require(duplicate_topup["balance_micros"] == 1_000_000, "top-up idempotency failed")

    redemption_code = f"GATE-{uuid.uuid4().hex.upper()}"
    request(
        "POST",
        "/admin/redemption-codes",
        headers=admin_headers,
        body={"label": "Gate benefit", "amount_micros": 250_000, "code": redemption_code, "max_redemptions": 1},
    )
    _, redeemed = request(
        "POST", "/portal/redemption-codes/redeem", headers=portal_headers, body={"code": redemption_code}
    )
    require(redeemed["balance_micros"] == 1_250_000, "redemption balance is inconsistent")
    request(
        "POST", "/portal/redemption-codes/redeem", headers=portal_headers, body={"code": redemption_code}, expected=409
    )

    _, api_key = request(
        "POST",
        "/portal/api-keys",
        headers=portal_headers,
        body={"name": "Gate key", "spending_limit_micros": 500_000},
    )
    key_headers = {"Authorization": f"Bearer {api_key['key']}"}
    _, balance = request("GET", "/v1/account", headers=key_headers)
    require(balance["balance_micros"] == 1_250_000, "API key account balance mismatch")
    _, rotated = request("POST", f"/portal/api-keys/{api_key['id']}/rotate", headers=portal_headers)
    request("GET", "/v1/account", headers=key_headers, expected=401)
    rotated_headers = {"Authorization": f"Bearer {rotated['key']}"}
    request("GET", "/v1/account", headers=rotated_headers)

    _, order = request(
        "POST",
        "/portal/payment-orders",
        headers=portal_headers,
        body={"account_id": account["id"], "amount_micros": 500_000, "provider": "manual"},
    )
    _, confirmed = request(
        "POST",
        f"/admin/payment-orders/{order['id']}/confirm",
        headers=admin_headers,
        body={"review_note": "Gate confirmation"},
    )
    require(confirmed["status"] == "paid", "payment confirmation failed")
    _, confirmed_again = request(
        "POST", f"/admin/payment-orders/{order['id']}/confirm", headers=admin_headers, body={}
    )
    require(confirmed_again["status"] == "paid", "payment confirmation is not idempotent")
    _, refunded = request(
        "POST",
        f"/admin/payment-orders/{order['id']}/refund",
        headers=admin_headers,
        body={"review_note": "Gate refund"},
    )
    require(refunded["status"] == "refunded", "payment refund failed")
    _, refunded_again = request(
        "POST", f"/admin/payment-orders/{order['id']}/refund", headers=admin_headers, body={}
    )
    require(refunded_again["status"] == "refunded", "payment refund is not idempotent")

    _, reconciliation = request("GET", "/admin/reconciliation", headers=admin_headers)
    require(reconciliation["ok"] is True, f"ledger reconciliation failed: {reconciliation}")
    _, runtime = request("GET", "/admin/runtime", headers=admin_headers)
    require(runtime["environment"] == "production", "Gate application is not in production mode")
    require(runtime["mock_mode"] is False and runtime["data_mode"] == "real", "mock data mode is enabled")

    request(
        "PUT",
        "/portal/security/contact",
        headers=portal_headers,
        body={"contact": f"gate-{suffix}@example.invalid", "password": password},
    )
    _, notifications = request("GET", "/portal/security-notifications", headers=portal_headers)
    event_types = {item["event_type"] for item in notifications["data"]}
    require({"api_key_rotated", "security_contact_bound"} <= event_types, "security notifications are incomplete")
    request("POST", "/portal/security/logout-all", headers=portal_headers)
    request("GET", "/portal/profile", headers=portal_headers, expected=401)

    print("PASS: live PostgreSQL Gate business flow")
    print(f"PASS: {len(admin_gets)} admin read paths and {len(portal_gets)} portal read paths")
    print("PASS: RBAC, team workspace, ledger, key rotation, order state, redemption, analytics, sessions")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise
