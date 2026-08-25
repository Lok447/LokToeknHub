"""Model publication rules shared by the console, portal, and gateway."""

import json
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import ModelChannel, ModelConfig
from .provider_secrets import ProviderSecretError, decrypt_provider_secret


def channel_credentials_configured(channel: ModelChannel) -> bool:
    settings = get_settings()
    if channel.encrypted_api_key:
        try:
            return bool(decrypt_provider_secret(channel.encrypted_api_key))
        except ProviderSecretError:
            return False
    if channel.provider_api_key_env:
        return bool(os.getenv(channel.provider_api_key_env, "").strip())
    return bool(settings.default_provider_api_key.strip())


def model_publication_state(
    model: ModelConfig,
    channels: list[ModelChannel],
    settings=None,
) -> tuple[str, list[str]]:
    settings = settings or get_settings()
    reasons: list[str] = []
    catalog_metadata = {}
    if model.catalog_metadata_json:
        try:
            parsed = json.loads(model.catalog_metadata_json)
            if isinstance(parsed, dict):
                catalog_metadata = parsed
        except json.JSONDecodeError:
            reasons.append("模型目录元数据格式无效")
    api_type = catalog_metadata.get("api_type", "chat_completions")
    if api_type == "chat_completions":
        if model.input_price_micros_per_1k <= 0 or model.output_price_micros_per_1k <= 0:
            reasons.append("平台输入和输出价格均需大于 0")
    elif api_type in {"images_generations", "video_generations"}:
        if model.task_price_micros <= 0:
            reasons.append("任务模型需配置单次生成价格")
    else:
        reasons.append(f"当前网关尚未启用 {api_type} 统一调用适配器")
    active_channels = [channel for channel in channels if channel.active]
    if not active_channels:
        reasons.append("没有启用渠道")
    elif settings.mock_mode:
        if not any(channel.status == "healthy" for channel in active_channels):
            reasons.append("没有健康渠道")
    else:
        provider_checked = [
            channel for channel in active_channels
            if channel.status == "healthy" and channel.health_source == "provider"
        ]
        if not provider_checked:
            reasons.append("尚未通过真实供应商健康检测")
        elif not any(channel_credentials_configured(channel) for channel in provider_checked):
            reasons.append("健康渠道尚未配置供应商密钥")
    if settings.mock_mode:
        return ("mock_published" if model.active else "candidate"), reasons
    if model.active:
        return ("published" if not reasons else "blocked"), reasons
    return ("candidate" if not reasons else "blocked"), reasons


def model_is_callable(db: Session, model: ModelConfig) -> bool:
    channels = db.scalars(select(ModelChannel).where(ModelChannel.model_config_id == model.id)).all()
    state, _ = model_publication_state(model, channels)
    return state in {"published", "mock_published"}
