"""Pure, simultaneous Glicko-2 batch calculations."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable, Literal

from chessharness.ratings.glicko2 import (
    DEFAULT_TAU,
    Glicko2Rating,
    Glicko2Result,
    update_rating,
)


GameScore = Literal["1-0", "0-1", "1/2-1/2"]


@dataclass(frozen=True, slots=True)
class CompetitorRating:
    """A competitor's complete state at the start of a rating batch."""

    competitor_id: str
    rating: Glicko2Rating = field(default_factory=Glicko2Rating)
    games_played: int = 0
    is_anchor: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.competitor_id, str):
            raise TypeError("competitor_id must be a string")
        if not self.competitor_id.strip():
            raise ValueError("competitor_id must not be empty")
        if not isinstance(self.rating, Glicko2Rating):
            raise TypeError("rating must be a Glicko2Rating")
        if not isinstance(self.games_played, int) or isinstance(self.games_played, bool):
            raise TypeError("games_played must be an integer")
        if self.games_played < 0:
            raise ValueError("games_played must not be negative")
        if not isinstance(self.is_anchor, bool):
            raise TypeError("is_anchor must be a boolean")


@dataclass(frozen=True, slots=True)
class RatedGame:
    """A completed, eligible chess result included in a rating batch."""

    white_id: str
    black_id: str
    result: GameScore

    def __post_init__(self) -> None:
        if not isinstance(self.white_id, str) or not self.white_id.strip():
            raise ValueError("white_id must be a non-empty string")
        if not isinstance(self.black_id, str) or not self.black_id.strip():
            raise ValueError("black_id must be a non-empty string")
        if self.white_id == self.black_id:
            raise ValueError("white_id and black_id must be distinct")
        if self.result not in ("1-0", "0-1", "1/2-1/2"):
            raise ValueError("result must be '1-0', '0-1', or '1/2-1/2'")


@dataclass(frozen=True, slots=True)
class RatingBatchChange:
    """One non-anchor competitor's state transition and result increments."""

    before: CompetitorRating
    after: CompetitorRating
    wins: int
    draws: int
    losses: int
    games: int


def _scores(result: GameScore) -> tuple[float, float]:
    if result == "1-0":
        return 1.0, 0.0
    if result == "0-1":
        return 0.0, 1.0
    return 0.5, 0.5


def calculate_rating_batch(
    competitors: Iterable[CompetitorRating],
    games: Iterable[RatedGame],
    *,
    tau: float = DEFAULT_TAU,
) -> dict[str, RatingBatchChange]:
    """Calculate a batch from one immutable pre-batch snapshot.

    Only participating non-anchor competitors are returned.  Every update is
    calculated against opponents' pre-batch ratings, so the result does not
    depend on game order or competitor iteration order.
    """

    if not isinstance(tau, (int, float)) or isinstance(tau, bool):
        raise TypeError("tau must be a real number")
    if not math.isfinite(tau):
        raise ValueError("tau must be finite")
    if tau <= 0:
        raise ValueError("tau must be greater than zero")

    try:
        competitor_list = tuple(competitors)
    except TypeError as exc:
        raise TypeError("competitors must be an iterable of CompetitorRating values") from exc
    if any(not isinstance(item, CompetitorRating) for item in competitor_list):
        raise TypeError("competitors must contain only CompetitorRating values")

    snapshot: dict[str, CompetitorRating] = {}
    for competitor in competitor_list:
        if competitor.competitor_id in snapshot:
            raise ValueError(f"duplicate competitor_id: {competitor.competitor_id!r}")
        snapshot[competitor.competitor_id] = competitor

    try:
        game_list = tuple(games)
    except TypeError as exc:
        raise TypeError("games must be an iterable of RatedGame values") from exc
    if any(not isinstance(game, RatedGame) for game in game_list):
        raise TypeError("games must contain only RatedGame values")

    # Validate the entire batch before beginning any calculations.
    for game in game_list:
        if game.white_id not in snapshot:
            raise ValueError(f"unknown white competitor_id: {game.white_id!r}")
        if game.black_id not in snapshot:
            raise ValueError(f"unknown black competitor_id: {game.black_id!r}")
        if game.white_id == game.black_id:
            # RatedGame normally rejects this itself, but retaining the check
            # here makes this function's invariant explicit.
            raise ValueError("a competitor cannot play itself")

    opponent_results: dict[str, list[Glicko2Result]] = {}
    records: dict[str, list[int]] = {}
    for game in game_list:
        white = snapshot[game.white_id]
        black = snapshot[game.black_id]
        white_score, black_score = _scores(game.result)

        if not white.is_anchor:
            opponent_results.setdefault(white.competitor_id, []).append(
                Glicko2Result(
                    opponent_rating=black.rating.rating,
                    opponent_deviation=black.rating.deviation,
                    score=white_score,
                )
            )
            record = records.setdefault(white.competitor_id, [0, 0, 0])
            record[0 if white_score == 1 else 1 if white_score == 0.5 else 2] += 1

        if not black.is_anchor:
            opponent_results.setdefault(black.competitor_id, []).append(
                Glicko2Result(
                    opponent_rating=white.rating.rating,
                    opponent_deviation=white.rating.deviation,
                    score=black_score,
                )
            )
            record = records.setdefault(black.competitor_id, [0, 0, 0])
            record[0 if black_score == 1 else 1 if black_score == 0.5 else 2] += 1

    changes: dict[str, RatingBatchChange] = {}
    for competitor_id, results in opponent_results.items():
        before = snapshot[competitor_id]
        wins, draws, losses = records[competitor_id]
        games_in_batch = wins + draws + losses
        after = CompetitorRating(
            competitor_id=competitor_id,
            rating=update_rating(before.rating, results, tau=tau),
            games_played=before.games_played + games_in_batch,
            is_anchor=False,
        )
        changes[competitor_id] = RatingBatchChange(
            before=before,
            after=after,
            wins=wins,
            draws=draws,
            losses=losses,
            games=games_in_batch,
        )

    return changes
