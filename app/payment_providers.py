from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Settings, get_settings


@dataclass(frozen=True)
class PaymentProviderDescriptor:
    id: str
    name: str
    implemented: bool
    configured: bool
    mode: str

    @property
    def available(self) -> bool:
        return self.implemented and self.configured

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "available": self.available}


def payment_providers(settings: Settings | None = None) -> list[PaymentProviderDescriptor]:
    settings = settings or get_settings()
    wechat_key_configured = bool(settings.wechat_private_key_path) and Path(settings.wechat_private_key_path).is_file()
    alipay_private_key_configured = bool(settings.alipay_private_key_path) and Path(settings.alipay_private_key_path).is_file()
    alipay_public_key_configured = bool(settings.alipay_public_key_path) and Path(settings.alipay_public_key_path).is_file()
    return [
        PaymentProviderDescriptor("manual", "人工确认", True, True, "admin"),
        PaymentProviderDescriptor(
            "wechat",
            "微信支付",
            False,
            all((settings.wechat_merchant_id, settings.wechat_app_id, settings.wechat_certificate_serial)) and wechat_key_configured,
            "native",
        ),
        PaymentProviderDescriptor(
            "alipay",
            "支付宝",
            False,
            bool(settings.alipay_app_id) and alipay_private_key_configured and alipay_public_key_configured,
            "page",
        ),
    ]


def require_available_provider(provider_id: str) -> PaymentProviderDescriptor:
    provider = next((item for item in payment_providers() if item.id == provider_id), None)
    if not provider:
        raise ValueError("unknown payment provider")
    if not provider.implemented:
        raise ValueError("payment provider adapter is not implemented")
    if not provider.configured:
        raise ValueError("payment provider is not configured")
    return provider
