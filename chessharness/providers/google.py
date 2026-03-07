"""
Google Gemini provider via the google-genai SDK (v1.x, native async).

The google-generativeai package is deprecated as of early 2026.
google-genai is the official replacement and has a native AsyncClient,
so no run_in_executor workaround is needed.

Vision is supported by gemini-1.5-* and gemini-2-* model families.
"""

from __future__ import annotations

import base64
import io
from typing import Any

from google import genai
from google.genai import types

from chessharness.providers.base import LLMProvider, Message, ProviderError

_VISION_PREFIXES = (
    # Current Gemini families are multimodal, including Gemini 3 preview models.
    "gemini-",
    # Some integrations expose model names with provider-style prefixes.
    "models/gemini-",
    "google/gemini-",
    # Legacy alias kept for compatibility.
    "gemini-pro-vision",
)


class GoogleProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        supports_vision_override: bool | None = None,
    ) -> None:
        self._model_name = model
        self._supports_vision_override = supports_vision_override
        self._client = genai.Client(api_key=api_key)
        self._last_response_metadata: dict[str, object] | None = None

    @property
    def supports_vision(self) -> bool:
        if self._supports_vision_override is not None:
            return self._supports_vision_override
        return any(self._model_name.startswith(p) for p in _VISION_PREFIXES)

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
                (m.content for m in messages if m.role == "system"), None
            )
            contents = self._build_contents(
                [m for m in messages if m.role != "system"]
            )

            gen_config = types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                system_instruction=system_content,
            )

            response = await self._client.aio.models.generate_content(
                model=self._model_name,
                contents=contents,
                config=gen_config,
            )
            self._last_response_metadata = _response_metadata(response)
            return (response.text or "").strip()
        except Exception as exc:
            raise ProviderError("google", str(exc), cause=exc) from exc

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
                (m.content for m in messages if m.role == "system"), None
            )
            contents = self._build_contents(
                [m for m in messages if m.role != "system"]
            )
            gen_config = types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                system_instruction=system_content,
            )
            response_stream = await self._client.aio.models.generate_content_stream(
                model=self._model_name,
                contents=contents,
                config=gen_config,
            )
            last_chunk = None
            async for chunk in response_stream:
                last_chunk = chunk
                if chunk.text:
                    yield chunk.text
            self._last_response_metadata = _response_metadata(last_chunk)
        except Exception as exc:
            raise ProviderError("google", str(exc), cause=exc) from exc

    def _build_contents(self, messages: list[Message]) -> list:
        """
        Build the contents list for the google-genai SDK.

        Each message becomes a types.Content with the correct role so that
        multi-turn conversation history is preserved.  Google uses "model"
        for assistant turns instead of "assistant".
        """
        contents = []
        for msg in messages:
            role = "model" if msg.role == "assistant" else "user"
            parts: list = []
            if msg.image_bytes and self.supports_vision:
                parts.append(
                    types.Part.from_bytes(
                        data=msg.image_bytes,
                        mime_type="image/png",
                    )
                )
            parts.append(types.Part(text=msg.content))
            contents.append(types.Content(role=role, parts=parts))
        return contents


def _response_metadata(response: Any) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if response is None:
        return metadata

    for key in ("response_id", "model_version"):
        value = getattr(response, key, None)
        if value is not None:
            metadata[key] = value

    candidates = getattr(response, "candidates", None) or []
    if candidates:
        candidate = candidates[0]
        finish_reason = getattr(candidate, "finish_reason", None)
        if finish_reason is not None:
            metadata["finish_reason"] = str(finish_reason)
        safety_ratings = getattr(candidate, "safety_ratings", None)
        if safety_ratings:
            metadata["safety_ratings"] = [str(rating) for rating in safety_ratings]

    prompt_feedback = getattr(response, "prompt_feedback", None)
    if prompt_feedback is not None:
        metadata["prompt_feedback"] = str(prompt_feedback)

    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        usage_metadata = {}
        for key in ("prompt_token_count", "candidates_token_count", "total_token_count"):
            value = getattr(usage, key, None)
            if value is not None:
                usage_metadata[key] = value
        if usage_metadata:
            metadata["usage"] = usage_metadata
    return metadata
