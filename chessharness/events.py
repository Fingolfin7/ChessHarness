"""
Typed event dataclasses — the shared language between the game loop and any consumer.

The game loop (game.py) yields these. The CLI, web UI, or test harness consumes them.
All events are frozen (immutable) so they're safe to pass across async boundaries
and can be trivially serialized to JSON via dataclasses.asdict().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Color = Literal["white", "black"]
GameResult = Literal["1-0", "0-1", "1/2-1/2", "*"]
GameOverReason = Literal[
    "checkmate",
    "stalemate",
    "resignation",
    "draw",
    "fifty_move",
    "insufficient_material",
    "max_retries_exceeded",
    "interrupted",
]


@dataclass(frozen=True)
class GameStartEvent:
    white_name: str
    black_name: str
    starting_fen: str = "start"
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class TurnStartEvent:
    color: Color
    player_name: str
    move_number: int
    fen: str
    board_ascii: str
    legal_moves_san: list[str]
    move_history_san: list[str]


@dataclass(frozen=True)
class MoveRequestedEvent:
    color: Color
    attempt_num: int


@dataclass(frozen=True)
class InvalidMoveEvent:
    color: Color
    attempted_move: str  # extracted move string that failed validation
    raw_response: str    # unmodified model output
    reasoning: str       # parsed ## Reasoning section (empty string if absent)
    error: str
    attempt_num: int


@dataclass(frozen=True)
class MoveAppliedEvent:
    color: Color
    move_uci: str
    move_san: str
    raw_response: str    # unmodified model output
    reasoning: str       # parsed ## Reasoning section (empty string if absent)
    fen_after: str
    board_ascii_after: str
    is_check: bool
    move_number: int


@dataclass(frozen=True)
class ReasoningChunkEvent:
    color: Color
    chunk: str   # raw token(s) from the model stream


@dataclass(frozen=True)
class CheckEvent:
    color_in_check: Color
    checking_move_san: str


@dataclass(frozen=True)
class GameOverEvent:
    result: GameResult
    reason: GameOverReason
    winner_name: str | None
    pgn: str
    total_moves: int
    timestamp: datetime = field(default_factory=datetime.now)


# Union type for type-safe pattern matching in consumers
GameEvent = (
    GameStartEvent
    | TurnStartEvent
    | MoveRequestedEvent
    | InvalidMoveEvent
    | ReasoningChunkEvent
    | MoveAppliedEvent
    | CheckEvent
    | GameOverEvent
)
