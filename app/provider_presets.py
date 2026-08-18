"""OpenAI-compatible provider templates and verified catalogue references."""

from dataclasses import asdict, dataclass

from .config import get_settings


DEEPSEEK_PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing"


@dataclass(frozen=True)
class ProviderModelPreset:
    model_id: str
    public_name: str
    display_name: str
    model_version: str
    context_window: str
    max_output_tokens: int
    catalog_metadata: dict[str, object]
    official_pricing: dict[str, object] | None = None
    platform_input_price_micros_per_1k: int = 0
    platform_output_price_micros_per_1k: int = 0


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    name: str
    base_url: str
    api_key_env: str
    models: tuple[ProviderModelPreset, ...]
    note: str

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(model.model_id for model in self.models)

    def get_model(self, model_id: str) -> ProviderModelPreset | None:
        return next((model for model in self.models if model.model_id == model_id), None)


def deepseek_model(
    model_id: str,
    display_name: str,
    version: str,
    cache_hit_off_peak: int,
    cache_hit_peak: int,
    cache_miss_off_peak: int,
    cache_miss_peak: int,
    output_off_peak: int,
    output_peak: int,
) -> ProviderModelPreset:
    """Build DeepSeek V4 metadata, converting the provider's USD prices to CNY."""
    rate = get_settings().usd_to_cny_rate

    def cny(usd_micros: int) -> int:
        return round(usd_micros * rate)

    # The platform's default billable price uses the official uncached, off-peak
    # rate. Operators can raise it for margin, but the account ledger stays CNY.
    platform_input_price = cny(cache_miss_off_peak) // 1_000
    platform_output_price = cny(output_off_peak) // 1_000
    catalog_metadata = {
        "display_name": display_name,
        "provider": "DeepSeek",
        "summary": "DeepSeek V4 系列，支持长上下文对话、推理与代码生成。",
        "capabilities": ["对话", "推理", "代码"],
        "modalities": ["text"],
        "supported_parameters": ["stream", "temperature", "max_tokens"],
        "context_window": "1M",
        "model_version": version,
        "max_output_tokens": 384_000,
    }
    official_pricing = {
        "source": "DeepSeek official pricing",
        "source_url": DEEPSEEK_PRICING_URL,
        "currency": "CNY",
        "source_currency": "USD",
        "exchange_rate_usd_to_cny": rate,
        "unit": "per_1m_tokens",
        "version": version,
        "effective_date": "2026-08-18",
        "off_peak": {
            "input_cache_hit_micros": cny(cache_hit_off_peak),
            "input_cache_miss_micros": cny(cache_miss_off_peak),
            "output_micros": cny(output_off_peak),
        },
        "peak": {
            "input_cache_hit_micros": cny(cache_hit_peak),
            "input_cache_miss_micros": cny(cache_miss_peak),
            "output_micros": cny(output_peak),
        },
    }
    return ProviderModelPreset(
        model_id=model_id,
        public_name=model_id,
        display_name=display_name,
        model_version=version,
        context_window="1M",
        max_output_tokens=384_000,
        catalog_metadata=catalog_metadata,
        official_pricing=official_pricing,
        platform_input_price_micros_per_1k=platform_input_price,
        platform_output_price_micros_per_1k=platform_output_price,
    )


PROVIDER_PRESETS = (
    ProviderPreset(
        id="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        models=(
            deepseek_model("deepseek-v4-flash", "DeepSeek V4 Flash", "DeepSeek-V4-Flash-0731", 7_000, 14_000, 220_000, 440_000, 660_000, 1_320_000),
            deepseek_model("deepseek-v4-pro", "DeepSeek V4 Pro", "DeepSeek-V4-Pro-0813", 22_000, 44_000, 660_000, 1_320_000, 1_980_000, 3_960_000),
        ),
        note="已按配置汇率将 DeepSeek 官网价格换算为人民币；默认平台价格采用未命中缓存、低峰档。",
    ),
    ProviderPreset(
        id="qwen",
        name="Qwen / DashScope",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        models=tuple(ProviderModelPreset(model_id=item, public_name=f"qwen/{item}", display_name=item, model_version="", context_window="", max_output_tokens=0, catalog_metadata={}) for item in ("qwen-plus", "qwen-turbo", "qwen-max")),
        note="请确认地域、模型版本和 DashScope 计费价格。",
    ),
    ProviderPreset(
        id="kimi",
        name="Kimi / Moonshot",
        base_url="https://api.moonshot.cn/v1",
        api_key_env="MOONSHOT_API_KEY",
        models=tuple(ProviderModelPreset(model_id=item, public_name=f"kimi/{item}", display_name=item, model_version="", context_window="", max_output_tokens=0, catalog_metadata={}) for item in ("moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k")),
        note="请以 Moonshot 控制台当前可用模型为准。",
    ),
    ProviderPreset(
        id="minimax",
        name="MiniMax",
        base_url="https://api.minimax.io/v1",
        api_key_env="MINIMAX_API_KEY",
        models=tuple(ProviderModelPreset(model_id=item, public_name=f"minimax/{item}", display_name=item, model_version="", context_window="", max_output_tokens=0, catalog_metadata={}) for item in ("MiniMax-Text-01", "MiniMax-M1")),
        note="MiniMax 接口地址和模型名称可能按区域变化，请先执行渠道检测。",
    ),
)


def provider_preset_data(preset: ProviderPreset) -> dict[str, object]:
    data = asdict(preset)
    data["model_ids"] = list(preset.model_ids)
    return data


def get_provider_preset(preset_id: str) -> ProviderPreset | None:
    return next((item for item in PROVIDER_PRESETS if item.id == preset_id), None)
