"""Round Robin tournament — stub (Phase 2)."""

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


class RoundRobinTournament(Tournament):
    """Every participant plays every other participant. Not yet implemented."""

    def run(
        self,
        participants: list[TournamentParticipant],
        config: Config,
        player_factory: PlayerFactory,
    ) -> AsyncIterator[TournamentEvent]:
        raise NotImplementedError("Round Robin tournament is not yet implemented.")

    def standings(self) -> list[StandingEntry]:
        raise NotImplementedError
