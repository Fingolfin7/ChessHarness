"""
Anthropic (Claude) provider.

Anthropic's API separates the system prompt from user/assistant turns,
so the system message is extracted from the messages list and passed
via the dedicated `system` parameter.

All claude-3+ models support vision.
"""

from __future__ import annotations

import base64
from typing import Any

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
        self._last_response_metadata: dict[str, object] | None = None

    @property
    def supports_vision(self) -> bool:
        if self._supports_vision_override is not None:
            return self._supports_vision_override
        return any(self._model.startswith(p) for p in _VISION_PREFIXES)

    @property
    def last_response_metadata(self) -> dict[str, object] | None:
        return self._last_response_metadata

    async def complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 5120,
        reasoning_effort: str | None = None,
    ) -> str:
        self._last_response_metadata = None
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
            self._last_response_metadata = _message_metadata(response)
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
        self._last_response_metadata = None
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
                get_final_message = getattr(stream, "get_final_message", None)
                if callable(get_final_message):
                    self._last_response_metadata = _message_metadata(
                        await get_final_message(),
                    )
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


def _message_metadata(message: Any) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if message is None:
        return metadata

    for key in ("id", "model", "stop_reason", "stop_sequence"):
        value = getattr(message, key, None)
        if value is not None:
            metadata[key] = value

    usage = getattr(message, "usage", None)
    if usage is not None:
        usage_metadata = {}
        for key in ("input_tokens", "output_tokens"):
            value = getattr(usage, key, None)
            if value is not None:
                usage_metadata[key] = value
        if usage_metadata:
            metadata["usage"] = usage_metadata
    return metadata
