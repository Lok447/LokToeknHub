"""The starter model catalogue used by local trial environments.

These records deliberately contain no provider credential.  In mock mode they
are callable immediately and exercise the same billing and routing paths as a
production model.  A production operator must explicitly configure a real
upstream channel before exposing models to customers.
"""

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BuiltinModel:
    public_name: str
    display_name: str
    provider: str
    summary: str
    capabilities: tuple[str, ...]
    modalities: tuple[str, ...]
    supported_parameters: tuple[str, ...]
    context_window: str
    input_price_micros_per_1k: int
    output_price_micros_per_1k: int


BUILTIN_MODELS = (
    BuiltinModel(
        public_name="lok-chat",
        display_name="Lok Chat",
        provider="LokSystem",
        summary="通用对话、内容生成与日常问答。",
        capabilities=("对话", "文本生成", "工具调用"),
        modalities=("text", "tool"),
        supported_parameters=("stream", "temperature", "max_tokens"),
        context_window="32K",
        input_price_micros_per_1k=1_000,
        output_price_micros_per_1k=3_000,
    ),
    BuiltinModel(
        public_name="lok-reason",
        display_name="Lok Reason",
        provider="LokSystem",
        summary="适用于复杂分析、代码理解与多步骤推理。",
        capabilities=("深度推理", "代码", "分析"),
        modalities=("text", "reasoning", "code"),
        supported_parameters=("stream", "temperature", "max_tokens"),
        context_window="64K",
        input_price_micros_per_1k=3_000,
        output_price_micros_per_1k=9_000,
    ),
    BuiltinModel(
        public_name="lok-vision",
        display_name="Lok Vision",
        provider="LokSystem",
        summary="面向图文理解、视觉问答与内容提取。",
        capabilities=("图像理解", "视觉问答", "文本生成"),
        modalities=("text", "image"),
        supported_parameters=("stream", "temperature", "max_tokens"),
        context_window="32K",
        input_price_micros_per_1k=2_000,
        output_price_micros_per_1k=6_000,
    ),
)


def model_metadata(public_name: str, catalog_metadata_json: str | None = None) -> dict[str, object]:
    """Return presentation metadata while keeping routing data in the database."""
    if catalog_metadata_json:
        try:
            metadata = json.loads(catalog_metadata_json)
        except json.JSONDecodeError:
            metadata = None
        if isinstance(metadata, dict):
            return _normalize_catalog_metadata(public_name, metadata)
    for model in BUILTIN_MODELS:
        if model.public_name == public_name:
            return {
                "display_name": model.display_name,
                "provider": model.provider,
                "summary": model.summary,
                "capabilities": list(model.capabilities),
                "modalities": list(model.modalities),
                "supported_parameters": list(model.supported_parameters),
                "context_window": model.context_window,
                "api_type": "chat_completions",
                "builtin": True,
            }
    return {
        "display_name": public_name,
        "provider": "第三方模型",
        "summary": "通过 LokSystem TOKEN 统一调用的 OpenAI 兼容模型。",
        "capabilities": ["OpenAI 兼容"],
        "modalities": ["text"],
        "supported_parameters": ["stream", "temperature", "max_tokens"],
        "context_window": None,
        "builtin": False,
    }


def _normalize_catalog_metadata(public_name: str, metadata: dict[str, Any]) -> dict[str, object]:
    """Keep database-backed catalogue data safe for portal serialization."""
    def string_list(value: object) -> list[str]:
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    return {
        "display_name": metadata.get("display_name") if isinstance(metadata.get("display_name"), str) else public_name,
        "provider": metadata.get("provider") if isinstance(metadata.get("provider"), str) else "第三方模型",
        "summary": metadata.get("summary") if isinstance(metadata.get("summary"), str) else "通过 LokToken 统一调用的 OpenAI 兼容模型。",
        "capabilities": string_list(metadata.get("capabilities")),
        "modalities": string_list(metadata.get("modalities")),
        "supported_parameters": string_list(metadata.get("supported_parameters")),
        "context_window": metadata.get("context_window") if isinstance(metadata.get("context_window"), str) else None,
        "model_version": metadata.get("model_version") if isinstance(metadata.get("model_version"), str) else None,
        "max_output_tokens": metadata.get("max_output_tokens") if isinstance(metadata.get("max_output_tokens"), int) else None,
        "gateway_profile": metadata.get("gateway_profile") if isinstance(metadata.get("gateway_profile"), dict) else None,
        "api_type": metadata.get("api_type") if isinstance(metadata.get("api_type"), str) else "chat_completions",
        "pricing_source_url": metadata.get("pricing_source_url") if isinstance(metadata.get("pricing_source_url"), str) else None,
        "builtin": False,
    }
