"""Arena tournament — stub (Phase 4)."""

from __future__ import annotations

from typing import AsyncIterator

from chessharness.config import Config, GameConfig
from chessharness.tournaments.base import (
    PlayerFactory,
    StandingEntry,
    Tournament,
    TournamentParticipant,
)
from chessharness.tournaments.events import TournamentEvent


class ArenaTournament(Tournament):
    """Time-limited, immediate re-pairing after each game. Not yet implemented."""

    def run(
        self,
        participants: list[TournamentParticipant],
        config: Config,
        player_factory: PlayerFactory,
    ) -> AsyncIterator[TournamentEvent]:
        raise NotImplementedError("Arena tournament is not yet implemented.")

    def standings(self) -> list[StandingEntry]:
        raise NotImplementedError
