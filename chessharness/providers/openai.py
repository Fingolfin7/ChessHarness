"""
OpenAI provider — also handles any OpenAI-compatible API (e.g., Kimi/Moonshot).

Pass base_url to redirect requests to a compatible endpoint.
Vision support is inferred from model name prefixes.

Parameter compatibility notes:
- max_completion_tokens: used by all models (replaces the deprecated max_tokens).
- temperature: not supported by reasoning models (o1, o3, o4-series); omitted for those.
"""

from __future__ import annotations

import base64
import logging

from openai import AsyncOpenAI

from chessharness.providers.base import LLMProvider, Message, ProviderError

logger = logging.getLogger(__name__)

# Model name prefixes that support vision (image) input
_VISION_PREFIXES = (
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-4-vision",
    "o1",
    "o3",
    "o4",
    "gpt-5",
    "kimi-vl",
)

# Reasoning models don't accept a custom temperature (must be omitted or 1)
_REASONING_PREFIXES = ("o1", "o3", "o4")


def _is_reasoning_model(model: str) -> bool:
    return any(model.startswith(p) for p in _REASONING_PREFIXES)


class OpenAIProvider(LLMProvider):
    """
    Supports all OpenAI chat models and OpenAI-compatible endpoints.

    For Kimi (Moonshot), pass:
        base_url="https://api.moonshot.cn/v1"
        api_key=<moonshot key>
    """

    def __init__(self, api_key: str, model: str, base_url: str | None = None, provider_label: str = "openai") -> None:
        self._model = model
        self._provider_label = provider_label
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    @property
    def supports_vision(self) -> bool:
        return any(self._model.startswith(p) for p in _VISION_PREFIXES)

    async def complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 5120,
    ) -> str:
        try:
            api_messages = self._build_api_messages(messages)
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=api_messages,  # type: ignore[arg-type]
                max_completion_tokens=max_tokens,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.error("complete() failed [provider=%s model=%s base_url=%s]: %s",
                         self._provider_label, self._model, self._client.base_url, exc, exc_info=True)
            raise ProviderError(self._provider_label, str(exc), cause=exc) from exc

    async def stream(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 5120,
    ):
        try:
            api_messages = self._build_api_messages(messages)
            async with await self._client.chat.completions.create(
                model=self._model,
                messages=api_messages,  # type: ignore[arg-type]
                max_completion_tokens=max_tokens,
                stream=True,
            ) as stream:
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta
        except Exception as exc:
            logger.error("stream() failed [provider=%s model=%s base_url=%s]: %s",
                         self._provider_label, self._model, self._client.base_url, exc, exc_info=True)
            raise ProviderError(self._provider_label, str(exc), cause=exc) from exc

    def _build_api_messages(self, messages: list[Message]) -> list[dict]:
        result: list[dict] = []
        for msg in messages:
            if msg.image_bytes and self.supports_vision:
                b64 = base64.b64encode(msg.image_bytes).decode()
                result.append({
                    "role": msg.role,
                    "content": [
                        {"type": "text", "text": msg.content},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                })
            else:
                result.append({"role": msg.role, "content": msg.content})
        return result
