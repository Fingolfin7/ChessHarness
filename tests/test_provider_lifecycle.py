import unittest
from unittest.mock import patch

from chessharness.providers import anthropic as anthropic_provider
from chessharness.providers import google as google_provider
from chessharness.providers import openai as openai_provider
from chessharness.providers import openai_chatgpt as chatgpt_provider


class _FakeAsyncOpenAI:
    instances: list["_FakeAsyncOpenAI"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.close_calls = 0
        self.__class__.instances.append(self)

    async def close(self) -> None:
        self.close_calls += 1


class _FakeAnthropicClient:
    instances: list["_FakeAnthropicClient"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.close_calls = 0
        self.__class__.instances.append(self)

    async def close(self) -> None:
        self.close_calls += 1


class _FakeGoogleAsyncClient:
    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


class _FakeGoogleClient:
    instances: list["_FakeGoogleClient"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.aio = _FakeGoogleAsyncClient()
        self.close_calls = 0
        self.__class__.instances.append(self)

    def close(self) -> None:
        self.close_calls += 1


class ProviderLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _FakeAsyncOpenAI.instances.clear()
        _FakeAnthropicClient.instances.clear()
        _FakeGoogleClient.instances.clear()

    async def test_openai_close_is_idempotent_and_replaced_client_is_closed(self) -> None:
        async def refresh(force: bool) -> str:
            return "new-token"

        with patch.object(openai_provider, "AsyncOpenAI", _FakeAsyncOpenAI):
            provider = openai_provider.OpenAIProvider(
                api_key="old-token",
                model="gpt-5",
                token_refresher=refresh,
            )
            old_client = _FakeAsyncOpenAI.instances[0]
            self.assertIsNone(old_client.kwargs["timeout"].read)

            await provider._ensure_fresh_token()
            new_client = _FakeAsyncOpenAI.instances[1]
            self.assertEqual(old_client.close_calls, 1)

            await provider.close()
            await provider.close()

        self.assertEqual(new_client.close_calls, 1)

    async def test_chatgpt_close_is_idempotent_and_replaced_client_is_closed(self) -> None:
        async def refresh(force: bool) -> str:
            return "new-token"

        with patch.object(chatgpt_provider, "AsyncOpenAI", _FakeAsyncOpenAI):
            provider = chatgpt_provider.OpenAIChatGPTProvider(
                bearer_token="old-token",
                model="gpt-5.6-sol",
                token_refresher=refresh,
            )
            old_client = _FakeAsyncOpenAI.instances[0]
            await provider._ensure_fresh_token()
            new_client = _FakeAsyncOpenAI.instances[1]
            self.assertEqual(old_client.close_calls, 1)

            await provider.close()
            await provider.close()

        self.assertEqual(new_client.close_calls, 1)
        self.assertIsNone(new_client.kwargs["timeout"].read)

    async def test_anthropic_close_is_idempotent(self) -> None:
        with patch.object(anthropic_provider.anthropic, "AsyncAnthropic", _FakeAnthropicClient):
            provider = anthropic_provider.AnthropicProvider(
                api_key="token",
                model="claude-sonnet-4",
            )
            client = _FakeAnthropicClient.instances[0]
            self.assertIsNone(client.kwargs["timeout"].read)

            await provider.close()
            await provider.close()

        self.assertEqual(client.close_calls, 1)

    async def test_google_close_closes_async_and_sync_transports(self) -> None:
        with patch.object(google_provider.genai, "Client", _FakeGoogleClient):
            provider = google_provider.GoogleProvider(
                api_key="token",
                model="gemini-3-pro",
            )
            client = _FakeGoogleClient.instances[0]

            await provider.close()
            await provider.close()

        self.assertEqual(client.aio.close_calls, 1)
        self.assertEqual(client.close_calls, 1)


if __name__ == "__main__":
    unittest.main()

