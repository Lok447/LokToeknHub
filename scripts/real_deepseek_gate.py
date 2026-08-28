"""Run a small, auditable real DeepSeek V4 Flash billing loop on the local service.

The script creates a uniquely named local test account and leaves its request,
usage, and ledger rows for audit. It never prints or reads a provider secret.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AccountBalanceTransaction, ApiKey, BillingAccount, ModelChannel, ModelConfig, UsageRecord, utcnow
from app.security import create_key, hash_key, hash_password


BASE_URL = "http://127.0.0.1:8000"
MODEL_NAME = "deepseek-v4-flash"
PASSWORD = "Real-gate-password-2026"


class TimeoutResponseHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        _ = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(408, "Request Timeout")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":{"message":"synthetic upstream timeout"}}')

    def log_message(self, *_args: object) -> None:
        return


def call_api(raw_key: str, request_id: str, payload: dict[str, object]) -> tuple[int, dict[str, object] | str]:
    request = urllib.request.Request(
        f"{BASE_URL}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
            "X-Trace-ID": f"trace_{request_id}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=150) as response:
            body = response.read().decode("utf-8", errors="replace")
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                return response.status, body
            return response.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def main() -> None:
    suffix = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    external_user_id = f"real-gate-{suffix}"
    login_id = f"real-gate-{suffix}"
    normal_id = f"real_normal_{suffix}"
    stream_id = f"real_stream_{suffix}"
    insufficient_id = f"real_insufficient_{suffix}"
    timeout_id = f"real_timeout_{suffix}"
    duplicate_id = normal_id

    with SessionLocal() as db:
        model = db.scalar(select(ModelConfig).where(ModelConfig.public_name == MODEL_NAME))
        channel = db.scalar(select(ModelChannel).where(ModelChannel.model_config_id == model.id, ModelChannel.active.is_(True))) if model else None
        if not model or not channel:
            raise RuntimeError("deepseek-v4-flash does not have an active channel in the local service")
        if channel.health_source != "provider" or channel.status != "healthy":
            raise RuntimeError(f"DeepSeek channel is not provider-healthy: status={channel.status}, source={channel.health_source}")
        official = json.loads(model.official_pricing_json or "{}")
        account = BillingAccount(
            external_user_id=external_user_id,
            login_id=login_id,
            password_hash=hash_password(PASSWORD),
            name="Real DeepSeek Gate",
            balance_micros=5_000_000,
            active=True,
        )
        db.add(account)
        db.flush()
        raw_key = create_key()
        api_key = ApiKey(
            account_id=account.id,
            name="real-deepseek-gate",
            key_prefix=raw_key[:12],
            key_hash=hash_key(raw_key),
            active=True,
        )
        db.add(api_key)
        db.add(AccountBalanceTransaction(
            account_id=account.id,
            api_key_id=api_key.id,
            amount_micros=5_000_000,
            transaction_type="topup",
            reference_id=f"real-gate-topup:{suffix}",
            description="real model closed-loop verification",
        ))
        db.commit()
        account_id = account.id
        api_key_id = api_key.id
        input_price = model.input_price_micros_per_1k
        output_price = model.output_price_micros_per_1k
        provider_channel_id = channel.id
        provider_model_id = model.id
        encrypted_key = channel.encrypted_api_key
        upstream_model = channel.upstream_model

    prompt = [{"role": "user", "content": "Reply with exactly OK."}]
    normal_status, normal_body = call_api(raw_key, normal_id, {"model": MODEL_NAME, "messages": prompt, "max_tokens": 8})
    stream_status, stream_body = call_api(raw_key, stream_id, {"model": MODEL_NAME, "messages": prompt, "max_tokens": 8, "stream": True})

    with SessionLocal() as db:
        account = db.get(BillingAccount, account_id)
        normal_record = db.scalar(select(UsageRecord).where(UsageRecord.request_id == normal_id))
        stream_record = db.scalar(select(UsageRecord).where(UsageRecord.request_id == stream_id))
        if not account or not normal_record or not stream_record:
            raise RuntimeError("successful real requests did not produce usage records")
        balance_after_success = account.balance_micros
        normal_payload = normal_body if isinstance(normal_body, dict) else {"raw": normal_body}
        stream_text = stream_body if isinstance(stream_body, str) else json.dumps(stream_body, ensure_ascii=False)
        stream_has_done = "data: [DONE]" in stream_text
        stream_has_usage = "prompt_tokens" in stream_text and "completion_tokens" in stream_text
        duplicate_status, duplicate_body = call_api(raw_key, duplicate_id, {"model": MODEL_NAME, "messages": prompt, "max_tokens": 8})
        duplicate_count = db.scalar(select(UsageRecord.id).where(UsageRecord.request_id == duplicate_id))

        account.balance_micros = 0
        db.commit()
    insufficient_status, insufficient_body = call_api(raw_key, insufficient_id, {"model": MODEL_NAME, "messages": prompt, "max_tokens": 8})

    # Use a local upstream that returns HTTP 408. This exercises provider timeout
    # handling and reservation refund without spending another provider request.
    timeout_server = ThreadingHTTPServer(("127.0.0.1", 19091), TimeoutResponseHandler)
    thread = threading.Thread(target=timeout_server.serve_forever, daemon=True)
    thread.start()
    with SessionLocal() as db:
        account = db.get(BillingAccount, account_id)
        account.balance_micros = 1_000_000
        original_model = db.get(ModelConfig, provider_model_id)
        timeout_model = ModelConfig(
            public_name=f"real-gate-timeout-{suffix}",
            upstream_model=upstream_model,
            provider_base_url="http://127.0.0.1:19091/v1",
            provider_api_key_env=None,
            input_price_micros_per_1k=input_price,
            output_price_micros_per_1k=output_price,
            catalog_metadata_json=original_model.catalog_metadata_json,
            official_pricing_json=original_model.official_pricing_json,
            active=True,
        )
        stream_timeout_model = ModelConfig(
            public_name=f"real-gate-stream-timeout-{suffix}",
            upstream_model=upstream_model,
            provider_base_url="http://127.0.0.1:19091/v1",
            provider_api_key_env=None,
            input_price_micros_per_1k=input_price,
            output_price_micros_per_1k=output_price,
            catalog_metadata_json=original_model.catalog_metadata_json,
            official_pricing_json=original_model.official_pricing_json,
            active=True,
        )
        db.add(timeout_model)
        db.add(stream_timeout_model)
        db.flush()
        timeout_channel = ModelChannel(
            model_config_id=timeout_model.id,
            name="Synthetic upstream timeout",
            provider_base_url="http://127.0.0.1:19091/v1",
            upstream_model=upstream_model,
            provider_api_key_env=None,
            encrypted_api_key=encrypted_key,
            active=True,
            status="healthy",
            health_source="provider",
        )
        stream_timeout_channel = ModelChannel(
            model_config_id=stream_timeout_model.id,
            name="Synthetic streaming timeout",
            provider_base_url="http://127.0.0.1:19091/v1",
            upstream_model=upstream_model,
            provider_api_key_env=None,
            encrypted_api_key=encrypted_key,
            active=True,
            status="healthy",
            health_source="provider",
        )
        db.add(timeout_channel)
        db.add(stream_timeout_channel)
        db.commit()
        timeout_model_name = timeout_model.public_name
        timeout_channel_id = timeout_channel.id
        stream_timeout_model_name = stream_timeout_model.public_name
        stream_timeout_channel_id = stream_timeout_channel.id
    stream_timeout_id = f"real_stream_timeout_{suffix}"
    stream_timeout_status, stream_timeout_body = call_api(raw_key, stream_timeout_id, {"model": stream_timeout_model_name, "messages": prompt, "max_tokens": 8, "stream": True})
    timeout_status, timeout_body = call_api(raw_key, timeout_id, {"model": timeout_model_name, "messages": prompt, "max_tokens": 8})
    timeout_server.shutdown()

    with SessionLocal() as db:
        account = db.get(BillingAccount, account_id)
        timeout_record = db.scalar(select(UsageRecord).where(UsageRecord.request_id == timeout_id))
        stream_timeout_record = db.scalar(select(UsageRecord).where(UsageRecord.request_id == stream_timeout_id))
        transactions = db.scalars(select(AccountBalanceTransaction).where(AccountBalanceTransaction.account_id == account_id).order_by(AccountBalanceTransaction.id)).all()
        # Remove only synthetic timeout model/channel. Keep account, key, real
        # usage, and ledger rows as auditable evidence for the operator.
        db.delete(db.get(ModelChannel, timeout_channel_id))
        db.delete(db.get(ModelConfig, timeout_model.id))
        db.delete(db.get(ModelChannel, stream_timeout_channel_id))
        db.delete(db.get(ModelConfig, stream_timeout_model.id))
        db.commit()
        result = {
            "account_id": account_id,
            "api_key_prefix": raw_key[:12],
            "model": MODEL_NAME,
            "channel_id": provider_channel_id,
            "normal": {
                "http_status": normal_status,
                "provider_response_id": normal_payload.get("id"),
                "provider_usage": normal_payload.get("usage"),
                "usage_record": {"request_id": normal_record.request_id, "status": normal_record.status, "input_tokens": normal_record.input_tokens, "output_tokens": normal_record.output_tokens, "total_tokens": normal_record.total_tokens, "amount_micros": normal_record.amount_micros},
            },
            "stream": {
                "http_status": stream_status,
                "has_done": stream_has_done,
                "has_usage": stream_has_usage,
                "usage_record": {"request_id": stream_record.request_id, "status": stream_record.status, "input_tokens": stream_record.input_tokens, "output_tokens": stream_record.output_tokens, "total_tokens": stream_record.total_tokens, "amount_micros": stream_record.amount_micros},
            },
            "balance_after_success_micros": balance_after_success,
            "duplicate_request": {"http_status": duplicate_status, "body": duplicate_body, "usage_row_exists": duplicate_count is not None},
            "insufficient_balance": {"http_status": insufficient_status, "body": insufficient_body},
            "upstream_timeout": {"http_status": timeout_status, "body": timeout_body, "usage_record": {"status": timeout_record.status if timeout_record else None, "amount_micros": timeout_record.amount_micros if timeout_record else None}},
            "stream_timeout_refund": {"http_status": stream_timeout_status, "has_done": "data: [DONE]" in (stream_timeout_body if isinstance(stream_timeout_body, str) else ""), "usage_record": {"status": stream_timeout_record.status if stream_timeout_record else None, "amount_micros": stream_timeout_record.amount_micros if stream_timeout_record else None}},
            "balance_after_failures_micros": account.balance_micros,
            "ledger_transaction_types": [item.transaction_type for item in transactions],
            "pricing": {
                "platform_input_micros_per_1k": input_price,
                "platform_output_micros_per_1k": output_price,
                "official_pricing": official,
            },
        }
        # Preserve the audit trail but make all generated credentials unusable.
        # Failure scenarios temporarily overwrite the balance to exercise 402
        # and refund paths. Restore the ledger-derived post-success balance so
        # the retained audit account does not create a reconciliation mismatch.
        account.balance_micros = balance_after_success
        account.active = False
        tracked_key = db.get(ApiKey, api_key_id)
        if tracked_key:
            tracked_key.active = False
        db.commit()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
