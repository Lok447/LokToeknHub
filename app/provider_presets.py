"""OpenAI-compatible provider templates and verified catalogue references."""

from dataclasses import asdict, dataclass



DEEPSEEK_PRICING_URL = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing"
QWEN_PRICING_URL = "https://help.aliyun.com/zh/model-studio/model-pricing"
GLM_PRICING_URL = "https://bigmodel.cn/pricing"
KIMI_PRICING_URL = "https://platform.kimi.com/docs/pricing/chat"
MINIMAX_PRICING_URL = "https://platform.minimaxi.com/docs/guides/pricing-paygo"
DOUBAO_PRICING_URL = "https://docs.volcengine.com/docs/82379/1544106?lang=zh"

PROVIDER_PRICING_SOURCES = {
    "DeepSeek": ("DeepSeek 官方价格", DEEPSEEK_PRICING_URL),
    "Qwen": ("阿里云百炼官方价格", QWEN_PRICING_URL),
    "GLM": ("智谱 GLM 官方价格", GLM_PRICING_URL),
    "Kimi": ("Kimi 官方价格", KIMI_PRICING_URL),
    "MiniMax": ("MiniMax 官方价格", MINIMAX_PRICING_URL),
    "Doubao": ("火山方舟官方价格", DOUBAO_PRICING_URL),
}

PROVIDER_GATEWAY_PROFILES = {
    "DeepSeek": {
        "protocol": "openai_chat_completions",
        "auth_scheme": "bearer",
        "discovery_path": "/models",
        "request_path": "/chat/completions",
        "stream_transport": "sse",
        "usage_source": "response.usage",
        "parameter_policy": "verified_common_only",
    },
    "Qwen": {
        "protocol": "openai_chat_completions",
        "auth_scheme": "bearer",
        "discovery_path": "/models",
        "request_path": "/chat/completions",
        "stream_transport": "sse",
        "usage_source": "response.usage",
        "parameter_policy": "verified_common_only",
    },
    "GLM": {
        "protocol": "openai_chat_completions",
        "auth_scheme": "bearer",
        "discovery_path": "/models",
        "request_path": "/chat/completions",
        "stream_transport": "sse",
        "usage_source": "response.usage",
        "parameter_policy": "verified_common_only",
    },
    "Kimi": {
        "protocol": "openai_chat_completions",
        "auth_scheme": "bearer",
        "discovery_path": "/models",
        "request_path": "/chat/completions",
        "stream_transport": "sse",
        "usage_source": "response.usage",
        "parameter_policy": "verified_common_only",
    },
    "MiniMax": {
        "protocol": "openai_chat_completions",
        "auth_scheme": "bearer",
        "discovery_path": "/models",
        "request_path": "/chat/completions",
        "stream_transport": "sse",
        "usage_source": "response.usage",
        "parameter_policy": "verified_common_only",
    },
    "Doubao": {
        "protocol": "openai_chat_completions",
        "auth_scheme": "bearer",
        "discovery_path": "/models",
        "request_path": "/chat/completions",
        "stream_transport": "sse",
        "usage_source": "response.usage",
        "parameter_policy": "verified_common_only",
    },
}


def gateway_profile(provider: str, api_type: str) -> dict[str, object]:
    profile = dict(PROVIDER_GATEWAY_PROFILES.get(provider, {
        "protocol": "openai_chat_completions",
        "auth_scheme": "bearer",
        "discovery_path": "/models",
        "request_path": "/chat/completions",
        "stream_transport": "sse",
        "usage_source": "response.usage",
        "parameter_policy": "passthrough_unknown",
        "parameter_aliases": {"max_completion_tokens": "max_tokens"},
    }))
    if api_type != "chat_completions":
        task_paths = {
            "images_generations": "/images/generations",
            "video_generations": "/videos/generations",
            "audio_speech": "/audio/speech",
            "audio_transcriptions": "/audio/transcriptions",
        }
        profile.update({
            "protocol": "async_task",
            "request_path": task_paths.get(api_type, "/audio/speech"),
            "stream_transport": "none",
            "usage_source": "task_result",
            "parameter_policy": "task_specific" if api_type.startswith("audio_") else "task_specific_pending_adapter",
            "parameter_aliases": {},
        })
    else:
        profile.setdefault("parameter_aliases", {"max_completion_tokens": "max_tokens"})
    return profile

# Removed from the curated catalogue after the Qwen3.8 series rollout. Startup
# cleanup deletes these candidates only when they have no usage history.
DEPRECATED_PROVIDER_MODEL_PUBLIC_NAMES = (
    "qwen/qwen-image-plus",
    "qwen/qwen-vl-max",
    "qwen/qwen-max",
    "qwen/qwen-turbo",
    "qwen/qwen-plus",
    "qwen/qwen3.8-plus",
    "qwen/qwen3.8",
    "qwen/qwen3-coder",
    "qwen/qwen3-vl-max",
)


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
    platform_task_price_micros: int = 0
    provider_task_cost_micros: int = 0


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
    *,
    modalities: tuple[str, ...] = ("text",),
    capabilities: tuple[str, ...] = ("对话", "推理", "代码"),
    summary: str = "DeepSeek V4 系列，支持长上下文对话、推理与代码生成。",
) -> ProviderModelPreset:
    """Build DeepSeek V4 metadata from the official CNY per-million-token list."""

    def cny(cny_micros: int) -> int:
        return cny_micros

    # The platform's default billable price uses the official uncached, off-peak
    # rate. Operators can raise it for margin, but the account ledger stays CNY.
    platform_input_price = cny(cache_miss_off_peak) // 1_000
    platform_output_price = cny(output_off_peak) // 1_000
    catalog_metadata = {
        "display_name": display_name,
        "provider": "DeepSeek",
        "summary": summary,
        "capabilities": list(capabilities),
        "modalities": list(modalities),
        "supported_parameters": ["stream", "temperature", "max_tokens"],
        "context_window": "1M",
        "model_version": version,
        "max_output_tokens": 384_000,
        "api_type": "chat_completions",
        "gateway_profile": gateway_profile("DeepSeek", "chat_completions"),
    }
    official_pricing = {
        "source": "DeepSeek official pricing",
        "source_url": DEEPSEEK_PRICING_URL,
        "currency": "CNY",
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
    max_output_tokens: int = 0,
) -> ProviderModelPreset:
    """Build a catalog-only candidate without inventing provider pricing."""
    pricing_source = PROVIDER_PRICING_SOURCES.get(provider)
    official_pricing = None
    if pricing_source:
        source_name, source_url = pricing_source
        pricing_unit = "per_1m_tokens" if api_type == "chat_completions" else "provider_defined"
        official_pricing = {
            "source": source_name,
            "source_url": source_url,
            "currency": "CNY",
            "unit": pricing_unit,
            "price_basis": "provider_list_price",
            "verification_status": "manual_review_required",
            "provider": provider,
            "note": "已挂载官方价格来源；模型级价格需按供应商当前目录核验后录入。",
        }
    return ProviderModelPreset(
        model_id=model_id,
        public_name=public_name,
        display_name=display_name,
        model_version=model_version,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        catalog_metadata={
            "display_name": display_name,
            "provider": provider,
            "summary": summary,
            "capabilities": list(capabilities),
            "modalities": list(modalities),
            "supported_parameters": ["stream", "temperature", "max_tokens"] if api_type == "chat_completions" else [],
            "context_window": context_window,
            "max_output_tokens": max_output_tokens,
            "model_version": model_version,
            "api_type": api_type,
            "catalog_status": "curated_candidate",
            "requires_provider_verification": True,
            "gateway_profile": gateway_profile(provider, api_type),
            "pricing_source_url": pricing_source[1] if pricing_source else None,
            "pricing_verification_status": "manual_review_required" if pricing_source else "unavailable",
        },
        official_pricing=official_pricing,
    )


def qwen_token_model(
    model_id: str,
    public_name: str,
    display_name: str,
    *,
    tiers: tuple[tuple[int, float, float], ...],
    modalities: tuple[str, ...] = ("text",),
    capabilities: tuple[str, ...] = ("对话",),
    summary: str,
    context_window: str,
) -> ProviderModelPreset:
    """Build a Qwen candidate backed by the official standard list price."""
    candidate = catalogue_model(
        model_id,
        public_name,
        display_name,
        "Qwen",
        modalities=modalities,
        capabilities=capabilities,
        summary=summary,
        context_window=context_window,
    )

    def micros(yuan: float) -> int:
        return round(yuan * 1_000_000)

    normalized_tiers = []
    lower_bound = 0
    for max_tokens, input_yuan, output_yuan in tiers:
        normalized_tiers.append({
            "min_input_tokens_exclusive": lower_bound,
            "max_input_tokens_inclusive": max_tokens,
            "input_micros": micros(input_yuan),
            "output_micros": micros(output_yuan),
        })
        lower_bound = max_tokens
    first = normalized_tiers[0]
    official_pricing = {
        "source": "Alibaba Cloud Model Studio official pricing",
        "source_url": QWEN_PRICING_URL,
        "currency": "CNY",
        "unit": "per_1m_tokens",
        "region": "cn-beijing",
        "price_basis": "standard_list_price",
        "verification_status": "verified",
        "page_updated_at": "2026-08-21 20:41:43",
        "retrieved_at": "2026-08-21",
        "default_reference": {
            "input_micros": first["input_micros"],
            "output_micros": first["output_micros"],
            "max_input_tokens_inclusive": first["max_input_tokens_inclusive"],
        },
        "tiers": normalized_tiers,
    }
    return ProviderModelPreset(
        model_id=candidate.model_id,
        public_name=candidate.public_name,
        display_name=candidate.display_name,
        model_version=candidate.model_version,
        context_window=candidate.context_window,
        max_output_tokens=candidate.max_output_tokens,
        catalog_metadata=candidate.catalog_metadata,
        official_pricing=official_pricing,
        platform_input_price_micros_per_1k=round(first["input_micros"] / 1000),
        platform_output_price_micros_per_1k=round(first["output_micros"] / 1000),
    )


def qwen_image_model(
    model_id: str,
    public_name: str,
    display_name: str,
    *,
    output_prices: tuple[tuple[str, float], ...],
    summary: str,
) -> ProviderModelPreset:
    """Build an image candidate without mixing per-image costs into token fields."""
    candidate = catalogue_model(
        model_id,
        public_name,
        display_name,
        "Qwen",
        api_type="images_generations",
        modalities=("image",),
        capabilities=("文生图", "图像编辑"),
        summary=summary,
    )
    official_pricing = {
        "source": "Alibaba Cloud Model Studio official pricing",
        "source_url": QWEN_PRICING_URL,
        "currency": "CNY",
        "unit": "per_image",
        "region": "cn-beijing",
        "price_basis": "standard_list_price",
        "verification_status": "verified",
        "page_updated_at": "2026-08-21 20:41:43",
        "retrieved_at": "2026-08-21",
        "input_per_image_micros": 20_000,
        "output_prices": [
            {"resolution": resolution, "output_per_image_micros": round(yuan * 1_000_000)}
            for resolution, yuan in output_prices
        ],
    }
    return ProviderModelPreset(
        model_id=candidate.model_id,
        public_name=candidate.public_name,
        display_name=candidate.display_name,
        model_version=candidate.model_version,
        context_window=candidate.context_window,
        max_output_tokens=candidate.max_output_tokens,
        catalog_metadata=candidate.catalog_metadata,
        official_pricing=official_pricing,
        platform_task_price_micros=official_pricing["input_per_image_micros"] + official_pricing["output_prices"][0]["output_per_image_micros"],
        provider_task_cost_micros=official_pricing["input_per_image_micros"] + official_pricing["output_prices"][0]["output_per_image_micros"],
    )


PROVIDER_PRESETS = (
    ProviderPreset(
        id="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        models=(
            deepseek_model("deepseek-v4-flash", "DeepSeek V4 Flash", "DeepSeek-V4-Flash-0731", 50_000, 100_000, 1_500_000, 3_000_000, 4_500_000, 9_000_000),
            deepseek_model(
                "deepseek-v4-flash-vision-exp",
                "DeepSeek V4 Flash Vision Experimental",
                "DeepSeek-V4-Flash-Vision-Exp",
                50_000,
                100_000,
                1_500_000,
                3_000_000,
                4_500_000,
                9_000_000,
                modalities=("text", "image"),
                capabilities=("图像理解", "视觉问答", "对话"),
                summary="DeepSeek V4 实验性视觉模型，支持文本与图像输入。",
            ),
            deepseek_model("deepseek-v4-pro", "DeepSeek V4 Pro", "DeepSeek-V4-Pro-0813", 150_000, 300_000, 4_500_000, 9_000_000, 13_500_000, 27_000_000),
        ),
        note="已按 DeepSeek 官网人民币价格维护；默认平台价格采用未命中缓存、低峰档。Vision Experimental 为实验性视觉候选，需单独核验目录和调用能力。",
    ),
    ProviderPreset(
        id="qwen",
        name="Qwen / DashScope",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        models=(
            # Qwen3.8 SOTA candidates. DashScope model IDs are verified during
            # provider sync; candidates stay unpublished until the account
            # exposes the exact ID and pricing is reviewed.
            qwen_token_model("qwen3.8-max", "qwen/qwen3.8-max", "Qwen3.8-Max", tiers=((1_000_000, 12, 36),), capabilities=("对话", "复杂推理", "Agent"), context_window="1M", summary="Qwen3.8 Max 候选，面向复杂推理和生产级 Agent 场景。"),
            qwen_token_model("qwen3.8-2.4t-a95b", "qwen/qwen3.8-2.4t-a95b", "Qwen3.8 2.4T A95B", tiers=((1_000_000, 12, 36),), capabilities=("对话", "推理", "长文本"), context_window="1M", summary="DashScope 真实目录中的 Qwen3.8 旗舰 MoE 候选。"),
            qwen_token_model("qwen3.8-27b", "qwen/qwen3.8-27b", "Qwen3.8 27B", tiers=((1_000_000, 3, 12),), capabilities=("对话", "推理"), context_window="1M", summary="DashScope 真实目录中的 Qwen3.8 27B 候选。"),
            qwen_token_model("qwen3.7-plus", "qwen/qwen3.7-plus", "Qwen3.7 Plus", tiers=((256_000, 2, 8), (1_000_000, 6, 24)), capabilities=("对话", "推理", "长文本"), context_window="1M", summary="DashScope 当前可用的 Plus 系列候选。"),
            qwen_token_model("qwen3-coder-next", "qwen/qwen3-coder-next", "Qwen3 Coder Next", tiers=((32_000, 1, 4), (128_000, 1.5, 6), (256_000, 2.5, 10)), capabilities=("代码", "Agent", "工具调用"), context_window="256K", summary="Qwen-Coder Next 候选，面向代码生成、审查和 Agent 工具调用。"),
            qwen_token_model("qwen3-coder-plus", "qwen/qwen3-coder-plus", "Qwen3 Coder Plus", tiers=((32_000, 4, 16), (128_000, 6, 24), (256_000, 10, 40), (1_000_000, 20, 200)), capabilities=("代码", "Agent", "工具调用"), context_window="1M", summary="Qwen-Coder Plus 候选，面向复杂代码和 Agent 编排。"),
            qwen_token_model("qwen3-vl-flash", "qwen/qwen3-vl-flash", "Qwen3-VL Flash", tiers=((32_000, 0.15, 1.5), (128_000, 0.3, 3), (256_000, 0.6, 6)), modalities=("text", "image"), capabilities=("图像理解", "视觉问答", "快速响应"), context_window="256K", summary="Qwen3-VL Flash 候选，面向低延迟图像理解。"),
            qwen_token_model("qwen3-vl-plus", "qwen/qwen3-vl-plus", "Qwen3-VL Plus", tiers=((32_000, 1, 10), (128_000, 1.5, 15), (256_000, 3, 30)), modalities=("text", "image"), capabilities=("图像理解", "视觉问答"), context_window="256K", summary="Qwen-VL Plus 候选，面向图文对话和视觉问答。"),
            qwen_image_model("qwen-image-3.0", "qwen/qwen-image-3.0", "Qwen Image 3.0", output_prices=(("1K", 0.18), ("2K", 0.18)), summary="DashScope 真实目录中的 Qwen Image 3.0 任务型候选。"),
            qwen_image_model("qwen-image-3.0-pro", "qwen/qwen-image-3.0-pro", "Qwen Image 3.0 Pro", output_prices=(("1K", 0.25), ("2K", 0.50)), summary="DashScope 真实目录中的 Qwen Image 3.0 Pro 任务型候选。"),
            catalogue_model("wan2.1-t2v-turbo", "qwen/wan2.1-t2v-turbo", "Wan 2.1 T2V Turbo", "Qwen", api_type="video_generations", modalities=("video",), capabilities=("文生视频",), summary="Wan 视频任务型候选；需异步任务适配器、回调/轮询和按时长计费规则。"),
        ),
        note="首批预置 DashScope 真实目录中的 Qwen3.8、Plus、Qwen-Coder、Qwen-VL、Qwen Image 3.0 及 Wan 视频任务候选；价格采用阿里云百炼标准原价，不包含活动优惠。",
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


def provider_catalogue_matches(preset_id: str, model_ids: set[str] | list[str]) -> bool:
    """Reject credentials or endpoints that clearly belong to another provider."""
    normalized = {str(model_id).strip().lower() for model_id in model_ids if str(model_id).strip()}
    if not normalized:
        return False
    markers = {
        "deepseek": ("deepseek",),
        "qwen": ("qwen", "wan"),
        "glm": ("glm", "cogview", "cogvideo"),
        "kimi": ("kimi", "moonshot"),
        "minimax": ("minimax", "speech-", "image-", "video-"),
        "doubao": ("doubao", "seedream", "seedance", "ep-"),
    }.get(preset_id)
    if not markers:
        return True
    return any(any(marker in model_id for marker in markers) for model_id in normalized)
