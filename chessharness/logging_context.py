"""Task-local correlation context for application log records.

Async games run concurrently, so a timestamp alone is not enough to connect a
provider failure to the game and tournament that caused it.  Context variables
keep these identifiers isolated per asyncio task without changing every log
call site.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
import logging
from collections.abc import Iterator


_LOG_CONTEXT: ContextVar[dict[str, str]] = ContextVar(
    "chessharness_log_context",
    default={},
)
_FIELDS = ("game_id", "tournament_id", "match_id", "batch_id")


@contextmanager
def logging_context(**values: object) -> Iterator[None]:
    """Temporarily add non-empty correlation values to current-task logs."""

    current = dict(_LOG_CONTEXT.get())
    for key, value in values.items():
        if key in _FIELDS and value is not None and str(value):
            current[key] = str(value)
    token = _LOG_CONTEXT.set(current)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


def bind_logging_context(**values: object) -> Token[dict[str, str]]:
    """Set context explicitly and return a token for callers with long scopes."""

    current = dict(_LOG_CONTEXT.get())
    for key, value in values.items():
        if key in _FIELDS and value is not None and str(value):
            current[key] = str(value)
    return _LOG_CONTEXT.set(current)


def reset_logging_context(token: Token[dict[str, str]]) -> None:
    """Restore the context returned by :func:`bind_logging_context`."""

    _LOG_CONTEXT.reset(token)


class CorrelationFilter(logging.Filter):
    """Inject stable correlation attributes expected by the app formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        values = _LOG_CONTEXT.get()
        for field in _FIELDS:
            setattr(record, field, values.get(field, "-"))
        return True

