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

import re
from typing import TYPE_CHECKING

from chessharness.players.base import Player, GameState, MoveResponse
from chessharness.providers.base import LLMProvider, Message

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
- One move only, on its own line, in UCI format: <from_square><to_square>[promotion]
- It MUST be from the legal moves list you are given
- Castling: e1g1 (white kingside), e1c1 (white queenside), e8g8, e8c8
- Promotion: append the piece letter, e.g. a7a8q (queen), a7a8r (rook)"""

_USER = """\
Position (FEN): {fen}

Board (you are {color_upper}, uppercase = White, lowercase = Black):
{board_ascii}

Move history ({move_count} half-moves):
{move_history}

Legal moves ({legal_count}):
{legal_moves_san}
{retry_block}"""

_RETRY_BLOCK = """\

## Correction
Your previous move "{prev_move}" was rejected.
Reason: {prev_error}
You must choose a different move from the legal moves list above.
"""

# Regex to find a UCI move token anywhere in text (fallback)
_UCI_RE = re.compile(r"\b([a-h][1-8][a-h][1-8][qrbnQRBN]?)\b")


class LLMPlayer(Player):
    """Chess player powered by an LLMProvider."""

    def __init__(
        self,
        name: str,
        provider: LLMProvider,
        logger: ConversationLogger | None = None,
    ) -> None:
        super().__init__(name)
        self._provider = provider
        self._logger = logger

    async def get_move(self, state: GameState) -> MoveResponse:
        messages = self._build_messages(state)

        if self._logger:
            self._logger.log_request(
                player_name=self.name,
                color=state.color,
                move_number=state.move_number,
                attempt=state.attempt_num,
                messages=messages,
            )

        raw = await self._provider.complete(messages)

        if self._logger:
            self._logger.log_response(player_name=self.name, raw=raw)

        reasoning, move = _parse_response(raw)
        return MoveResponse(raw=raw, move=move, reasoning=reasoning)

    # ------------------------------------------------------------------ #
    # Prompt construction                                                  #
    # ------------------------------------------------------------------ #

    def _build_messages(self, state: GameState) -> list[Message]:
        system_text = _SYSTEM.format(
            color=state.color,
            color_upper=state.color.upper(),
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

        user_text = _USER.format(
            fen=state.fen,
            color_upper=state.color.upper(),
            board_ascii=state.board_ascii,
            move_count=len(state.move_history_san),
            move_history=history_str,
            legal_count=len(state.legal_moves_san),
            legal_moves_san=", ".join(state.legal_moves_san),
            retry_block=retry_block,
        )

        image_bytes = (
            state.board_image_bytes
            if self._provider.supports_vision
            else None
        )

        return [
            Message(role="system", content=system_text),
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

    uci = _extract_uci(move_text if move_text else raw)
    return reasoning, uci


def _extract_uci(text: str) -> str:
    """
    Extract a UCI move token from arbitrary text.

    Strips common prefixes, applies UCI regex, falls back to first token.
    """
    cleaned = text.strip().lower()

    for prefix in ("my move:", "move:", "i play", "i choose", "best move:", "**", "*"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()

    match = _UCI_RE.search(cleaned)
    if match:
        return match.group(1).lower()

    tokens = cleaned.split()
    return tokens[0] if tokens else cleaned
