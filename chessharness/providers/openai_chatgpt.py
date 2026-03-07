"""
OpenAI ChatGPT/Codex provider via the ChatGPT Codex endpoint.

This path is separate from regular OpenAI API key auth:
  - Base URL defaults to https://chatgpt.com/backend-api/codex
  - Uses the Responses API payload shape (not chat/completions)
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from openai import AsyncOpenAI

from chessharness.providers.base import LLMProvider, Message, ProviderError

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://chatgpt.com/backend-api/codex"
_VISION_PREFIXES = ("gpt-4o", "gpt-5", "o1", "o3", "o4")


class OpenAIChatGPTProvider(LLMProvider):
    """Provider for ChatGPT/Codex-session style auth and endpoint."""

    def __init__(
        self,
        bearer_token: str,
        model: str,
        base_url: str | None = None,
        supports_vision_override: bool | None = None,
        token_refresher: Callable[[bool], Awaitable[str]] | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url or _DEFAULT_BASE_URL
        self._supports_vision_override = supports_vision_override
        self._token_refresher = token_refresher
        self._current_token = bearer_token
        self._client = AsyncOpenAI(
            api_key=bearer_token,
            base_url=self._base_url,
        )
        self._last_response_metadata: dict[str, object] | None = None

    @property
    def supports_vision(self) -> bool:
        if self._supports_vision_override is not None:
            return self._supports_vision_override
        return any(self._model.startswith(p) for p in _VISION_PREFIXES)

    @property
    def last_response_metadata(self) -> dict[str, object] | None:
        return self._last_response_metadata

    def _rebuild_client(self, new_token: str) -> None:
        self._current_token = new_token
        self._client = AsyncOpenAI(
            api_key=new_token,
            base_url=self._base_url,
        )

    async def _ensure_fresh_token(self, force: bool = False) -> None:
        if self._token_refresher is None:
            return
        token = await self._token_refresher(force)
        if token and token != self._current_token:
            logger.info("Refreshed ChatGPT/Codex token; rebuilding client.")
            self._rebuild_client(token)

    async def complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 5120,
        reasoning_effort: str | None = None,
    ) -> str:
        chunks: list[str] = []
        try:
            async for chunk in self.stream(
                messages,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
            ):
                chunks.append(chunk)
            return "".join(chunks).strip()
        except Exception as exc:
            raise ProviderError("openai_chatgpt", str(exc), cause=exc) from exc

    async def stream(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 5120,
        reasoning_effort: str | None = None,
    ):
        self._last_response_metadata = None
        await self._ensure_fresh_token()
        base_kwargs = self._build_request_kwargs(
            messages,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )

        async def _run_with(kwargs: dict[str, Any]):
            req = dict(kwargs)
            # Codex endpoint requires streaming mode.
            req["stream"] = True
            return await self._client.responses.create(**req)

        try:
            event_stream = await _run_with(base_kwargs)
        except Exception as exc:
            msg = str(exc).lower()
            # Some ChatGPT/Codex deployments reject max_output_tokens and/or reasoning.
            if "unsupported parameter: max_output_tokens" in msg:
                fallback = dict(base_kwargs)
                fallback.pop("max_output_tokens", None)
                try:
                    event_stream = await _run_with(fallback)
                except Exception as exc2:
                    raise ProviderError("openai_chatgpt", str(exc2), cause=exc2) from exc2
            elif "unsupported parameter: reasoning" in msg:
                fallback = dict(base_kwargs)
                fallback.pop("reasoning", None)
                try:
                    event_stream = await _run_with(fallback)
                except Exception as exc2:
                    raise ProviderError("openai_chatgpt", str(exc2), cause=exc2) from exc2
            else:
                raise ProviderError("openai_chatgpt", str(exc), cause=exc) from exc

        try:
            async for event in event_stream:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", "")
                    if isinstance(delta, str) and delta:
                        yield delta
                elif event_type in {"response.completed", "response.incomplete", "response.failed"}:
                    self._last_response_metadata = _response_event_metadata(event)
        except Exception as exc:
            raise ProviderError("openai_chatgpt", str(exc), cause=exc) from exc

    def _build_request_kwargs(
        self,
        messages: list[Message],
        *,
        max_tokens: int,
        reasoning_effort: str | None,
    ) -> dict[str, Any]:
        instructions = next((m.content for m in messages if m.role == "system"), "")
        kwargs: dict[str, Any] = {
            "model": self._model,
            "instructions": instructions,
            "input": self._build_input([m for m in messages if m.role != "system"]),
            "max_output_tokens": max_tokens,
            # Keep server-side conversation state off; game history is explicit.
            "store": False,
        }
        if reasoning_effort in {"low", "medium", "high"}:
            kwargs["reasoning"] = {"effort": reasoning_effort}
        return kwargs

    def _build_input(self, messages: list[Message]) -> list[dict]:
        items: list[dict] = []
        for msg in messages:
            role = "assistant" if msg.role == "assistant" else "user"
            text_part_type = "output_text" if role == "assistant" else "input_text"
            parts: list[dict] = [{"type": text_part_type, "text": msg.content}]
            if role == "user" and msg.image_bytes and self.supports_vision:
                b64 = base64.b64encode(msg.image_bytes).decode()
                parts.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{b64}",
                    }
                )
            items.append({"role": role, "content": parts})
        return items


def _response_event_metadata(event: Any) -> dict[str, object]:
    metadata: dict[str, object] = {
        "event_type": str(getattr(event, "type", "unknown")),
    }
    response = getattr(event, "response", None)
    if response is None:
        return metadata

    for key in ("id", "model", "status"):
        value = getattr(response, key, None)
        if value is not None:
            metadata[key] = value

    for key in ("error", "incomplete_details", "usage"):
        value = _normalize_metadata_value(getattr(response, key, None))
        if value not in (None, {}, []):
            metadata[key] = value
    return metadata


def _normalize_metadata_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _normalize_metadata_value(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_normalize_metadata_value(v) for v in value if v is not None]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(exclude_none=True)
        return _normalize_metadata_value(dumped)

    if hasattr(value, "__dict__"):
        items = {
            key: _normalize_metadata_value(val)
            for key, val in vars(value).items()
            if not key.startswith("_") and val is not None
        }
        return items

    return str(value)
