import unittest

from chessharness.config import ProviderConfig
from chessharness.providers import create_provider
from chessharness.providers.anthropic import AnthropicProvider
from chessharness.providers.google import GoogleProvider
from chessharness.providers.openai import OpenAIProvider


class ProviderCapabilityTests(unittest.TestCase):
    def test_openai_override_takes_precedence(self) -> None:
        provider = OpenAIProvider(
            api_key="x",
            model="gpt-oss-20b",
            supports_vision_override=True,
        )
        self.assertTrue(provider.supports_vision)

    def test_google_override_takes_precedence(self) -> None:
        provider = GoogleProvider(
            api_key="x",
            model="gemini-3-flash-preview",
            supports_vision_override=False,
        )
        self.assertFalse(provider.supports_vision)

    def test_anthropic_override_takes_precedence(self) -> None:
        provider = AnthropicProvider(
            api_key="x",
            model="claude-opus-4-6",
            supports_vision_override=False,
        )
        self.assertFalse(provider.supports_vision)

    def test_create_provider_passes_override(self) -> None:
        providers_cfg = {
            "openai": ProviderConfig(api_key="x", models=[]),
        }
        provider = create_provider(
            "openai",
            "gpt-5",
            providers_cfg,
            supports_vision_override=False,
        )
        self.assertFalse(provider.supports_vision)

