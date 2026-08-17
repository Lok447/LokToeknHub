import os
import json
import random
import time
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_audit_event
from .config import get_settings
from .models import AccountBalanceTransaction, ApiKey, BillingAccount, ModelChannel, ModelConfig, UsageRecord, utcnow
from .schemas import ChatCompletionRequest


def is_expired(value) -> bool:
    if value is None:
        return False
    now = utcnow()
    if value.tzinfo is None:
        now = now.replace(tzinfo=None)
    return value <= now


def estimate_tokens(messages: list[Any]) -> int:
    text = " ".join(str(message.content) for message in messages)
    return max(1, len(text) // 4)


def calculate_amount(model: ModelConfig, input_tokens: int, output_tokens: int) -> int:
    return (
        input_tokens * model.input_price_micros_per_1k // 1000
        + output_tokens * model.output_price_micros_per_1k // 1000
    )


def reserved_amount(model: ModelConfig, input_tokens: int, output_tokens: int) -> int:
    return calculate_amount(model, input_tokens, output_tokens)


def reserve_balance(
    db: Session,
    account: BillingAccount,
    api_key: ApiKey,
    amount_micros: int,
    reference_id: str,
) -> None:
    if amount_micros <= 0:
        return
    locked_key = db.scalar(select(ApiKey).where(ApiKey.id == api_key.id).with_for_update())
    if not locked_key or not locked_key.active:
        raise ValueError("api key is inactive")
    if is_expired(locked_key.expires_at):
        raise ValueError("api key has expired")
    if locked_key.spending_limit_micros is not None and locked_key.spent_micros + amount_micros > locked_key.spending_limit_micros:
        raise ValueError("api key spending limit exceeded")
    locked_account = db.scalar(select(BillingAccount).where(BillingAccount.id == account.id).with_for_update())
    if not locked_account or not locked_account.active or locked_account.balance_micros < amount_micros:
        raise ValueError("insufficient balance")
    locked_account.balance_micros -= amount_micros
    locked_key.spent_micros += amount_micros
    db.add(AccountBalanceTransaction(
        account_id=locked_account.id,
        api_key_id=api_key.id,
        amount_micros=-amount_micros,
        transaction_type="reservation",
        reference_id=reference_id,
        description="model request reservation",
    ))
    db.commit()
    account.balance_micros = locked_account.balance_micros


def settle_balance(
    db: Session,
    account: BillingAccount,
    api_key: ApiKey,
    reserved_micros: int,
    actual_micros: int,
    reference_id: str,
) -> None:
    delta = reserved_micros - actual_micros
    if delta == 0:
        return
    locked_key = db.scalar(select(ApiKey).where(ApiKey.id == api_key.id).with_for_update())
    locked_account = db.scalar(select(BillingAccount).where(BillingAccount.id == account.id).with_for_update())
    if not locked_account or not locked_key:
        return
    locked_account.balance_micros += delta
    locked_key.spent_micros = max(0, locked_key.spent_micros - delta)
    db.add(AccountBalanceTransaction(
        account_id=locked_account.id,
        api_key_id=api_key.id,
        amount_micros=delta,
        transaction_type="settlement",
        reference_id=f"{reference_id}:settlement",
        description="model request settlement",
    ))
    db.commit()
    account.balance_micros = locked_account.balance_micros


def credit_balance(
    db: Session,
    account: BillingAccount,
    amount_micros: int,
    reference_id: str,
    description: str | None,
    api_key_id: int | None = None,
    audit_event: dict[str, object] | None = None,
) -> BillingAccount:
    existing = db.scalar(select(AccountBalanceTransaction).where(AccountBalanceTransaction.reference_id == reference_id))
    if existing:
        if existing.account_id != account.id:
            raise ValueError("idempotency key already belongs to another account")
        return account
    locked_account = db.scalar(select(BillingAccount).where(BillingAccount.id == account.id).with_for_update())
    if not locked_account:
        raise ValueError("account not found")
    locked_account.balance_micros += amount_micros
    db.add(AccountBalanceTransaction(
        account_id=locked_account.id,
        api_key_id=api_key_id,
        amount_micros=amount_micros,
        transaction_type="topup",
        reference_id=reference_id,
        description=description,
    ))
    if audit_event:
        record_audit_event(db, **audit_event)
    db.commit()
    return locked_account


class ProviderCallError(Exception):
    def __init__(self, detail: str, retryable: bool = True):
        super().__init__(detail)
        self.detail = detail
        self.retryable = retryable


def _is_circuit_open(channel: ModelChannel) -> bool:
    if channel.circuit_open_until is None:
        return False
    now = utcnow()
    if channel.circuit_open_until.tzinfo is None:
        now = now.replace(tzinfo=None)
    return channel.circuit_open_until > now


def select_channels(db: Session, model: ModelConfig) -> list[ModelChannel]:
    settings = get_settings()
    channels = db.scalars(
        select(ModelChannel).where(
            ModelChannel.model_config_id == model.id,
            ModelChannel.active.is_(True),
        )
    ).all()
    eligible = [channel for channel in channels if not _is_circuit_open(channel)]
    ordered: list[ModelChannel] = []
    for priority in sorted({channel.priority for channel in eligible}):
        group = [channel for channel in eligible if channel.priority == priority]
        group.sort(key=lambda item: random.random() ** (1 / max(item.weight, 1)), reverse=True)
        ordered.extend(group)
    return ordered[:settings.max_channel_attempts]


def mark_channel_success(db: Session, channel: ModelChannel) -> None:
    tracked = db.get(ModelChannel, channel.id)
    if not tracked:
        return
    tracked.status = "healthy"
    tracked.consecutive_failures = 0
    tracked.circuit_open_until = None
    tracked.last_checked_at = utcnow()
    tracked.last_error = None
    db.commit()


def mark_channel_failure(db: Session, channel: ModelChannel, detail: str) -> None:
    settings = get_settings()
    tracked = db.get(ModelChannel, channel.id)
    if not tracked:
        return
    tracked.status = "unhealthy"
    tracked.consecutive_failures += 1
    tracked.last_checked_at = utcnow()
    tracked.last_error = detail[:1000]
    if tracked.consecutive_failures >= settings.channel_failure_threshold:
        tracked.circuit_open_until = utcnow() + timedelta(seconds=settings.channel_circuit_cooldown_seconds)
    db.commit()


def _provider_auth(channel: ModelChannel) -> tuple[str, dict[str, str]]:
    settings = get_settings()
    api_key = os.getenv(channel.provider_api_key_env, "") if channel.provider_api_key_env else settings.default_provider_api_key
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return channel.provider_base_url.rstrip("/") + "/chat/completions", headers


async def discover_upstream_models(provider_base_url: str, provider_api_key_env: str | None) -> list[str]:
    """Read an OpenAI-compatible model catalogue without persisting provider secrets."""
    settings = get_settings()
    api_key = os.getenv(provider_api_key_env, "") if provider_api_key_env else settings.default_provider_api_key
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    endpoint = provider_base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=settings.channel_health_timeout_seconds) as client:
            response = await client.get(endpoint, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"upstream model discovery failed: {exc}") from exc
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("upstream model discovery returned an invalid response")
    model_ids = sorted({str(item.get("id", "")).strip() for item in items if isinstance(item, dict) and str(item.get("id", "")).strip()})
    return model_ids[:500]


def _retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or status_code >= 500


async def check_channel_health(db: Session, channel: ModelChannel) -> dict[str, Any]:
    settings = get_settings()
    started = time.perf_counter()
    if settings.mock_mode:
        mark_channel_success(db, channel)
        return {"healthy": True, "latency_ms": 0, "detail": "Mock mode"}
    endpoint, headers = _provider_auth(channel)
    endpoint = endpoint.removesuffix("/chat/completions") + "/models"
    try:
        async with httpx.AsyncClient(timeout=settings.channel_health_timeout_seconds) as client:
            response = await client.get(endpoint, headers=headers)
        if response.is_error:
            detail = f"HTTP {response.status_code}: {response.text[:500]}"
            mark_channel_failure(db, channel, detail)
            return {"healthy": False, "latency_ms": int((time.perf_counter() - started) * 1000), "detail": detail}
    except httpx.HTTPError as exc:
        detail = f"provider unavailable: {exc}"
        mark_channel_failure(db, channel, detail)
        return {"healthy": False, "latency_ms": int((time.perf_counter() - started) * 1000), "detail": detail}
    mark_channel_success(db, channel)
    return {"healthy": True, "latency_ms": int((time.perf_counter() - started) * 1000), "detail": "OK"}


async def call_provider(db: Session, model: ModelConfig, request: ChatCompletionRequest) -> tuple[dict[str, Any], int, int]:
    settings = get_settings()
    estimated_input = estimate_tokens(request.messages)
    if settings.mock_mode:
        channels = select_channels(db, model)
        if channels:
            mark_channel_success(db, channels[0])
        output = "TOKEN mock response"
        output_tokens = max(1, len(output) // 4)
        return {
            "id": "chatcmpl-" + uuid.uuid4().hex,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model.public_name,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": output}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": estimated_input, "completion_tokens": output_tokens, "total_tokens": estimated_input + output_tokens},
        }, estimated_input, output_tokens

    channels = select_channels(db, model)
    if not channels:
        raise HTTPException(status_code=502, detail="no available channel for model")
    last_detail = "all available channels failed"
    for channel in channels:
        endpoint, headers = _provider_auth(channel)
        payload = request.model_dump(exclude_none=True)
        payload["model"] = channel.upstream_model
        payload.setdefault("max_tokens", settings.reservation_output_tokens)
        try:
            async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
            if response.is_error:
                raise ProviderCallError(
                    f"{channel.name}: HTTP {response.status_code}: {response.text[:500]}",
                    retryable=_retryable_status(response.status_code),
                )
            data = response.json()
            usage = data.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens", estimated_input))
            output_tokens = int(usage.get("completion_tokens", 0))
            mark_channel_success(db, channel)
            return data, input_tokens, output_tokens
        except ProviderCallError as exc:
            last_detail = exc.detail
            mark_channel_failure(db, channel, exc.detail)
            if not exc.retryable:
                break
        except httpx.HTTPError as exc:
            last_detail = f"{channel.name}: provider unavailable: {exc}"
            mark_channel_failure(db, channel, last_detail)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            last_detail = f"{channel.name}: invalid provider response: {exc}"
            mark_channel_failure(db, channel, last_detail)
    raise HTTPException(status_code=502, detail=last_detail)


async def stream_provider(db: Session, model: ModelConfig, request: ChatCompletionRequest) -> AsyncIterator[bytes]:
    settings = get_settings()
    estimated_input = estimate_tokens(request.messages)
    if settings.mock_mode:
        channels = select_channels(db, model)
        if channels:
            mark_channel_success(db, channels[0])
        completion_id = "chatcmpl-" + uuid.uuid4().hex
        base = {"id": completion_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model.public_name}
        chunks = [
            {**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
            {**base, "choices": [{"index": 0, "delta": {"content": "TOKEN "}, "finish_reason": None}]},
            {**base, "choices": [{"index": 0, "delta": {"content": "mock response"}, "finish_reason": None}]},
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {**base, "choices": [], "usage": {"prompt_tokens": estimated_input, "completion_tokens": 4, "total_tokens": estimated_input + 4}},
        ]
        for chunk in chunks:
            yield f"data: {json.dumps(chunk, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"
        return

    channels = select_channels(db, model)
    if not channels:
        raise HTTPException(status_code=502, detail="no available channel for model")
    last_detail = "all available channels failed"
    for channel in channels:
        emitted = False
        endpoint, headers = _provider_auth(channel)
        headers["Accept"] = "text/event-stream"
        payload = request.model_dump(exclude_none=True)
        payload["model"] = channel.upstream_model
        payload["stream"] = True
        payload.setdefault("max_tokens", settings.reservation_output_tokens)
        stream_options = payload.get("stream_options") or {}
        stream_options["include_usage"] = True
        payload["stream_options"] = stream_options
        try:
            async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
                async with client.stream("POST", endpoint, json=payload, headers=headers) as response:
                    if response.is_error:
                        detail = (await response.aread()).decode("utf-8", errors="replace")[:500]
                        raise ProviderCallError(
                            f"{channel.name}: HTTP {response.status_code}: {detail}",
                            retryable=_retryable_status(response.status_code),
                        )
                    async for line in response.aiter_lines():
                        if line:
                            emitted = True
                            yield (line + "\n\n").encode("utf-8")
            mark_channel_success(db, channel)
            return
        except ProviderCallError as exc:
            last_detail = exc.detail
            mark_channel_failure(db, channel, exc.detail)
            if emitted or not exc.retryable:
                raise HTTPException(status_code=502, detail=exc.detail) from exc
        except httpx.HTTPError as exc:
            last_detail = f"{channel.name}: provider unavailable: {exc}"
            mark_channel_failure(db, channel, last_detail)
            if emitted:
                raise HTTPException(status_code=502, detail=last_detail) from exc
    raise HTTPException(status_code=502, detail=last_detail)


def save_usage(
    db: Session,
    api_key: ApiKey,
    model: ModelConfig,
    request_id: str,
    trace_id: str,
    input_tokens: int,
    output_tokens: int,
    status: str,
    latency_ms: int,
    error_message: str | None = None,
) -> UsageRecord:
    tracked_key = db.get(ApiKey, api_key.id)
    if tracked_key:
        tracked_key.last_used_at = utcnow()
    record = UsageRecord(
        request_id=request_id,
        trace_id=trace_id,
        account_id=api_key.account_id,
        api_key_id=api_key.id,
        model=model.public_name,
        upstream_model=model.upstream_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        amount_micros=calculate_amount(model, input_tokens, output_tokens),
        status=status,
        latency_ms=latency_ms,
        error_message=error_message,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record
