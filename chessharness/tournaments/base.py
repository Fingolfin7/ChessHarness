"""
Tournament abstractions — shared types and the Tournament base class.

All tournament formats (Knockout, Round Robin, Swiss, Arena) inherit from
Tournament and implement the same async-generator run() interface so that
any consumer (CLI display, WebSocket broadcaster, test harness) works
identically regardless of format.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncIterator, Callable, Literal

from chessharness.config import Config, GameConfig, ModelEntry
from chessharness.events import GameResult
from chessharness.players.base import Player

if TYPE_CHECKING:
    from chessharness.tournaments.events import TournamentEvent


DrawHandling = Literal["rematch", "coin_flip", "seed"]


@dataclass
class TournamentParticipant:
    """A single entrant in a tournament — one (provider, model, seed) tuple."""

    provider_name: str
    model: ModelEntry
    seed: int  # 1-based; seed 1 = top seed (first picked)

    @property
    def display_name(self) -> str:
        return self.model.name

    # Manual hash/eq so this can be used as a dict key even though ModelEntry
    # is a mutable dataclass (not hashable by default).
    def __hash__(self) -> int:
        return hash((self.provider_name, self.model.id, self.seed))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TournamentParticipant):
            return NotImplemented
        return (
            self.provider_name == other.provider_name
            and self.model.id == other.model.id
            and self.seed == other.seed
        )

    def __repr__(self) -> str:
        return f"TournamentParticipant({self.display_name!r}, seed={self.seed})"


@dataclass(frozen=True)
class MatchResult:
    """The outcome of a single game that constitutes a tournament match."""

    match_id: str                          # e.g. "R1-M1", "SF-1", "F"
    white: TournamentParticipant
    black: TournamentParticipant
    game_result: GameResult                # "1-0" | "0-1" | "1/2-1/2" | "*"
    pgn: str
    total_moves: int
    winner: TournamentParticipant | None   # None = draw (rematch may follow)

    def __hash__(self) -> int:
        return hash((self.match_id, self.game_result))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MatchResult):
            return NotImplemented
        return self.match_id == other.match_id and self.game_result == other.game_result


@dataclass
class StandingEntry:
    """Running tally for one participant across all completed matches."""

    participant: TournamentParticipant
    wins: int = 0
    losses: int = 0
    draws: int = 0

    @property
    def points(self) -> float:
        return float(self.wins) + self.draws * 0.5

    @property
    def games_played(self) -> int:
        return self.wins + self.losses + self.draws


# Type alias: a callable that creates a fresh Player for a given participant.
# The tournament calls this per-game so each game starts with clean history.
PlayerFactory = Callable[[TournamentParticipant], Player]


class Tournament(ABC):
    """
    Abstract base class for all tournament formats.

    Subclasses implement run() as an async generator that yields TournamentEvent
    objects (including MatchGameEvent wrappers around per-game GameEvents).

    The tournament never creates players directly — it calls player_factory()
    to get a fresh Player instance for each game, ensuring no conversation
    history leaks between games.
    """

    @abstractmethod
    def run(
        self,
        participants: list[TournamentParticipant],
        config: Config,
        player_factory: PlayerFactory,
    ) -> AsyncIterator[TournamentEvent]:
        """
        Run the full tournament, yielding events as play progresses.

        Yields:
            TournamentStartEvent  — once, before round 1
            RoundStartEvent       — once per round
            MatchStartEvent       — once per game (including rematches)
            MatchGameEvent        — once per GameEvent within a game
            MatchCompleteEvent    — once per decided match
            RoundCompleteEvent    — once after all matches in a round finish
            TournamentCompleteEvent — once when the tournament ends

        The generator is exhausted when the tournament is over.
        """
        ...  # pragma: no cover

    @abstractmethod
    def standings(self) -> list[StandingEntry]:
        """Return current standings, sorted by points descending."""
        ...  # pragma: no cover
