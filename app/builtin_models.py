"""The starter model catalogue used by local trial environments.

These records deliberately contain no provider credential.  In mock mode they
are callable immediately and exercise the same billing and routing paths as a
production model.  A production operator must explicitly configure a real
upstream channel before exposing models to customers.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BuiltinModel:
    public_name: str
    display_name: str
    summary: str
    capabilities: tuple[str, ...]
    context_window: str
    input_price_micros_per_1k: int
    output_price_micros_per_1k: int


BUILTIN_MODELS = (
    BuiltinModel(
        public_name="lok-chat",
        display_name="Lok Chat",
        summary="通用对话、内容生成与日常问答。",
        capabilities=("对话", "文本生成", "工具调用"),
        context_window="32K",
        input_price_micros_per_1k=1_000,
        output_price_micros_per_1k=3_000,
    ),
    BuiltinModel(
        public_name="lok-reason",
        display_name="Lok Reason",
        summary="适用于复杂分析、代码理解与多步骤推理。",
        capabilities=("深度推理", "代码", "分析"),
        context_window="64K",
        input_price_micros_per_1k=3_000,
        output_price_micros_per_1k=9_000,
    ),
    BuiltinModel(
        public_name="lok-vision",
        display_name="Lok Vision",
        summary="面向图文理解、视觉问答与内容提取。",
        capabilities=("图像理解", "视觉问答", "文本生成"),
        context_window="32K",
        input_price_micros_per_1k=2_000,
        output_price_micros_per_1k=6_000,
    ),
)


def model_metadata(public_name: str) -> dict[str, object]:
    """Return presentation metadata while keeping routing data in the database."""
    for model in BUILTIN_MODELS:
        if model.public_name == public_name:
            return {
                "display_name": model.display_name,
                "summary": model.summary,
                "capabilities": list(model.capabilities),
                "context_window": model.context_window,
                "builtin": True,
            }
    return {
        "display_name": public_name,
        "summary": "通过 LokSystem TOKEN 统一调用的 OpenAI 兼容模型。",
        "capabilities": ["OpenAI 兼容"],
        "context_window": None,
        "builtin": False,
    }
