from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

import httpx


BASES = ["http://127.0.0.1:18000", "http://127.0.0.1:18001"]
ADMIN_TOKEN = "uat-admin-token-20260908-long"
WEBHOOK_SECRET = "uat-payment-webhook-secret-20260908"
REPORT = Path("runtime-logs/preprod-acceptance.json")


def main() -> int:
    run_id = f"{int(time.time())}-{os.getpid()}"
    result: dict[str, object] = {"run_id": run_id, "checks": [], "failures": [], "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    def check(name: str, passed: bool, detail: object) -> None:
        item = {"name": name, "passed": bool(passed), "detail": detail}
        result["checks"].append(item)
        if not passed:
            result["failures"].append(item)

    def finish() -> int:
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result["passed"] = not result["failures"]
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1

    def request_id(name: str) -> str:
        return f"uat-{name}-{run_id}"

    client = httpx.Client(timeout=20)
    try:
        for base in BASES:
            health = client.get(base + "/healthz")
            ready = client.get(base + "/readyz")
            check(f"healthz {base}", health.status_code == 200, health.text)
            check(f"readyz {base}", ready.status_code == 200, ready.text)

        admin = {"X-Admin-Token": ADMIN_TOKEN}
        bootstrap = client.post(BASES[0] + "/admin/auth/bootstrap", headers=admin, json={"login_id": "uat-admin", "password": "uat-admin-password-20260908"})
        if bootstrap.status_code == 409:
            login = client.post(BASES[0] + "/admin/auth/login", json={"login_id": "uat-admin", "password": "uat-admin-password-20260908"})
            bootstrap = login
        check("admin bootstrap/login", bootstrap.status_code == 200, bootstrap.text)
        if bootstrap.status_code != 200:
            return finish()
        admin_headers = {"Authorization": "Bearer " + bootstrap.json()["access_token"]}

        account = client.post(BASES[0] + "/admin/accounts", headers=admin_headers, json={"external_user_id": f"preprod-acceptance-{run_id}", "name": f"Preprod Acceptance {run_id}"})
        check("create account", account.status_code == 200, account.text)
        if account.status_code != 200:
            return finish()
        account_id = account.json()["id"]
        key = client.post(BASES[0] + "/admin/api-keys", headers=admin_headers, json={"account_id": account_id, "name": f"preprod-key-{run_id}", "rate_limit_requests": 1000, "rate_limit_window_seconds": 60})
        check("create API key", key.status_code == 200, key.text)
        if key.status_code != 200:
            return finish()
        api_key = key.json()["key"]
        topup = client.post(BASES[0] + f"/admin/accounts/{account_id}/balance", headers=admin_headers, json={"amount_micros": 10_000_000, "idempotency_key": f"preprod-acceptance-topup-{run_id}"})
        check("top up account", topup.status_code == 200, topup.text)
        if topup.status_code != 200:
            return finish()

        public_model = f"preprod-chat-{run_id}"
        model = client.post(BASES[0] + "/admin/models", headers=admin_headers, json={"public_name": public_model, "upstream_model": "preprod-model", "provider_base_url": "http://host.docker.internal:4010/v1", "provider_api_key": "preprod-provider-key", "input_price_micros_per_1k": 1000, "output_price_micros_per_1k": 2000})
        check("create real HTTP model", model.status_code == 200, model.text)
        if model.status_code != 200:
            return finish()
        model_id = model.json()["id"]
        channels = client.get(BASES[0] + f"/admin/models/{model_id}/channels", headers=admin_headers)
        channel_id = channels.json()["data"][0]["id"]
        health = client.post(BASES[0] + f"/admin/channels/{channel_id}/check", headers=admin_headers)
        check("real provider health check", health.status_code == 200 and health.json().get("healthy") is True, health.text)
        listed = client.get(BASES[0] + "/admin/models", headers=admin_headers)
        listed_model = next(item for item in listed.json()["data"] if item["id"] == model_id)
        check("model publication", listed_model.get("publication_state") == "published", listed_model)

        bearer = {"Authorization": f"Bearer {api_key}"}
        payload = {"model": public_model, "messages": [{"role": "user", "content": "hello preprod"}]}
        trace_id = request_id("trace")
        chat = client.post(BASES[0] + "/v1/chat/completions", headers={**bearer, "X-Request-ID": request_id("chat"), "X-Trace-ID": trace_id}, json=payload)
        check("OpenAI chat HTTP", chat.status_code == 200 and chat.json().get("usage", {}).get("total_tokens") == 5, {"status": chat.status_code, "headers": {"x-request-id": chat.headers.get("x-request-id"), "x-trace-id": chat.headers.get("x-trace-id")}, "body": chat.json()})
        stream = client.post(BASES[0] + "/v1/chat/completions", headers={**bearer, "X-Request-ID": request_id("stream")}, json={**payload, "stream": True})
        check("OpenAI chat SSE", stream.status_code == 200 and "data: [DONE]" in stream.text and "PREPROD OK" in stream.text, stream.text[:500])

        responses = client.post(BASES[0] + "/v1/responses", headers={**bearer, "X-Request-ID": request_id("responses")}, json={"model": public_model, "input": "hello"})
        anthropic = client.post(BASES[0] + "/v1/messages", headers={**bearer, "X-Request-ID": request_id("anthropic")}, json={"model": public_model, "max_tokens": 20, "messages": [{"role": "user", "content": "hello"}]})
        gemini = client.post(BASES[0] + f"/v1beta/models/{public_model}:generateContent", headers={**bearer, "X-Request-ID": request_id("gemini")}, json={"contents": [{"role": "user", "parts": [{"text": "hello"}]}]})
        check("Responses adapter", responses.status_code == 200 and responses.json().get("object") == "response", responses.text)
        check("Anthropic adapter", anthropic.status_code == 200 and anthropic.json().get("type") == "message", anthropic.text)
        check("Gemini adapter", gemini.status_code == 200 and "candidates" in gemini.json(), gemini.text)

        cache_first = client.post(BASES[0] + "/v1/chat/completions", headers={**bearer, "X-Request-ID": request_id("cache-1")}, json={**payload, "user": "cache"})
        cache_second = client.post(BASES[1] + "/v1/chat/completions", headers={**bearer, "X-Request-ID": request_id("cache-2")}, json={**payload, "user": "cache"})
        check("cross-instance Redis cache", cache_first.status_code == 200 and cache_second.status_code == 200 and cache_second.headers.get("x-cache") == "HIT", {"first": cache_first.headers.get("x-cache"), "second": cache_second.headers.get("x-cache")})

        trace = client.get(BASES[1] + f"/admin/traces/{trace_id}", headers=admin_headers)
        metrics = client.get(BASES[0] + "/metrics")
        check("trace explainability", trace.status_code == 200 and trace.json()["data"][0]["route_attempts"], trace.text)
        check("prometheus metrics", metrics.status_code == 200 and "loktoken_http_requests_total" in metrics.text, metrics.text[:500])

        limited = client.post(BASES[0] + "/admin/api-keys", headers=admin_headers, json={"account_id": account_id, "name": f"rate-limited-{run_id}", "rate_limit_requests": 1, "rate_limit_window_seconds": 60})
        limited_key = limited.json()["key"]
        one = client.post(BASES[0] + "/v1/chat/completions", headers={"Authorization": f"Bearer {limited_key}", "X-Request-ID": request_id("limit-1")}, json=payload)
        two = client.post(BASES[1] + "/v1/chat/completions", headers={"Authorization": f"Bearer {limited_key}", "X-Request-ID": request_id("limit-2")}, json=payload)
        check("cross-instance Redis rate limit", one.status_code == 200 and two.status_code == 429, {"first": one.status_code, "second": two.status_code, "body": two.text})

        budget = client.patch(BASES[0] + f"/admin/accounts/{account_id}/budget", headers=admin_headers, json={"budget_micros": 1})
        blocked = client.post(BASES[0] + "/v1/chat/completions", headers={**bearer, "X-Request-ID": request_id("budget")}, json=payload)
        check("account budget block", budget.status_code == 200 and blocked.status_code == 402, {"budget": budget.text, "blocked": blocked.text})

        order = client.post(BASES[0] + "/admin/payment-orders", headers=admin_headers, json={"account_id": account_id, "amount_micros": 12345, "provider": "manual"})
        if order.status_code == 200:
            order_no = order.json()["order_no"]
            event = {"event_id": request_id("payment"), "order_no": order_no, "provider_order_id": request_id("provider"), "provider": "manual", "amount_micros": 12345, "status": "paid"}
            body = json.dumps(event, separators=(",", ":")).encode()
            timestamp = str(int(time.time()))
            signature = hmac.new(WEBHOOK_SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
            webhook_headers = {"Content-Type": "application/json", "X-Token-Timestamp": timestamp, "X-Token-Signature": "sha256=" + signature}
            paid = client.post(BASES[0] + "/payments/webhook", headers=webhook_headers, content=body)
            replay = client.post(BASES[1] + "/payments/webhook", headers=webhook_headers, content=body)
            check("payment webhook idempotency", paid.status_code == 200 and replay.status_code == 200, {"paid": paid.status_code, "replay": replay.status_code})
        else:
            check("payment order create", False, order.text)

        return finish()
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
