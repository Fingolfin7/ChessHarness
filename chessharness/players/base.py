"""
Abstract Player interface and the GameState snapshot passed to each player per turn.

GameState contains everything a player needs to make a decision — whether that's
an LLM API call, stdin input, a chess engine query, or a remote API call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from chessharness.events import Color


@dataclass
class MoveResponse:
    """Returned by Player.get_move(). Carries the raw output, parsed reasoning, and extracted move."""
    raw: str        # unmodified text from the model / human / engine
    move: str       # extracted UCI string (may still be invalid — game.py validates)
    reasoning: str = ""  # content from the ## Reasoning section (empty for human/engine)


@dataclass
class GameState:
    """Immutable snapshot of the game at the start of a player's turn."""

    fen: str
    board_ascii: str
    legal_moves_uci: list[str]
    legal_moves_san: list[str]
    move_history_san: list[str]
    color: Color
    move_number: int

    # Set when board_input == "image" and cairosvg is available
    board_image_bytes: bytes | None = None

    # Populated on retry attempts (attempt_num > 1)
    previous_invalid_move: str | None = None
    previous_error: str | None = None
    attempt_num: int = 1


class Player(ABC):
    """Abstract base class for all chess players."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def get_move(self, state: GameState) -> MoveResponse:
        """
        Given the current game state, return a MoveResponse.

        MoveResponse.raw  — the unmodified output (full model text, stdin line, etc.)
        MoveResponse.move — the extracted UCI string to validate

        The game loop validates move — players may return invalid moves;
        the loop will call get_move() again with retry context.

        Must be async. Implementations may await LLM API calls, stdin reads,
        engine queries, etc.
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
