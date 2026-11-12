from timbot.config import settings
from timbot.services.providers.base import AIProvider
from timbot.services.providers.ollama import OllamaProvider

_PROVIDERS: dict[str, type[AIProvider]] = {"ollama": OllamaProvider}

def get_provider() -> AIProvider:
    provider_type = _PROVIDERS.get(settings.ai_provider.lower())
    if provider_type is None:
        supported = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"Unsupported AI_PROVIDER '{settings.ai_provider}'. Available providers: {supported}.")
    return provider_type(settings)

__all__ = ["AIProvider", "OllamaProvider", "get_provider"]
