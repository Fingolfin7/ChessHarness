"""
Provider factory.

create_provider() is the single entry point for instantiating any LLMProvider.
Kimi/Moonshot and other OpenAI-compatible providers are handled as special cases of OpenAIProvider with a custom base_url.

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


def _copilot_chat_headers() -> dict[str, str]:
    # Copilot Chat endpoint expects IDE-style metadata headers.
    return {
        "Editor-Version": "vscode/1.95.3",
        "Editor-Plugin-Version": "copilot-chat/0.22.1",
        "Copilot-Integration-Id": "vscode-chat",
    }


def create_provider(
    provider_name: str,
    model_id: str,
    providers_cfg: dict[str, ProviderConfig],
    supports_vision_override: bool | None = None,
) -> LLMProvider:
    """Instantiate the correct LLMProvider for the given provider name and model ID."""
    prov_cfg = providers_cfg.get(provider_name)
    if prov_cfg is None:
        raise ValueError(
            f"Provider '{provider_name}' is not defined in the providers section of config.yaml"
        )

    token = prov_cfg.auth_token
    if not token:
        raise ValueError(
            f"Provider '{provider_name}' needs either 'api_key' or 'bearer_token' in config.yaml"
        )

    match provider_name:
        case "openai":
            return OpenAIProvider(
                api_key=token,
                model=model_id,
                base_url=prov_cfg.base_url,
                supports_vision_override=supports_vision_override,
            )
        case "anthropic":
            return AnthropicProvider(
                api_key=token,
                model=model_id,
                supports_vision_override=supports_vision_override,
            )
        case "google":
            return GoogleProvider(
                api_key=token,
                model=model_id,
                supports_vision_override=supports_vision_override,
            )
        case "kimi" | "copilot" | "copilot_chat" | "groq" | "openrouter":
            if not prov_cfg.base_url:
                raise ValueError(f"{provider_name} provider requires 'base_url' in config")
            default_headers = _copilot_chat_headers() if provider_name in {"copilot", "copilot_chat"} else None
            return OpenAIProvider(
                api_key=token,
                model=model_id,
                base_url=prov_cfg.base_url,
                provider_label=provider_name,
                default_headers=default_headers,
                supports_vision_override=supports_vision_override,
            )
        case _:
            raise ValueError(
                f"Unknown provider: '{provider_name}'. "
                "Supported: openai, anthropic, google, kimi, copilot_chat, groq, openrouter"
            )
