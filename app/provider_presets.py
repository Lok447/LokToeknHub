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


def catalogue_model(
    model_id: str,
    public_name: str,
    display_name: str,
    provider: str,
    *,
    api_type: str = "chat_completions",
    modalities: tuple[str, ...] = ("text",),
    capabilities: tuple[str, ...] = ("对话",),
    summary: str = "OpenAI 兼容模型候选，需完成供应商检测后发布。",
    model_version: str = "",
    context_window: str = "按供应商配置",
) -> ProviderModelPreset:
    """Build a catalog-only candidate without inventing provider pricing."""
    return ProviderModelPreset(
        model_id=model_id,
        public_name=public_name,
        display_name=display_name,
        model_version=model_version,
        context_window=context_window,
        max_output_tokens=0,
        catalog_metadata={
            "display_name": display_name,
            "provider": provider,
            "summary": summary,
            "capabilities": list(capabilities),
            "modalities": list(modalities),
            "supported_parameters": ["stream", "temperature", "max_tokens"] if api_type == "chat_completions" else [],
            "context_window": context_window,
            "model_version": model_version,
            "api_type": api_type,
            "catalog_status": "curated_candidate",
            "requires_provider_verification": True,
        },
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
        models=(
            catalogue_model("qwen-plus", "qwen/qwen-plus", "Qwen Plus", "Qwen", capabilities=("对话", "长文本")),
            catalogue_model("qwen-turbo", "qwen/qwen-turbo", "Qwen Turbo", "Qwen", capabilities=("对话", "快速响应")),
            catalogue_model("qwen-max", "qwen/qwen-max", "Qwen Max", "Qwen", capabilities=("对话", "复杂推理")),
            catalogue_model("qwen3-coder-plus", "qwen/qwen3-coder-plus", "Qwen3 Coder Plus", "Qwen", capabilities=("代码", "Agent")),
            catalogue_model("qwen-vl-max-latest", "qwen/qwen-vl-max", "Qwen VL Max", "Qwen", modalities=("text", "image"), capabilities=("图像理解", "视觉问答")),
            catalogue_model("qwen-image-plus", "qwen/qwen-image-plus", "Qwen Image Plus", "Qwen", api_type="images_generations", modalities=("image",), capabilities=("文生图", "图像编辑")),
            catalogue_model("wan2.1-t2v-turbo", "qwen/wan2.1-t2v-turbo", "Wan 2.1 T2V Turbo", "Qwen", api_type="video_generations", modalities=("video",), capabilities=("文生视频",)),
        ),
        note="预置 Qwen 文本、多模态、图像和视频候选；请按 DashScope 账号实际目录确认模型 ID 与价格。",
    ),
    ProviderPreset(
        id="glm",
        name="GLM / 智谱",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key_env="ZHIPU_API_KEY",
        models=(
            catalogue_model("glm-4.5", "glm/glm-4.5", "GLM-4.5", "GLM", capabilities=("对话", "推理", "代码")),
            catalogue_model("glm-4.5-air", "glm/glm-4.5-air", "GLM-4.5 Air", "GLM", capabilities=("对话", "快速响应")),
            catalogue_model("glm-4.5-flash", "glm/glm-4.5-flash", "GLM-4.5 Flash", "GLM", capabilities=("对话", "低成本")),
            catalogue_model("glm-4.1v-thinking-flash", "glm/glm-4.1v-thinking-flash", "GLM-4.1V Thinking Flash", "GLM", modalities=("text", "image"), capabilities=("视觉理解", "推理")),
            catalogue_model("cogview-4-250304", "glm/cogview-4", "CogView-4", "GLM", api_type="images_generations", modalities=("image",), capabilities=("文生图", "图像编辑")),
            catalogue_model("cogvideox-flash", "glm/cogvideox-flash", "CogVideoX Flash", "GLM", api_type="video_generations", modalities=("video",), capabilities=("文生视频", "图生视频")),
        ),
        note="预置 GLM 文本、视觉、CogView 图像和 CogVideoX 视频候选；图像/视频等待统一适配器启用。",
    ),
    ProviderPreset(
        id="kimi",
        name="Kimi / Moonshot",
        base_url="https://api.moonshot.cn/v1",
        api_key_env="MOONSHOT_API_KEY",
        models=(
            catalogue_model("kimi-k2-0905-preview", "kimi/kimi-k2-0905-preview", "Kimi K2", "Kimi", capabilities=("对话", "推理", "代码")),
            catalogue_model("moonshot-v1-8k", "kimi/moonshot-v1-8k", "Moonshot V1 8K", "Kimi"),
            catalogue_model("moonshot-v1-32k", "kimi/moonshot-v1-32k", "Moonshot V1 32K", "Kimi"),
            catalogue_model("moonshot-v1-128k", "kimi/moonshot-v1-128k", "Moonshot V1 128K", "Kimi", capabilities=("对话", "长文本")),
        ),
        note="预置 Kimi K2 与 Moonshot V1 候选；请以 Moonshot 控制台当前模型目录为准。",
    ),
    ProviderPreset(
        id="minimax",
        name="MiniMax",
        base_url="https://api.minimax.io/v1",
        api_key_env="MINIMAX_API_KEY",
        models=(
            catalogue_model("MiniMax-M2.1", "minimax/MiniMax-M2.1", "MiniMax M2.1", "MiniMax", capabilities=("对话", "推理", "代码")),
            catalogue_model("MiniMax-Text-01", "minimax/MiniMax-Text-01", "MiniMax Text 01", "MiniMax"),
            catalogue_model("MiniMax-VL-01", "minimax/MiniMax-VL-01", "MiniMax VL 01", "MiniMax", modalities=("text", "image"), capabilities=("图像理解", "视觉问答")),
            catalogue_model("image-01", "minimax/image-01", "MiniMax Image-01", "MiniMax", api_type="images_generations", modalities=("image",), capabilities=("文生图", "图像编辑")),
            catalogue_model("video-01", "minimax/video-01", "MiniMax Video-01", "MiniMax", api_type="video_generations", modalities=("video",), capabilities=("文生视频", "图生视频")),
        ),
        note="预置 MiniMax 文本、视觉、图像和视频候选；接口版本与模型 ID 需按区域账号核验。",
    ),
    ProviderPreset(
        id="doubao",
        name="Doubao / 火山方舟",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key_env="ARK_API_KEY",
        models=(
            catalogue_model("doubao-seed-1-6-250615", "doubao/doubao-seed-1-6", "Doubao Seed 1.6", "Doubao", capabilities=("对话", "推理", "Agent")),
            catalogue_model("doubao-seed-1-6-flash-250828", "doubao/doubao-seed-1-6-flash", "Doubao Seed 1.6 Flash", "Doubao", capabilities=("对话", "快速响应")),
            catalogue_model("doubao-seed-1-6-vision-250815", "doubao/doubao-seed-1-6-vision", "Doubao Seed 1.6 Vision", "Doubao", modalities=("text", "image"), capabilities=("图像理解", "视觉问答")),
            catalogue_model("doubao-seedream-4-0", "doubao/doubao-seedream-4-0", "Seedream 4.0", "Doubao", api_type="images_generations", modalities=("image",), capabilities=("文生图", "图像编辑")),
            catalogue_model("doubao-seedance-1-0-pro", "doubao/doubao-seedance-1-0-pro", "Seedance 1.0 Pro", "Doubao", api_type="video_generations", modalities=("video",), capabilities=("文生视频", "图生视频")),
        ),
        note="预置火山方舟 Doubao、Seedream、Seedance 候选；方舟文本模型通常使用 endpoint ID，请配置后执行模型发现。",
    ),
)


def provider_preset_data(preset: ProviderPreset) -> dict[str, object]:
    data = asdict(preset)
    data["model_ids"] = list(preset.model_ids)
    return data


def get_provider_preset(preset_id: str) -> ProviderPreset | None:
    return next((item for item in PROVIDER_PRESETS if item.id == preset_id), None)
