"""
Tournament package.

create_tournament() is the single entry point for instantiating any format.

To add a new format:
  1. Create chessharness/tournaments/<name>.py implementing Tournament
  2. Add a case here
"""

from __future__ import annotations

from chessharness.tournaments.base import (
    DrawHandling,
    MatchResult,
    PlayerFactory,
    StandingEntry,
    Tournament,
    TournamentParticipant,
)
from chessharness.tournaments.events import (
    MatchCompleteEvent,
    MatchGameEvent,
    MatchStartEvent,
    RoundCompleteEvent,
    RoundStartEvent,
    TournamentCompleteEvent,
    TournamentEvent,
    TournamentStartEvent,
    TournamentType,
)
from chessharness.tournaments.knockout import KnockoutTournament
from chessharness.tournaments.round_robin import RoundRobinTournament
from chessharness.tournaments.swiss import SwissTournament
from chessharness.tournaments.arena import ArenaTournament

__all__ = [
    # Base types
    "DrawHandling",
    "MatchResult",
    "PlayerFactory",
    "StandingEntry",
    "Tournament",
    "TournamentParticipant",
    # Events
    "TournamentEvent",
    "TournamentStartEvent",
    "RoundStartEvent",
    "MatchStartEvent",
    "MatchGameEvent",
    "MatchCompleteEvent",
    "RoundCompleteEvent",
    "TournamentCompleteEvent",
    "TournamentType",
    # Implementations
    "KnockoutTournament",
    "RoundRobinTournament",
    "SwissTournament",
    "ArenaTournament",
    # Factory
    "create_tournament",
]


def create_tournament(
    tournament_type: TournamentType,
    draw_handling: DrawHandling = "rematch",
) -> Tournament:
    """
    Instantiate the correct Tournament subclass.

    Args:
        tournament_type: "knockout" | "round_robin" | "swiss" | "arena"
        draw_handling:   "rematch" | "coin_flip" | "seed"  (knockout only for now)
    """
    match tournament_type:
        case "knockout":
            return KnockoutTournament(draw_handling=draw_handling)
        case "round_robin":
            return RoundRobinTournament()
        case "swiss":
            return SwissTournament()
        case "arena":
            return ArenaTournament()
        case _:
            raise ValueError(
                f"Unknown tournament type: {tournament_type!r}. "
                "Valid types: knockout, round_robin, swiss, arena"
            )
