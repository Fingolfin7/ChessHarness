"""
Provider factory.

create_provider() is the single entry point for instantiating any LLMProvider.
Kimi/Moonshot is handled as a special case of OpenAIProvider with a custom base_url.

To add a new provider:
  1. Create chessharness/providers/<name>.py implementing LLMProvider
  2. Add a case here in create_provider()
  3. Add the provider section to config.yaml
"""

from __future__ import annotations

from chessharness.config import ProviderConfig
from chessharness.providers.base import LLMProvider, Message, ProviderError
from chessharness.providers.openai import OpenAIProvider
from chessharness.providers.anthropic import AnthropicProvider
from chessharness.providers.google import GoogleProvider

__all__ = [
    "LLMProvider",
    "Message",
    "ProviderError",
    "OpenAIProvider",
    "AnthropicProvider",
    "GoogleProvider",
    "create_provider",
]


def create_provider(
    provider_name: str,
    model_id: str,
    providers_cfg: dict[str, ProviderConfig],
) -> LLMProvider:
    """Instantiate the correct LLMProvider for the given provider name and model ID."""
    prov_cfg = providers_cfg.get(provider_name)
    if prov_cfg is None:
        raise ValueError(
            f"Provider '{provider_name}' is not defined in the providers section of config.yaml"
        )

    match provider_name:
        case "openai":
            return OpenAIProvider(api_key=prov_cfg.api_key, model=model_id)
        case "anthropic":
            return AnthropicProvider(api_key=prov_cfg.api_key, model=model_id)
        case "google":
            return GoogleProvider(api_key=prov_cfg.api_key, model=model_id)
        case "kimi":
            if not prov_cfg.base_url:
                raise ValueError("Kimi provider requires 'base_url' in config")
            return OpenAIProvider(
                api_key=prov_cfg.api_key,
                model=model_id,
                base_url=prov_cfg.base_url,
            )
        case _:
            raise ValueError(
                f"Unknown provider: '{provider_name}'. "
                "Supported: openai, anthropic, google, kimi"
            )
