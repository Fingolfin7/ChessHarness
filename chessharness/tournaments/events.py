"""
Tournament event dataclasses — the shared language between the tournament
loop and any consumer (CLI, WebSocket broadcaster, tests).

Follows the same frozen-dataclass pattern as chessharness/events.py.
All events are immutable and safe to pass across async boundaries.
dataclasses.asdict() serialises them to JSON-compatible dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from chessharness.events import GameEvent
from chessharness.tournaments.base import MatchResult, StandingEntry, TournamentParticipant

TournamentType = Literal["knockout", "round_robin", "swiss", "arena"]


@dataclass(frozen=True)
class TournamentStartEvent:
    """Fired once before round 1."""

    tournament_type: TournamentType
    participant_names: list[str]        # display names, in seed order
    total_rounds: int                   # known for knockout/round-robin; 0 if unknown
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class RoundStartEvent:
    """Fired at the start of each round, with the full pairing list."""

    round_num: int
    total_rounds: int
    # Each pairing: (match_id, white_display_name, black_display_name)
    # "BYE" as black_display_name means the white participant advances automatically.
    pairings: list[tuple[str, str, str]]


@dataclass(frozen=True)
class MatchStartEvent:
    """Fired immediately before a game begins (including rematch games)."""

    match_id: str
    white_name: str
    black_name: str
    round_num: int
    game_num: int = 1   # 1 for the first game, 2+ for rematches


@dataclass(frozen=True)
class MatchGameEvent:
    """
    Wraps a GameEvent so tournament consumers can identify which match it
    belongs to.  The CLI display delegates these to display_event(); the
    WebSocket broadcaster fans them out to per-match game subscribers.
    """

    match_id: str
    game_event: GameEvent


@dataclass(frozen=True)
class MatchCompleteEvent:
    """Fired after a match is fully decided (after rematches if needed)."""

    match_id: str
    result: MatchResult
    advancing_name: str   # display name of the participant who advances
    round_num: int


@dataclass(frozen=True)
class RoundCompleteEvent:
    """Fired after all matches in a round are decided."""

    round_num: int
    results: list[MatchResult]
    standings: list[StandingEntry]


@dataclass(frozen=True)
class TournamentCompleteEvent:
    """Fired once the tournament is over."""

    winner_name: str
    final_standings: list[StandingEntry]
    all_results: list[MatchResult]
    timestamp: datetime = field(default_factory=datetime.now)


# Union type for type-safe pattern matching in consumers
TournamentEvent = (
    TournamentStartEvent
    | RoundStartEvent
    | MatchStartEvent
    | MatchGameEvent
    | MatchCompleteEvent
    | RoundCompleteEvent
    | TournamentCompleteEvent
)
