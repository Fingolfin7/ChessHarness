"""
LLMPlayer — a chess player backed by any LLMProvider.

Prompt design:
  - Structured response format with ## Reasoning and ## Move sections.
    This gives models room to think before committing to a move, which
    dramatically reduces invalid/hallucinated responses compared to asking
    for a bare UCI token.
  - Legal moves list included every turn — most effective anti-hallucination measure.
  - Extraction parses the ## Move section first, falls back to UCI regex scan.
  - max_tokens=5120 to accommodate extended reasoning + move.
  - Temperature not set — each provider uses its own default.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from chessharness.players.base import Player, GameState, MoveResponse
from chessharness.providers.base import LLMProvider, Message, ProviderError

if TYPE_CHECKING:
    from chessharness.conv_logger import ConversationLogger

# --------------------------------------------------------------------------- #
# Prompt templates                                                             #
# --------------------------------------------------------------------------- #

_SYSTEM = """\
You are playing chess as {color} ({color_upper} pieces) in a standard game.

Respond in this exact format — two sections, nothing else:

## Reasoning
Think through the position: threats, tactics, your plan. Be concise.

## Move
Your chosen move in UCI notation (e.g. e2e4, g1f3, e1g1, a7a8q)

Rules for the ## Move section:
- One move only, on its own line
{legal_moves_rule}- Either SAN (e.g. e4, Nf3, cxd4, O-O, O-O-O, e8=Q) or UCI (e.g. e2e4, g1f3, e1g1, a7a8q) is accepted"""

_USER = """\
Position (FEN): {fen}

Board (you are {color_upper}, uppercase = White, lowercase = Black):
{board_ascii}

Move history ({move_count} half-moves):
{move_history}
{legal_moves_block}{retry_block}"""

_RETRY_BLOCK = """\

## Correction
Your previous move "{prev_move}" was rejected.
Reason: {prev_error}
You must choose a different, valid move.
"""

# Regexes used to find a move token in noisy model output
_UCI_RE = re.compile(r"\b([a-h][1-8][a-h][1-8][qrbnQRBN]?)\b")
# SAN pattern: optional piece, destination square, optional promotion/annotation
_SAN_RE = re.compile(r"\b([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O-O|O-O)\b")


class LLMPlayer(Player):
    """Chess player powered by an LLMProvider."""

    def __init__(
        self,
        name: str,
        provider: LLMProvider,
        logger: ConversationLogger | None = None,
        show_legal_moves: bool = True,
        move_timeout: int = 120,
    ) -> None:
        super().__init__(name)
        self._provider = provider
        self._logger = logger
        self._show_legal_moves = show_legal_moves
        self._move_timeout = move_timeout
        self._history: list[Message] = []  # grows as [user, assistant, user, assistant, ...]

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
            async with asyncio.timeout(self._move_timeout):
                raw = ""
                async for chunk in self._provider.stream(messages):
                    raw += chunk
                    if chunk_queue is not None:
                        await chunk_queue.put(chunk)
        except TimeoutError:
            raise ProviderError(
                self._provider.__class__.__name__,
                f"No response within {self._move_timeout}s timeout.",
            )

        if self._logger:
            self._logger.log_response(raw=raw)

        # Append this exchange to history using a compact turn label instead of
        # the full board text — the current board state is re-sent fresh each turn
        # so replaying it in every historical user slot wastes tokens with no benefit.
        turn_label = (
            f"[Move {state.move_number} — {state.color.upper()}"
            + (f" | Attempt {state.attempt_num}" if state.attempt_num > 1 else "")
            + "]"
        )
        self._history.append(Message(role="user", content=turn_label))
        self._history.append(Message(role="assistant", content=raw))

        reasoning, move = _parse_response(raw)
        return MoveResponse(raw=raw, move=move, reasoning=reasoning)

    # ------------------------------------------------------------------ #
    # Prompt construction                                                  #
    # ------------------------------------------------------------------ #

    def _build_messages(self, state: GameState) -> list[Message]:
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

        history_str = (
            " ".join(state.move_history_san)
            if state.move_history_san
            else "(game just started)"
        )

        legal_moves_block = (
            f"Legal moves ({len(state.legal_moves_san)}):\n"
            f"{', '.join(state.legal_moves_san)}\n"
            if self._show_legal_moves
            else ""
        )

        user_text = _USER.format(
            fen=state.fen,
            color_upper=state.color.upper(),
            board_ascii=state.board_ascii,
            move_count=len(state.move_history_san),
            move_history=history_str,
            legal_moves_block=legal_moves_block,
            retry_block=retry_block,
        )

        image_bytes = (
            state.board_image_bytes
            if self._provider.supports_vision
            else None
        )

        return [
            Message(role="system", content=system_text),
            *self._history,
            Message(role="user", content=user_text, image_bytes=image_bytes),
        ]


# --------------------------------------------------------------------------- #
# Response parsing                                                             #
# --------------------------------------------------------------------------- #

def _parse_response(raw: str) -> tuple[str, str]:
    """
    Parse a structured ## Reasoning / ## Move response.

    Returns (reasoning_text, uci_move).

    Strategy:
      1. Split on markdown headers (## ...).
      2. Find the Reasoning and Move sections by header name.
      3. Extract UCI from the Move section content.
      4. Fall back to a full-text UCI regex scan if no ## Move found.
    """
    reasoning = ""
    move_text = ""

    # Split into (header, content) pairs
    # re.split on lines starting with one or more # captures section boundaries
    parts = re.split(r"(?m)^#{1,3}\s*", raw)
    for part in parts:
        if not part.strip():
            continue
        lines = part.splitlines()
        header = lines[0].strip().lower()
        content = "\n".join(lines[1:]).strip()

        if any(kw in header for kw in ("reasoning", "thinking", "analysis", "thought")):
            reasoning = content
        elif "move" in header and "correction" not in header:
            move_text = content

    move = _extract_move(move_text if move_text else raw)
    return reasoning, move


def _extract_move(text: str) -> str:
    """
    Extract a move token (UCI or SAN) from arbitrary model output.

    Strategy:
      1. Strip whitespace and common prefixes models add despite instructions.
      2. Try UCI regex (unambiguous: source+dest squares).
      3. Try SAN regex (piece moves, captures, castling).
      4. Fall back to the first whitespace-delimited token.
    """
    cleaned = text.strip()

    # Strip common prefixes — check case-insensitively but preserve original case
    # for SAN (Nf3 ≠ nf3)
    lower = cleaned.lower()
    for prefix in ("my move:", "move:", "i play", "i choose", "best move:", "**", "*"):
        if lower.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            lower = cleaned.lower()

    # UCI is case-insensitive and unambiguous — lower it for consistency
    uci_match = _UCI_RE.search(cleaned)
    if uci_match:
        return uci_match.group(1).lower()

    # SAN must preserve case (N, Q etc. are piece indicators)
    san_match = _SAN_RE.search(cleaned)
    if san_match:
        return san_match.group(1)

    tokens = cleaned.split()
    return tokens[0] if tokens else cleaned
