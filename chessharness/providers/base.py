"""
Abstract LLM provider interface.

All concrete providers (OpenAI, Anthropic, Google, …) implement LLMProvider.
The Message NamedTuple is the canonical way to pass conversation turns,
including optional image bytes for vision-capable providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import inspect
from typing import AsyncIterator, Literal, NamedTuple

import httpx


ProviderErrorKind = Literal[
    "timeout",
    "rate_limit",
    "auth",
    "image_unsupported",
    "upstream",
    "empty_response",
    "unknown",
]

# Keep connection setup and pool acquisition bounded, while leaving response
# reads to the game-level shared deadline.  This preserves the configured
# move_timeout even for models that legitimately spend a long time reasoning.
DEFAULT_NETWORK_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=None,
    write=30.0,
    pool=10.0,
)
_RETRYABLE_ERROR_KINDS = frozenset(
    {"timeout", "rate_limit", "upstream", "empty_response", "unknown"}
)


class Message(NamedTuple):
    role: str                    # "system" | "user" | "assistant"
    content: str                 # text content
    image_bytes: bytes | None = None  # set only on user turns for vision models


class LLMProvider(ABC):
    """Abstract base for all LLM API backends."""

    @property
    @abstractmethod
    def supports_vision(self) -> bool:
        """True if this provider/model accepts image inputs."""
        ...

    @property
    def last_response_metadata(self) -> dict[str, object] | None:
        """Best-effort provider completion metadata from the most recent call."""
        return None

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 5120,
        reasoning_effort: str | None = None,
    ) -> str:
        """
        Send messages to the LLM and return the raw text response.

        Args:
            messages: Full conversation including system message at index 0.
            max_tokens: Token budget for the response. Default 5120 to allow
                        for the structured ## Reasoning / ## Move format.

        Raises:
            ProviderError: Wraps provider-specific exceptions for uniform handling.
        """
        ...

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 5120,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        """
        Yield raw text tokens as the model produces them.

        Implementors must define this as an async generator (async def + yield).
        Callers iterate with:  async for chunk in provider.stream(messages): ...

        Raises:
            ProviderError: Wraps provider-specific exceptions for uniform handling.
        """
        raise NotImplementedError
        yield  # marks this as an async generator so subclasses can too

    async def close(self) -> None:
        """Release provider-owned network resources.

        Providers commonly keep an HTTP connection pool alive for the whole
        game.  The default is intentionally a no-op so third-party providers
        written against the original interface remain source-compatible.
        """


async def close_resource(resource: object | None) -> None:
    """Close an SDK resource regardless of whether its API is sync or async.

    OpenAI and Anthropic expose an async ``close`` method, while the Google
    client exposes ``aio.aclose`` for its asynchronous transport and a sync
    ``close`` for the parent client.  Keeping this small adapter here avoids
    provider-specific lifecycle assumptions leaking into player/game code.
    """

    if resource is None:
        return
    closer = getattr(resource, "aclose", None)
    if not callable(closer):
        closer = getattr(resource, "close", None)
    if not callable(closer):
        return
    result = closer()
    if inspect.isawaitable(result):
        await result


class ProviderError(Exception):
    """Raised when a provider API call fails unrecoverably."""

    def __init__(
        self,
        provider: str,
        message: str,
        cause: Exception | None = None,
        *,
        kind: ProviderErrorKind | str | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.provider = provider
        self.cause = cause
        inherited_kind = cause.kind if isinstance(cause, ProviderError) else None
        inherited_retryable = (
            cause.retryable if isinstance(cause, ProviderError) else None
        )
        self.kind = kind or inherited_kind or classify_provider_error(message, cause)
        if retryable is not None:
            self.retryable = retryable
        elif inherited_retryable is not None:
            self.retryable = inherited_retryable
        else:
            self.retryable = self.kind in _RETRYABLE_ERROR_KINDS
        super().__init__(f"[{provider}] {message}")


def classify_provider_error(
    message: str,
    cause: BaseException | None = None,
) -> ProviderErrorKind:
    """Infer a stable failure category from common SDK/network errors.

    Classification deliberately gives transport failures precedence over text
    mentioning images.  A request that times out while carrying an image is a
    timeout, not evidence that the model rejected image input; only explicit
    unsupported-image responses should trigger a text fallback.
    """

    if isinstance(cause, ProviderError):
        return cause.kind  # type: ignore[return-value]

    status_code = _status_code(cause)
    text = " ".join(
        value
        for value in (str(message), str(cause) if cause is not None else "")
        if value
    ).casefold()
    class_name = type(cause).__name__.casefold() if cause is not None else ""

    if status_code in {408, 504} or _contains_any(
        text,
        "timed out",
        "timeout",
        "deadline exceeded",
        "read timeout",
        "connect timeout",
        "pool timeout",
    ) or _contains_any(
        class_name,
        "timeout",
    ):
        return "timeout"

    if status_code == 429 or _contains_any(
        text,
        "rate limit",
        "rate-limit",
        "too many requests",
        "quota exceeded",
        "resource exhausted",
        "status code: 429",
        "http 429",
    ) or _contains_any(class_name, "ratelimit", "resourceexhausted"):
        return "rate_limit"

    if status_code in {401, 403} or _contains_any(
        text,
        "authentication",
        "unauthorized",
        "forbidden",
        "invalid api key",
        "invalid token",
        "token expired",
        "permission denied",
        "status code: 401",
        "status code: 403",
    ) or _contains_any(
        class_name,
        "authentication",
        "unauthorized",
        "permissiondenied",
    ):
        return "auth"

    # Restrict this to explicit capability/validation language.  In
    # particular, do not match a generic "image" mention or an image upload
    # timeout: those should remain ordinary upstream/timeout failures.
    if _contains_any(
        text,
        "image input is not supported",
        "image inputs are not supported",
        "image input not supported",
        "image inputs not supported",
        "does not support image",
        "doesn't support image",
        "unsupported image",
        "image_url is not supported",
        "image_url not supported",
        "input_image is not supported",
        "multimodal input is not supported",
        "vision is not supported",
        "images are not supported",
        "image content is not supported",
        "invalid image input",
    ):
        return "image_unsupported"

    if status_code is not None and status_code >= 500:
        return "upstream"
    if _contains_any(
        text,
        "connection error",
        "network error",
        "remote protocol",
        "service unavailable",
        "bad gateway",
        "upstream error",
        "server error",
        "temporarily unavailable",
        "connection reset",
        "connection refused",
        "status code: 5",
        "http 5",
    ) or _contains_any(
        class_name,
        "connection",
        "connecterror",
        "readerror",
        "remoteprotocol",
        "servererror",
    ):
        return "upstream"

    if _contains_any(
        text,
        "empty response",
        "empty completion",
        "no output tokens",
        "returned no output",
    ):
        return "empty_response"

    return "unknown"


def _status_code(cause: BaseException | None) -> int | None:
    """Extract an HTTP status from common SDK exception shapes."""

    if cause is None:
        return None
    for candidate in (
        getattr(cause, "status_code", None),
        getattr(getattr(cause, "response", None), "status_code", None),
        getattr(cause, "code", None),
    ):
        if isinstance(candidate, int):
            return candidate
    return None


def _contains_any(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)
