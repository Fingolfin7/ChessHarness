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
from collections.abc import Callable, Awaitable

from openai import AsyncOpenAI, AuthenticationError as _OpenAIAuthError

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
    # OpenAI-compatible gateways (Copilot/OpenRouter/etc.) often expose
    # Claude/Gemini model IDs through Chat Completions.
    "claude-",
    "gemini-",
    "anthropic/claude",
    "google/gemini",
)

# Reasoning models don't accept a custom temperature (must be omitted or 1)
_REASONING_PREFIXES = ("o1", "o3", "o4")


def _is_reasoning_model(model: str) -> bool:
    return any(model.startswith(p) for p in _REASONING_PREFIXES)


def _supports_reasoning_effort(model: str) -> bool:
    return _is_reasoning_model(model) or model.startswith("gpt-5")


class OpenAIProvider(LLMProvider):
    """
    Supports all OpenAI chat models and OpenAI-compatible endpoints.

    For Kimi (Moonshot), pass:
        base_url="https://api.moonshot.cn/v1"
        api_key=<moonshot key>
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        provider_label: str = "openai",
        default_headers: dict[str, str] | None = None,
        supports_vision_override: bool | None = None,
        token_refresher: Callable[[bool], Awaitable[str]] | None = None,
    ) -> None:
        self._model = model
        self._provider_label = provider_label
        self._supports_vision_override = supports_vision_override
        self._token_refresher = token_refresher
        self._base_url = base_url
        self._default_headers = default_headers
        self._current_key = api_key
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=default_headers,
        )

    def _rebuild_client(self, new_key: str) -> None:
        """Recreate the AsyncOpenAI client with a refreshed token."""
        self._current_key = new_key
        self._client = AsyncOpenAI(
            api_key=new_key,
            base_url=self._base_url,
            default_headers=self._default_headers,
        )

    async def _ensure_fresh_token(self, force: bool = False) -> None:
        """Call token_refresher and rebuild the client if the token changed."""
        if self._token_refresher is None:
            return
        new_key = await self._token_refresher(force)
        if new_key and new_key != self._current_key:
            logger.info(
                "Token refreshed — rebuilding client [provider=%s]",
                self._provider_label,
            )
            self._rebuild_client(new_key)

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
        await self._ensure_fresh_token()
        try:
            return await self._do_complete(messages, max_tokens=max_tokens, reasoning_effort=reasoning_effort)
        except _OpenAIAuthError as exc:
            if self._token_refresher is not None:
                logger.warning(
                    "Auth error on complete(), forcing token refresh [provider=%s model=%s]",
                    self._provider_label, self._model,
                )
                await self._ensure_fresh_token(force=True)
                try:
                    return await self._do_complete(messages, max_tokens=max_tokens, reasoning_effort=reasoning_effort)
                except Exception as exc2:
                    logger.error("complete() failed after token refresh [provider=%s model=%s]: %s",
                                 self._provider_label, self._model, exc2, exc_info=True)
                    raise ProviderError(self._provider_label, str(exc2), cause=exc2) from exc2
            logger.error("complete() failed [provider=%s model=%s]: %s",
                         self._provider_label, self._model, exc, exc_info=True)
            raise ProviderError(self._provider_label, str(exc), cause=exc) from exc
        except Exception as exc:
            logger.error("complete() failed [provider=%s model=%s]: %s",
                         self._provider_label, self._model, exc, exc_info=True)
            raise ProviderError(self._provider_label, str(exc), cause=exc) from exc

    async def stream(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 5120,
        reasoning_effort: str | None = None,
    ):
        await self._ensure_fresh_token()
        retried = False
        while True:
            try:
                async for chunk in self._do_stream(messages, max_tokens=max_tokens, reasoning_effort=reasoning_effort):
                    yield chunk
                return
            except _OpenAIAuthError as exc:
                if not retried and self._token_refresher is not None:
                    retried = True
                    logger.warning(
                        "Auth error on stream(), forcing token refresh [provider=%s model=%s]",
                        self._provider_label, self._model,
                    )
                    await self._ensure_fresh_token(force=True)
                    continue
                logger.error("stream() failed [provider=%s model=%s]: %s",
                             self._provider_label, self._model, exc, exc_info=True)
                raise ProviderError(self._provider_label, str(exc), cause=exc) from exc
            except Exception as exc:
                logger.error("stream() failed [provider=%s model=%s]: %s",
                             self._provider_label, self._model, exc, exc_info=True)
                raise ProviderError(self._provider_label, str(exc), cause=exc) from exc

    async def _do_complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int,
        reasoning_effort: str | None,
    ) -> str:
        api_messages = self._build_api_messages(messages)
        request_kwargs: dict = {}
        if reasoning_effort and _supports_reasoning_effort(self._model):
            request_kwargs["reasoning_effort"] = reasoning_effort
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=api_messages,  # type: ignore[arg-type]
            max_completion_tokens=max_tokens,
            **request_kwargs,
        )
        return (response.choices[0].message.content or "").strip()

    async def _do_stream(
        self,
        messages: list[Message],
        *,
        max_tokens: int,
        reasoning_effort: str | None,
    ):
        api_messages = self._build_api_messages(messages)
        request_kwargs: dict = {}
        if reasoning_effort and _supports_reasoning_effort(self._model):
            request_kwargs["reasoning_effort"] = reasoning_effort
        async with await self._client.chat.completions.create(
            model=self._model,
            messages=api_messages,  # type: ignore[arg-type]
            max_completion_tokens=max_tokens,
            stream=True,
            **request_kwargs,
        ) as stream:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta

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
