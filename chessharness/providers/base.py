"""
Abstract LLM provider interface.

All concrete providers (OpenAI, Anthropic, Google, …) implement LLMProvider.
The Message NamedTuple is the canonical way to pass conversation turns,
including optional image bytes for vision-capable providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, NamedTuple


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

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 5120,
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


class ProviderError(Exception):
    """Raised when a provider API call fails unrecoverably."""

    def __init__(self, provider: str, message: str, cause: Exception | None = None) -> None:
        self.provider = provider
        self.cause = cause
        super().__init__(f"[{provider}] {message}")
