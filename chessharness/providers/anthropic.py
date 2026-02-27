"""
Anthropic (Claude) provider.

Anthropic's API separates the system prompt from user/assistant turns,
so the system message is extracted from the messages list and passed
via the dedicated `system` parameter.

All claude-3+ models support vision.
"""

from __future__ import annotations

import base64

import anthropic

from chessharness.providers.base import LLMProvider, Message, ProviderError

_VISION_PREFIXES = (
    "claude-3",
    "claude-opus",
    "claude-sonnet",
    "claude-haiku",
)


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        supports_vision_override: bool | None = None,
    ) -> None:
        self._model = model
        self._supports_vision_override = supports_vision_override
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    @property
    def supports_vision(self) -> bool:
        if self._supports_vision_override is not None:
            return self._supports_vision_override
        return any(self._model.startswith(p) for p in _VISION_PREFIXES)

    async def complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 5120,
        reasoning_effort: str | None = None,
    ) -> str:
        try:
            system_content = next(
                (m.content for m in messages if m.role == "system"), ""
            )
            user_messages = self._build_api_messages(
                [m for m in messages if m.role != "system"]
            )
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system_content,
                messages=user_messages,  # type: ignore[arg-type]
            )
            block = response.content[0]
            return (block.text if hasattr(block, "text") else "").strip()
        except Exception as exc:
            raise ProviderError("anthropic", str(exc), cause=exc) from exc

    async def stream(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 5120,
        reasoning_effort: str | None = None,
    ):
        try:
            system_content = next(
                (m.content for m in messages if m.role == "system"), ""
            )
            user_messages = self._build_api_messages(
                [m for m in messages if m.role != "system"]
            )
            async with self._client.messages.stream(
                model=self._model,
                max_tokens=max_tokens,
                system=system_content,
                messages=user_messages,  # type: ignore[arg-type]
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as exc:
            raise ProviderError("anthropic", str(exc), cause=exc) from exc

    def _build_api_messages(self, messages: list[Message]) -> list[dict]:
        result: list[dict] = []
        for msg in messages:
            if msg.image_bytes and self.supports_vision:
                b64 = base64.b64encode(msg.image_bytes).decode()
                result.append({
                    "role": msg.role,
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": msg.content},
                    ],
                })
            else:
                result.append({"role": msg.role, "content": msg.content})
        return result
