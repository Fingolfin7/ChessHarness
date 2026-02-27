import unittest
from unittest.mock import patch

from chessharness.config import Config, GameConfig, ModelEntry, ProviderConfig
from chessharness.web import app as web_app


class WebProviderOverridesTests(unittest.TestCase):
    def test_providers_with_auth_overrides_injects_token(self) -> None:
        cfg = Config(
            game=GameConfig(),
            providers={
                "openai": ProviderConfig(
                    api_key="",
                    bearer_token="",
                    models=[ModelEntry(id="gpt-5", name="GPT-5", supports_vision=True)],
                    base_url=None,
                )
            },
        )
        with patch.object(web_app, "config", cfg), patch.object(
            web_app, "auth_tokens", {"openai": "bearer-123"}
        ):
            providers = web_app._providers_with_auth_overrides()

        self.assertIn("openai", providers)
        self.assertEqual(providers["openai"].auth_token, "bearer-123")
        self.assertEqual(providers["openai"].models[0].id, "gpt-5")

    def test_find_model_entry_returns_exact_match(self) -> None:
        providers_cfg = {
            "google": ProviderConfig(
                api_key="x",
                models=[
                    ModelEntry(id="gemini-3-flash-preview", name="Gemini 3 Flash", supports_vision=True),
                    ModelEntry(id="gemini-2.5-flash", name="Gemini 2.5 Flash", supports_vision=True),
                ],
            )
        }
        found = web_app._find_model_entry(
            providers_cfg, "google", "gemini-3-flash-preview"
        )
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.name, "Gemini 3 Flash")
        self.assertTrue(found.supports_vision)

