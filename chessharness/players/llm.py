"""
LLMPlayer - a chess player backed by any LLMProvider.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import logging
import re
from typing import TYPE_CHECKING

from chessharness.players.base import GameState, MoveResponse, Player
from chessharness.providers.base import LLMProvider, Message, ProviderError

if TYPE_CHECKING:
    from chessharness.conv_logger import ConversationLogger

_SYSTEM = """\
You are playing chess as {color} ({color_upper} pieces) in a standard game.

Respond in this exact format - two sections, nothing else:

## Reasoning
Think through the position: threats, tactics, your plan. Be concise.

## Move
Your chosen move in SAN notation (e.g. e4, Nf3, cxd4, O-O, O-O-O, e8=Q)

Rules for the ## Move section:
- One move only, on its own line
{legal_moves_rule}- Use SAN notation (e.g. e4, Nf3, cxd4, O-O, O-O-O, e8=Q)"""

_USER_TEXT = """\
Position (FEN): {fen}

Board (you are {color_upper}, uppercase = White, lowercase = Black):
{board_ascii}

Move history ({move_count} half-moves):
{move_history}
{legal_moves_block}{retry_block}"""

_USER_IMAGE = """\
Board image is attached for the current position.
You are playing as {color_upper}.

Move history ({move_count} half-moves):
{move_history}
{legal_moves_block}{retry_block}"""

_RETRY_BLOCK = """\

## Correction
Your previous move "{prev_move}" was rejected.
Reason: {prev_error}
You must choose a different, valid move.
"""

_UCI_RE = re.compile(r"\b([a-h][1-8][a-h][1-8][qrbnQRBN]?)\b")
_SAN_RE = re.compile(r"\b([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O-O|O-O)\b")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParseDiagnostics:
    raw_length: int
    raw_tail: str
    move_section_found: bool
    move_section_length: int
    move_section_tail: str
    fallback_used: bool
    parsed_move: str


@dataclass(frozen=True)
class ParsedResponse:
    reasoning: str
    move: str
    diagnostics: ParseDiagnostics


@dataclass(frozen=True)
class ProviderCallResult:
    raw: str
    chunk_count: int
    provider_metadata: dict[str, object]


class LLMPlayer(Player):
    """Chess player powered by an LLMProvider."""

    def __init__(
        self,
        name: str,
        provider: LLMProvider,
        logger: ConversationLogger | None = None,
        show_legal_moves: bool = True,
        move_timeout: int = 120,
        max_output_tokens: int = 5120,
        reasoning_effort: str | None = None,
    ) -> None:
        super().__init__(name, player_type="llm")
        self._provider = provider
        self._logger = logger
        self._show_legal_moves = show_legal_moves
        self._move_timeout = move_timeout
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort
        self._history: list[Message] = []

    async def get_move(
        self,
        state: GameState,
        chunk_queue: asyncio.Queue | None = None,
    ) -> MoveResponse:
        messages = self._build_messages(state)

        if self._logger:
            self._logger.log_request(
                color=state.color,
                move_number=state.move_number,
                attempt=state.attempt_num,
                messages=messages,
            )

        try:
            provider_result = await self._call_provider(messages, chunk_queue)
        except ProviderError:
            if any(m.image_bytes for m in messages):
                logger.warning(
                    "Vision call failed for %s - retrying with text board representation.",
                    self.name,
                )
                messages = self._build_messages(state, force_text=True)
                provider_result = await self._call_provider(messages, chunk_queue)
            else:
                raise

        if self._logger:
            self._logger.log_response(raw=provider_result.raw)
            self._logger.log_response_diagnostics(
                title="STREAM DIAGNOSTICS",
                values={
                    "status": "complete",
                    "chunk_count": provider_result.chunk_count,
                    "raw_length": len(provider_result.raw),
                    "raw_tail": _tail(provider_result.raw),
                    "provider_metadata": provider_result.provider_metadata,
                },
            )

        turn_label = (
            f"[Move {state.move_number} - {state.color.upper()}"
            + (f" | Attempt {state.attempt_num}" if state.attempt_num > 1 else "")
            + "]"
        )
        self._history.append(Message(role="user", content=turn_label))
        self._history.append(Message(role="assistant", content=provider_result.raw))

        parsed = _parse_response(provider_result.raw)
        logger.info(
            "Parsed model response [player=%s move=%s color=%s attempt=%s move_section_found=%s fallback_used=%s parsed_move=%r raw_length=%s provider_metadata=%s]",
            self.name,
            state.move_number,
            state.color,
            state.attempt_num,
            parsed.diagnostics.move_section_found,
            parsed.diagnostics.fallback_used,
            parsed.move,
            parsed.diagnostics.raw_length,
            provider_result.provider_metadata,
        )
        if self._logger:
            self._logger.log_response_diagnostics(
                title="PARSE DIAGNOSTICS",
                values=asdict(parsed.diagnostics),
            )

        return MoveResponse(
            raw=provider_result.raw,
            move=parsed.move,
            reasoning=parsed.reasoning,
            provider_metadata=provider_result.provider_metadata,
        )

    async def _call_provider(
        self,
        messages: list[Message],
        chunk_queue: asyncio.Queue | None,
    ) -> ProviderCallResult:
        raw = ""
        chunk_count = 0
        try:
            async with asyncio.timeout(self._move_timeout):
                async for chunk in self._provider.stream(
                    messages,
                    max_tokens=self._max_output_tokens,
                    reasoning_effort=self._reasoning_effort,
                ):
                    raw += chunk
                    chunk_count += 1
                    if chunk_queue is not None:
                        await chunk_queue.put(chunk)
        except TimeoutError as exc:
            self._log_partial_stream(
                raw=raw,
                chunk_count=chunk_count,
                error=f"timeout after {self._move_timeout}s",
            )
            raise ProviderError(
                self._provider.__class__.__name__,
                f"No response within {self._move_timeout}s timeout.",
                cause=exc,
            ) from exc
        except ProviderError:
            self._log_partial_stream(
                raw=raw,
                chunk_count=chunk_count,
                error="provider stream error",
            )
            raise
        except Exception as exc:
            self._log_partial_stream(
                raw=raw,
                chunk_count=chunk_count,
                error=str(exc),
            )
            raise ProviderError(
                self._provider.__class__.__name__,
                str(exc),
                cause=exc,
            ) from exc

        provider_metadata = _provider_metadata(self._provider)
        logger.info(
            "Model stream completed [player=%s provider=%s chunks=%s raw_length=%s provider_metadata=%s]",
            self.name,
            self._provider.__class__.__name__,
            chunk_count,
            len(raw),
            provider_metadata,
        )
        return ProviderCallResult(
            raw=raw,
            chunk_count=chunk_count,
            provider_metadata=provider_metadata,
        )

    def _log_partial_stream(self, *, raw: str, chunk_count: int, error: str) -> None:
        provider_metadata = _provider_metadata(self._provider)
        logger.warning(
            "Model stream aborted [player=%s provider=%s chunks=%s raw_length=%s error=%s provider_metadata=%s]",
            self.name,
            self._provider.__class__.__name__,
            chunk_count,
            len(raw),
            error,
            provider_metadata,
        )
        if self._logger:
            self._logger.log_response(raw=raw)
            self._logger.log_response_diagnostics(
                title="STREAM DIAGNOSTICS",
                values={
                    "status": "aborted",
                    "error": error,
                    "chunk_count": chunk_count,
                    "raw_length": len(raw),
                    "raw_tail": _tail(raw),
                    "provider_metadata": provider_metadata,
                },
            )

    def _build_messages(self, state: GameState, force_text: bool = False) -> list[Message]:
        legal_moves_rule = (
            "- It MUST be from the legal moves list you are given\n"
            if self._show_legal_moves
            else ""
        )
        system_text = _SYSTEM.format(
            color=state.color,
            color_upper=state.color.upper(),
            legal_moves_rule=legal_moves_rule,
        )

        retry_block = ""
        if state.attempt_num > 1 and state.previous_invalid_move:
            retry_block = _RETRY_BLOCK.format(
                prev_move=state.previous_invalid_move,
                prev_error=state.previous_error or "unknown error",
            )

        history_str = " ".join(state.move_history_san) if state.move_history_san else "(game just started)"
        legal_moves_block = (
            f"Legal moves ({len(state.legal_moves_san)}):\n"
            f"{', '.join(state.legal_moves_san)}\n"
            if self._show_legal_moves
            else ""
        )

        provider_supports_vision = self._provider.supports_vision
        image_bytes = state.board_image_bytes if provider_supports_vision and not force_text else None
        if state.board_image_bytes and not provider_supports_vision:
            logger.debug(
                "Board image rendered but not attached because provider does not advertise vision support [player=%s provider=%s]",
                self.name,
                self._provider.__class__.__name__,
            )

        if image_bytes:
            user_text = _USER_IMAGE.format(
                color_upper=state.color.upper(),
                move_count=len(state.move_history_san),
                move_history=history_str,
                legal_moves_block=legal_moves_block,
                retry_block=retry_block,
            )
        else:
            user_text = _USER_TEXT.format(
                fen=state.fen,
                color_upper=state.color.upper(),
                board_ascii=state.board_ascii,
                move_count=len(state.move_history_san),
                move_history=history_str,
                legal_moves_block=legal_moves_block,
                retry_block=retry_block,
            )

        return [
            Message(role="system", content=system_text),
            *self._history,
            Message(role="user", content=user_text, image_bytes=image_bytes),
        ]


def _parse_response(raw: str) -> ParsedResponse:
    """Parse a structured response, preferring the explicit ## Move section."""

    reasoning = ""
    move_text = ""
    move_section_found = False

    parts = re.split(r"(?m)^#{1,3}\s*", raw)
    for part in parts:
        if not part.strip():
            continue
        lines = part.splitlines()
        header = lines[0].strip().lower()
        content = "\n".join(lines[1:]).strip()

        if any(keyword in header for keyword in ("reasoning", "thinking", "analysis", "thought")):
            reasoning = content
        elif "move" in header and "correction" not in header:
            move_section_found = True
            move_text = content

    if move_text:
        move = _extract_move(move_text)
        fallback_used = False
    else:
        fallback_text = _extract_bare_move_reply(raw)
        move = _extract_move(fallback_text) if fallback_text else ""
        fallback_used = bool(fallback_text)

    return ParsedResponse(
        reasoning=reasoning,
        move=move,
        diagnostics=ParseDiagnostics(
            raw_length=len(raw),
            raw_tail=_tail(raw),
            move_section_found=move_section_found,
            move_section_length=len(move_text),
            move_section_tail=_tail(move_text),
            fallback_used=fallback_used,
            parsed_move=move,
        ),
    )


def _extract_move(text: str) -> str:
    """Extract a move token (UCI or SAN) from a short move-only snippet."""

    cleaned = text.strip()
    lower = cleaned.lower()
    for prefix in ("my move:", "move:", "i play", "i choose", "best move:", "**", "*"):
        if lower.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            lower = cleaned.lower()

    uci_match = _UCI_RE.search(cleaned)
    if uci_match:
        return uci_match.group(1).lower()

    san_match = _SAN_RE.search(cleaned)
    if san_match:
        return san_match.group(1)

    tokens = cleaned.split()
    return tokens[0] if tokens else cleaned


def _extract_bare_move_reply(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned:
        return ""

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if len(lines) == 1 and len(lines[0]) <= 32:
        return lines[0]
    if (
        len(lines) == 2
        and lines[0].rstrip(":").lower() in {"move", "best move", "my move"}
        and len(lines[1]) <= 32
    ):
        return lines[1]
    return ""


def _provider_metadata(provider: object) -> dict[str, object]:
    metadata = getattr(provider, "last_response_metadata", None)
    return dict(metadata) if isinstance(metadata, dict) else {}


def _tail(text: str, limit: int = 200) -> str:
    return text[-limit:] if len(text) > limit else text
