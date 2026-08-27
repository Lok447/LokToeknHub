import os
import os
import json
import random
import time
import uuid
from dataclasses import dataclass, field
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_audit_event
from .config import get_settings
from .models import AccountBalanceTransaction, ApiKey, BillingAccount, GenerationTask, ModelChannel, ModelConfig, Project, ProviderConnection, UsageRecord, utcnow
from .provider_presets import get_provider_preset, provider_catalogue_matches
from .provider_secrets import ProviderSecretError, decrypt_provider_secret
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


def _catalog_metadata(model: ModelConfig | None) -> dict[str, Any]:
    if not model or not model.catalog_metadata_json:
        return {}
    try:
        value = json.loads(model.catalog_metadata_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def normalize_request_payload(request: ChatCompletionRequest, model: ModelConfig | None = None) -> dict[str, Any]:
    """Build an upstream payload from the model's provider capability contract."""
    payload = request.model_dump(exclude_none=True)
    metadata = _catalog_metadata(model)
    profile = metadata.get("gateway_profile") if isinstance(metadata.get("gateway_profile"), dict) else {}
    aliases = profile.get("parameter_aliases") if isinstance(profile.get("parameter_aliases"), dict) else {}
    aliases = {str(source): str(target) for source, target in aliases.items() if str(source) and str(target)}
    aliases.setdefault("max_completion_tokens", "max_tokens")
    for source, target in aliases.items():
        if source in payload:
            value = payload.pop(source)
            if target not in payload:
                payload[target] = value
    return payload


def validate_model_request(model: ModelConfig, request: ChatCompletionRequest) -> None:
    """Reject requests that cannot be handled by the model's configured gateway type."""
    metadata = _catalog_metadata(model)
    api_type = metadata.get("api_type", "chat_completions")
    profile = metadata.get("gateway_profile") if isinstance(metadata.get("gateway_profile"), dict) else {}
    if api_type != "chat_completions" or profile.get("protocol") not in {None, "openai_chat_completions"}:
        raise HTTPException(status_code=422, detail=f"模型 {model.public_name} 不是当前文本聊天协议可调用模型")
    max_output = metadata.get("max_output_tokens")
    requested = request.max_tokens or request.max_completion_tokens
    if isinstance(max_output, int) and max_output > 0 and requested and requested > max_output:
        raise HTTPException(status_code=422, detail=f"请求输出上限超过模型限制 {max_output} tokens")


def extract_usage(data: dict[str, Any], estimated_input: int = 0) -> tuple[int, int, dict[str, int]]:
    usage = data.get("usage") or {}
    input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", estimated_input)) or 0)
    output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
    details = {
        "input_cache_hit_tokens": int(usage.get("prompt_cache_hit_tokens", usage.get("cache_read_input_tokens", 0)) or 0),
        "input_cache_miss_tokens": int(usage.get("prompt_cache_miss_tokens", usage.get("cache_creation_input_tokens", 0)) or 0),
        "reasoning_tokens": int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens", usage.get("reasoning_tokens", 0)) or 0),
    }
    return input_tokens, output_tokens, details


def provider_cost(channel: ModelChannel, input_tokens: int, output_tokens: int, usage_details: dict[str, int] | None = None) -> int:
    """Calculate auditable provider cost when operators configured channel prices."""
    usage_details = usage_details or {}
    input_price = channel.provider_input_cost_micros_per_1k
    output_price = channel.provider_output_cost_micros_per_1k
    if input_price is None or output_price is None:
        return 0
    hit = usage_details.get("input_cache_hit_tokens", 0)
    miss = usage_details.get("input_cache_miss_tokens", 0)
    priced_input = hit + miss if hit or miss else input_tokens
    return priced_input * input_price // 1000 + output_tokens * output_price // 1000


@dataclass
class ProviderCallDetails:
    response: dict[str, Any]
    input_tokens: int
    output_tokens: int
    channel_id: int | None = None
    provider_request_id: str | None = None
    provider_cost_micros: int = 0
    usage_details: dict[str, int] = field(default_factory=dict)
    raw_usage: dict[str, Any] = field(default_factory=dict)
    route_attempts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ProviderTaskDetails:
    status: str
    result: dict[str, Any]
    channel_id: int | None = None
    provider_task_id: str | None = None
    provider_cost_micros: int = 0
    route_attempts: list[dict[str, Any]] = field(default_factory=list)


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
    if not locked_key or not locked_key.active or locked_key.revoked_at:
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
    project = db.get(Project, api_key.project_id) if api_key.project_id else None
    db.add(AccountBalanceTransaction(
        account_id=locked_account.id,
        workspace_id=project.workspace_id if project else None,
        project_id=project.id if project else None,
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
    project = db.get(Project, api_key.project_id) if api_key.project_id else None
    db.add(AccountBalanceTransaction(
        account_id=locked_account.id,
        workspace_id=project.workspace_id if project else None,
        project_id=project.id if project else None,
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
    project_id: int | None = None,
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
    project = db.get(Project, project_id) if project_id else None
    db.add(AccountBalanceTransaction(
        account_id=locked_account.id,
        workspace_id=project.workspace_id if project else None,
        project_id=project.id if project else None,
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


def mark_channel_success(db: Session, channel: ModelChannel, source: str = "provider", *, latency_ms: int | None = None, status_code: int | None = None) -> None:
    tracked = db.get(ModelChannel, channel.id)
    if not tracked:
        return
    tracked.status = "healthy"
    tracked.health_source = source
    tracked.consecutive_failures = 0
    tracked.circuit_open_until = None
    tracked.last_checked_at = utcnow()
    tracked.last_error = None
    if latency_ms is not None:
        tracked.last_latency_ms = max(0, int(latency_ms))
    tracked.last_status_code = status_code
    db.commit()


def mark_channel_failure(db: Session, channel: ModelChannel, detail: str, *, latency_ms: int | None = None, status_code: int | None = None) -> None:
    settings = get_settings()
    tracked = db.get(ModelChannel, channel.id)
    if not tracked:
        return
    tracked.status = "unhealthy"
    tracked.consecutive_failures += 1
    tracked.last_checked_at = utcnow()
    tracked.last_error = detail[:1000]
    if latency_ms is not None:
        tracked.last_latency_ms = max(0, int(latency_ms))
    tracked.last_status_code = status_code
    if tracked.consecutive_failures >= settings.channel_failure_threshold:
        tracked.circuit_open_until = utcnow() + timedelta(seconds=settings.channel_circuit_cooldown_seconds)
    db.commit()


def mark_channel_catalogue_state(db: Session, channel: ModelChannel, status: str, detail: str, source: str = "provider", *, latency_ms: int | None = None, status_code: int | None = None) -> None:
    """Record catalogue/configuration problems without opening the runtime circuit."""
    tracked = db.get(ModelChannel, channel.id)
    if not tracked:
        return
    tracked.status = status
    tracked.health_source = source
    tracked.consecutive_failures = 0
    tracked.circuit_open_until = None
    tracked.last_checked_at = utcnow()
    tracked.last_error = detail[:1000]
    if latency_ms is not None:
        tracked.last_latency_ms = max(0, int(latency_ms))
    tracked.last_status_code = status_code
    db.commit()


def _provider_auth(channel: ModelChannel) -> tuple[str, dict[str, str]]:
    settings = get_settings()
    api_key = None
    if channel.credential_source == "console" and channel.encrypted_api_key:
        try:
            api_key = decrypt_provider_secret(channel.encrypted_api_key)
        except ProviderSecretError:
            api_key = None
    elif channel.credential_source == "environment":
        api_key = os.getenv(channel.provider_api_key_env, "") if channel.provider_api_key_env else settings.default_provider_api_key
    else:
        api_key = settings.default_provider_api_key
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return channel.provider_base_url.rstrip("/") + "/chat/completions", headers


def _task_endpoint(channel: ModelChannel, model: ModelConfig, suffix: str = "") -> tuple[str, dict[str, str]]:
    _, headers = _provider_auth(channel)
    metadata = _catalog_metadata(model)
    profile = metadata.get("gateway_profile") if isinstance(metadata.get("gateway_profile"), dict) else {}
    default_paths = {
        "images_generations": "/images/generations",
        "video_generations": "/videos/generations",
        "audio_speech": "/audio/speech",
        "audio_transcriptions": "/audio/transcriptions",
    }
    request_path = str(profile.get("request_path") or default_paths.get(metadata.get("api_type"), "/audio/speech"))
    return channel.provider_base_url.rstrip("/") + "/" + request_path.lstrip("/") + suffix, headers


def _task_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"completed", "complete", "succeeded", "success", "done", "finished"}:
        return "completed"
    if normalized in {"failed", "error", "cancelled", "canceled", "expired"}:
        return "failed"
    return "processing"


def _task_result(data: dict[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
    body = data.get("data") if isinstance(data.get("data"), dict) else data
    task_id = next((str(value) for value in (
        data.get("task_id"), data.get("id"), body.get("task_id") if isinstance(body, dict) else None, body.get("id") if isinstance(body, dict) else None,
    ) if value), None)
    state = data.get("status") or data.get("state") or (body.get("status") if isinstance(body, dict) else None)
    result_data = data.get("data") if isinstance(data.get("data"), list) else (body.get("output") if isinstance(body, dict) else None)
    if isinstance(result_data, list) and result_data:
        return "completed", task_id, {"data": result_data}
    audio_url = data.get("audio_url") or data.get("url") or (body.get("audio_url") if isinstance(body, dict) else None) or (body.get("url") if isinstance(body, dict) else None)
    transcript = data.get("text") or (body.get("text") if isinstance(body, dict) else None)
    if audio_url:
        return "completed", task_id, {"data": [{"url": audio_url}]}
    if transcript is not None:
        return "completed", task_id, {"text": str(transcript), "data": [{"text": str(transcript)}]}
    url = data.get("url") or data.get("video_url") or (body.get("url") if isinstance(body, dict) else None)
    if url:
        return "completed", task_id, {"data": [{"url": url}]}
    return _task_status(state), task_id, data


async def create_provider_task(db: Session, model: ModelConfig, payload: dict[str, Any]) -> ProviderTaskDetails:
    settings = get_settings()
    metadata = _catalog_metadata(model)
    api_type = metadata.get("api_type")
    channels = select_channels(db, model)
    if not channels:
        raise HTTPException(status_code=502, detail="no available channel for model")
    if settings.mock_mode:
        channel = channels[0]
        mark_channel_success(db, channel, "mock")
        if api_type == "audio_transcriptions":
            result = {"text": "TOKEN mock transcription", "data": [{"text": "TOKEN mock transcription"}]}
        else:
            result = {"data": [{"url": f"https://mock.loktoken.local/{api_type}/{uuid.uuid4().hex}"}]}
        return ProviderTaskDetails("completed" if api_type in {"images_generations", "audio_speech", "audio_transcriptions"} else "processing", result, channel_id=channel.id, provider_task_id="task_" + uuid.uuid4().hex)
    attempts: list[dict[str, Any]] = []
    last_detail = "all available channels failed"
    for channel in channels:
        endpoint, headers = _task_endpoint(channel, model)
        request_payload = dict(payload)
        request_payload["model"] = channel.upstream_model
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
                response = await client.post(endpoint, json=request_payload, headers=headers)
            if response.is_error:
                raise ProviderCallError(f"{channel.name}: HTTP {response.status_code}: {response.text[:500]}", _retryable_status(response.status_code))
            status, provider_task_id, result = _task_result(response.json())
            attempts.append({"channel_id": channel.id, "channel": channel.name, "status": response.status_code, "latency_ms": int((time.perf_counter() - started) * 1000)})
            mark_channel_success(db, channel)
            return ProviderTaskDetails(status, result, channel.id, provider_task_id, int(channel.provider_task_cost_micros or 0), attempts)
        except ProviderCallError as exc:
            last_detail = exc.detail
            mark_channel_failure(db, channel, exc.detail)
            if not exc.retryable:
                break
        except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError) as exc:
            last_detail = f"{channel.name}: task provider response failed: {exc}"
            attempts.append({"channel_id": channel.id, "channel": channel.name, "status": None, "error": str(exc)[:300], "latency_ms": int((time.perf_counter() - started) * 1000)})
            mark_channel_failure(db, channel, last_detail)
    raise HTTPException(status_code=502, detail=last_detail)


async def refresh_provider_task(db: Session, task: GenerationTask, model: ModelConfig) -> ProviderTaskDetails:
    if task.status in {"completed", "failed"}:
        result = json.loads(task.result_json or "{}")
        return ProviderTaskDetails(task.status, result, task.provider_channel_id, task.provider_task_id)
    channel = db.get(ModelChannel, task.provider_channel_id) if task.provider_channel_id else None
    if not channel or not task.provider_task_id:
        raise HTTPException(status_code=502, detail="task has no provider polling route")
    settings = get_settings()
    if settings.mock_mode:
        mark_channel_success(db, channel, "mock")
        return ProviderTaskDetails("completed", {"data": [{"url": f"https://mock.loktoken.local/{task.task_type}/{task.task_id}"}]}, channel.id, task.provider_task_id)
    endpoint, headers = _task_endpoint(channel, model, "/" + task.provider_task_id)
    try:
        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            response = await client.get(endpoint, headers=headers)
        if response.is_error:
            raise ProviderCallError(f"{channel.name}: HTTP {response.status_code}: {response.text[:500]}", _retryable_status(response.status_code))
        status, provider_task_id, result = _task_result(response.json())
        mark_channel_success(db, channel)
        return ProviderTaskDetails(status, result, channel.id, provider_task_id or task.provider_task_id, int(channel.provider_task_cost_micros or 0))
    except ProviderCallError as exc:
        mark_channel_failure(db, channel, exc.detail)
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError) as exc:
        detail = f"{channel.name}: task polling failed: {exc}"
        mark_channel_failure(db, channel, detail)
        raise HTTPException(status_code=502, detail=detail) from exc


async def discover_upstream_models(
    provider_base_url: str,
    provider_api_key_env: str | None,
    provider_api_key: str | None = None,
) -> list[str]:
    """Read an OpenAI-compatible model catalogue without persisting provider secrets."""
    settings = get_settings()
    api_key = provider_api_key or (os.getenv(provider_api_key_env, "") if provider_api_key_env else settings.default_provider_api_key)
    if provider_api_key_env and not api_key.strip():
        raise ValueError(f"供应商密钥环境变量未配置: {provider_api_key_env}")
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


async def fetch_provider_balance(
    preset_id: str,
    provider_base_url: str,
    provider_api_key_env: str | None,
    provider_api_key: str | None = None,
) -> dict[str, Any]:
    """Fetch a provider account balance when the provider exposes a documented endpoint.

    Most providers expose billing only in their console, so unsupported providers
    deliberately return a structured result instead of guessing from model health.
    """
    settings = get_settings()
    if preset_id != "deepseek":
        return {"status": "unsupported", "source": "console", "detail": "该供应商未提供可验证的余额 API，请在供应商控制台查看或手工录入。"}
    api_key = provider_api_key or (os.getenv(provider_api_key_env, "") if provider_api_key_env else settings.default_provider_api_key)
    if not api_key.strip():
        raise ValueError("供应商密钥未配置，无法查询余额")
    base = provider_base_url.rstrip("/")
    endpoint = base.removesuffix("/v1") + "/user/balance"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=settings.channel_health_timeout_seconds) as client:
            response = await client.get(endpoint, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"供应商余额查询失败: {exc}") from exc
    infos = payload.get("balance_infos") if isinstance(payload, dict) else None
    if not isinstance(infos, list) or not infos:
        raise ValueError("供应商余额响应缺少 balance_infos")
    item = next((entry for entry in infos if isinstance(entry, dict) and str(entry.get("currency", "")).upper() == "CNY"), infos[0])
    try:
        amount_micros = round(float(item.get("total_balance", 0)) * 1_000_000)
    except (TypeError, ValueError) as exc:
        raise ValueError("供应商余额不是有效数字") from exc
    return {"status": "available", "source": "api", "amount_micros": amount_micros, "currency": str(item.get("currency") or "CNY").upper(), "raw": payload}


def _retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or status_code >= 500


async def check_channel_health(db: Session, channel: ModelChannel) -> dict[str, Any]:
    settings = get_settings()
    started = time.perf_counter()
    model = db.get(ModelConfig, channel.model_config_id)
    try:
        metadata = json.loads(model.catalog_metadata_json or "{}") if model else {}
    except json.JSONDecodeError:
        metadata = {}
    api_type = metadata.get("api_type", "chat_completions") if isinstance(metadata, dict) else "chat_completions"
    if api_type in {"images_generations", "video_generations", "audio_speech", "audio_transcriptions"}:
        detail = "任务适配器已启用；健康检查不创建可能产生费用的生成任务"
        mark_channel_success(db, channel, "adapter")
        return {"healthy": True, "status": "healthy", "latency_ms": 0, "detail": detail}
    if api_type != "chat_completions":
        detail = f"等待 {api_type} 统一调用适配器"
        mark_channel_catalogue_state(db, channel, "pending_adapter", detail, "catalogue")
        return {"healthy": False, "status": "pending_adapter", "latency_ms": 0, "detail": detail}
    if settings.mock_mode:
        mark_channel_success(db, channel, "mock")
        return {"healthy": True, "latency_ms": 0, "detail": "Mock mode"}
    endpoint, headers = _provider_auth(channel)
    if channel.provider_api_key_env and "Authorization" not in headers:
        detail = f"供应商密钥环境变量未配置: {channel.provider_api_key_env}"
        mark_channel_failure(db, channel, detail)
        return {"healthy": False, "latency_ms": 0, "detail": detail}
    endpoint = endpoint.removesuffix("/chat/completions") + "/models"
    try:
        async with httpx.AsyncClient(timeout=settings.channel_health_timeout_seconds) as client:
            response = await client.get(endpoint, headers=headers)
        if response.is_error:
            detail = f"HTTP {response.status_code}: {response.text[:500]}"
            mark_channel_failure(db, channel, detail, latency_ms=int((time.perf_counter() - started) * 1000), status_code=response.status_code)
            return {"healthy": False, "latency_ms": int((time.perf_counter() - started) * 1000), "detail": detail}
        try:
            payload = response.json()
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        items = payload.get("data") if isinstance(payload, dict) else None
        provider_model_ids = {
            str(item.get("id", "")).strip()
            for item in items or []
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }
        if not provider_model_ids:
            detail = "供应商模型目录响应无有效模型"
            mark_channel_failure(db, channel, detail, latency_ms=int((time.perf_counter() - started) * 1000), status_code=response.status_code)
            return {"healthy": False, "latency_ms": int((time.perf_counter() - started) * 1000), "detail": detail}
        connection = db.get(ProviderConnection, channel.provider_connection_id) if channel.provider_connection_id else None
        preset = get_provider_preset(connection.preset_id) if connection else None
        if preset and not provider_catalogue_matches(preset.id, provider_model_ids):
            available = ", ".join(sorted(provider_model_ids)[:6])
            detail = f"服务商目录与 {preset.name} 不匹配，请检查 API 地址和 API Key；返回样例: {available}"
            mark_channel_catalogue_state(db, channel, "misconfigured", detail, latency_ms=int((time.perf_counter() - started) * 1000), status_code=getattr(response, "status_code", 200))
            return {"healthy": False, "status": "misconfigured", "latency_ms": int((time.perf_counter() - started) * 1000), "detail": detail}
        if channel.upstream_model not in provider_model_ids:
            detail = f"当前服务商账号尚未开放上游模型: {channel.upstream_model}"
            mark_channel_catalogue_state(db, channel, "unavailable", detail, latency_ms=int((time.perf_counter() - started) * 1000), status_code=getattr(response, "status_code", 200))
            return {"healthy": False, "status": "unavailable", "latency_ms": int((time.perf_counter() - started) * 1000), "detail": detail}
    except httpx.HTTPError as exc:
        detail = f"provider unavailable: {exc}"
        mark_channel_failure(db, channel, detail)
        return {"healthy": False, "latency_ms": int((time.perf_counter() - started) * 1000), "detail": detail}
    mark_channel_success(db, channel, latency_ms=int((time.perf_counter() - started) * 1000), status_code=200)
    return {"healthy": True, "status": "healthy", "latency_ms": int((time.perf_counter() - started) * 1000), "detail": "OK"}


async def call_provider_details(db: Session, model: ModelConfig, request: ChatCompletionRequest) -> ProviderCallDetails:
    settings = get_settings()
    validate_model_request(model, request)
    estimated_input = estimate_tokens(request.messages)
    if settings.mock_mode:
        channels = select_channels(db, model)
        channel = channels[0] if channels else None
        if channel:
            mark_channel_success(db, channel)
        output = "TOKEN mock response"
        output_tokens = max(1, len(output) // 4)
        response = {
            "id": "chatcmpl-" + uuid.uuid4().hex,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model.public_name,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": output}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": estimated_input, "completion_tokens": output_tokens, "total_tokens": estimated_input + output_tokens},
        }
        return ProviderCallDetails(response, estimated_input, output_tokens, channel_id=channel.id if channel else None, provider_request_id=response["id"], raw_usage=response["usage"])

    channels = select_channels(db, model)
    if not channels:
        raise HTTPException(status_code=502, detail="no available channel for model")
    last_detail = "all available channels failed"
    route_attempts: list[dict[str, Any]] = []
    for channel in channels:
        endpoint, headers = _provider_auth(channel)
        payload = normalize_request_payload(request, model)
        payload["model"] = channel.upstream_model
        payload.setdefault("max_tokens", settings.reservation_output_tokens)
        attempt_started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
            if response.is_error:
                route_attempts.append({"channel_id": channel.id, "channel": channel.name, "status": response.status_code, "latency_ms": int((time.perf_counter() - attempt_started) * 1000)})
                raise ProviderCallError(
                    f"{channel.name}: HTTP {response.status_code}: {response.text[:500]}",
                    retryable=_retryable_status(response.status_code),
                )
            data = response.json()
            input_tokens, output_tokens, usage_details = extract_usage(data, estimated_input)
            usage = data.get("usage") or {}
            route_attempts.append({"channel_id": channel.id, "channel": channel.name, "status": response.status_code, "latency_ms": int((time.perf_counter() - attempt_started) * 1000)})
            mark_channel_success(db, channel)
            return ProviderCallDetails(
                data,
                input_tokens,
                output_tokens,
                channel_id=channel.id,
                provider_request_id=str(data.get("id")) if data.get("id") else None,
                provider_cost_micros=provider_cost(channel, input_tokens, output_tokens, usage_details),
                usage_details=usage_details,
                raw_usage=usage,
                route_attempts=route_attempts,
            )
        except ProviderCallError as exc:
            last_detail = exc.detail
            mark_channel_failure(db, channel, exc.detail)
            if not exc.retryable:
                break
        except httpx.HTTPError as exc:
            last_detail = f"{channel.name}: provider unavailable: {exc}"
            route_attempts.append({"channel_id": channel.id, "channel": channel.name, "status": None, "error": str(exc)[:300], "latency_ms": int((time.perf_counter() - attempt_started) * 1000)})
            mark_channel_failure(db, channel, last_detail)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            last_detail = f"{channel.name}: invalid provider response: {exc}"
            route_attempts.append({"channel_id": channel.id, "channel": channel.name, "status": None, "error": str(exc)[:300], "latency_ms": int((time.perf_counter() - attempt_started) * 1000)})
            mark_channel_failure(db, channel, last_detail)
    raise HTTPException(status_code=502, detail=last_detail)


async def call_provider(db: Session, model: ModelConfig, request: ChatCompletionRequest) -> tuple[dict[str, Any], int, int]:
    """Backward-compatible provider call tuple used by preflight and older integrations."""
    result = await call_provider_details(db, model, request)
    return result.response, result.input_tokens, result.output_tokens


async def stream_provider(
    db: Session,
    model: ModelConfig,
    request: ChatCompletionRequest,
    route_meta: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    settings = get_settings()
    validate_model_request(model, request)
    estimated_input = estimate_tokens(request.messages)
    if settings.mock_mode:
        channels = select_channels(db, model)
        if channels:
            mark_channel_success(db, channels[0])
            if route_meta is not None:
                route_meta.update({"provider_channel_id": channels[0].id, "route_attempts": [{"channel_id": channels[0].id, "channel": channels[0].name, "status": 200, "latency_ms": 0}]})
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
        payload = normalize_request_payload(request, model)
        payload["model"] = channel.upstream_model
        payload["stream"] = True
        payload.setdefault("max_tokens", settings.reservation_output_tokens)
        stream_options = payload.get("stream_options") or {}
        stream_options["include_usage"] = True
        payload["stream_options"] = stream_options
        attempt_started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
                async with client.stream("POST", endpoint, json=payload, headers=headers) as response:
                    if response.is_error:
                        detail = (await response.aread()).decode("utf-8", errors="replace")[:500]
                        if route_meta is not None:
                            route_meta.setdefault("route_attempts", []).append({"channel_id": channel.id, "channel": channel.name, "status": response.status_code, "latency_ms": int((time.perf_counter() - attempt_started) * 1000)})
                        raise ProviderCallError(
                            f"{channel.name}: HTTP {response.status_code}: {detail}",
                            retryable=_retryable_status(response.status_code),
                        )
                    async for line in response.aiter_lines():
                        if line:
                            emitted = True
                            if route_meta is not None and line.startswith("data: ") and "[DONE]" not in line:
                                try:
                                    chunk_data = json.loads(line[6:].strip())
                                    route_meta.setdefault("provider_request_id", chunk_data.get("id"))
                                    usage = chunk_data.get("usage") or {}
                                    if usage:
                                        _, _, usage_details = extract_usage(chunk_data, estimated_input)
                                        route_meta["usage_details"] = usage_details
                                        route_meta["raw_usage"] = usage
                                except (TypeError, ValueError, json.JSONDecodeError):
                                    pass
                            yield (line + "\n\n").encode("utf-8")
            mark_channel_success(db, channel)
            if route_meta is not None:
                route_meta["provider_channel_id"] = channel.id
                route_meta.setdefault("route_attempts", []).append({"channel_id": channel.id, "channel": channel.name, "status": 200, "latency_ms": int((time.perf_counter() - attempt_started) * 1000)})
            return
        except ProviderCallError as exc:
            last_detail = exc.detail
            mark_channel_failure(db, channel, exc.detail)
            if emitted or not exc.retryable:
                raise HTTPException(status_code=502, detail=exc.detail) from exc
        except httpx.HTTPError as exc:
            last_detail = f"{channel.name}: provider unavailable: {exc}"
            if route_meta is not None:
                route_meta.setdefault("route_attempts", []).append({"channel_id": channel.id, "channel": channel.name, "status": None, "error": str(exc)[:300], "latency_ms": int((time.perf_counter() - attempt_started) * 1000)})
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
    *,
    provider_cost_micros: int = 0,
    provider_channel_id: int | None = None,
    provider_request_id: str | None = None,
    usage_details: dict[str, int] | None = None,
    raw_usage: dict[str, Any] | None = None,
    route_attempts: list[dict[str, Any]] | None = None,
    price_version: str | None = None,
    amount_micros: int | None = None,
) -> UsageRecord:
    tracked_key = db.get(ApiKey, api_key.id)
    if tracked_key:
        tracked_key.last_used_at = utcnow()
    record = UsageRecord(
        request_id=request_id,
        trace_id=trace_id,
        account_id=api_key.account_id,
        workspace_id=db.get(Project, api_key.project_id).workspace_id if api_key.project_id and db.get(Project, api_key.project_id) else None,
        project_id=api_key.project_id,
        api_key_id=api_key.id,
        model=model.public_name,
        upstream_model=model.upstream_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        amount_micros=calculate_amount(model, input_tokens, output_tokens) if amount_micros is None else amount_micros,
        provider_cost_micros=provider_cost_micros,
        provider_channel_id=provider_channel_id,
        provider_request_id=provider_request_id,
        input_cache_hit_tokens=(usage_details or {}).get("input_cache_hit_tokens", 0),
        input_cache_miss_tokens=(usage_details or {}).get("input_cache_miss_tokens", 0),
        reasoning_tokens=(usage_details or {}).get("reasoning_tokens", 0),
        price_version=price_version,
        route_attempts_json=json.dumps(route_attempts or [], ensure_ascii=False, separators=(",", ":")),
        raw_usage_json=json.dumps(raw_usage or {}, ensure_ascii=False, separators=(",", ":")),
        status=status,
        latency_ms=latency_ms,
        error_message=error_message,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record
