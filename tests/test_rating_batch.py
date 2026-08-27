"""Tests for pure simultaneous Glicko-2 rating batches."""

from __future__ import annotations

import pytest

from chessharness.ratings.batch import (
    CompetitorRating,
    RatedGame,
    calculate_rating_batch,
)
from chessharness.ratings.glicko2 import Glicko2Rating


def competitor(
    competitor_id: str,
    *,
    rating: float = 1500,
    deviation: float = 200,
    games_played: int = 0,
    is_anchor: bool = False,
) -> CompetitorRating:
    return CompetitorRating(
        competitor_id=competitor_id,
        rating=Glicko2Rating(rating, deviation, 0.06),
        games_played=games_played,
        is_anchor=is_anchor,
    )


def test_equal_competitors_update_symmetrically_from_same_snapshot() -> None:
    changes = calculate_rating_batch(
        [competitor("white"), competitor("black")],
        [RatedGame("white", "black", "1-0")],
    )

    white = changes["white"]
    black = changes["black"]
    assert white.after.rating.rating - 1500 == pytest.approx(
        1500 - black.after.rating.rating
    )
    assert white.after.rating.deviation == pytest.approx(black.after.rating.deviation)
    assert (white.wins, white.draws, white.losses, white.games) == (1, 0, 0, 1)
    assert (black.wins, black.draws, black.losses, black.games) == (0, 0, 1, 1)


def test_anchor_is_immutable_but_contributes_rating_and_deviation() -> None:
    model = competitor("model", games_played=7)
    anchor = competitor("stockfish-1600", rating=1600, deviation=60, is_anchor=True)

    changes = calculate_rating_batch(
        [model, anchor],
        [RatedGame("model", "stockfish-1600", "1/2-1/2")],
    )

    assert set(changes) == {"model"}
    assert changes["model"].after.rating.rating > model.rating.rating
    assert changes["model"].after.games_played == 8
    assert anchor.rating == Glicko2Rating(1600, 60, 0.06)


def test_draw_between_equal_competitors_reduces_both_deviations() -> None:
    players = [competitor("a"), competitor("b")]

    changes = calculate_rating_batch(players, [RatedGame("a", "b", "1/2-1/2")])

    for competitor_id in ("a", "b"):
        change = changes[competitor_id]
        assert change.after.rating.rating == pytest.approx(1500)
        assert change.after.rating.deviation < change.before.rating.deviation
        assert (change.wins, change.draws, change.losses, change.games) == (0, 1, 0, 1)


def test_multiple_games_are_one_period_and_accumulate_records() -> None:
    players = [competitor("a", games_played=10), competitor("b"), competitor("c")]
    games = [
        RatedGame("a", "b", "1-0"),
        RatedGame("c", "a", "1/2-1/2"),
        RatedGame("a", "b", "0-1"),
    ]

    changes = calculate_rating_batch(players, games)

    assert (changes["a"].wins, changes["a"].draws, changes["a"].losses) == (1, 1, 1)
    assert changes["a"].games == 3
    assert changes["a"].after.games_played == 13
    assert changes["b"].games == 2
    assert changes["c"].games == 1


def test_game_and_competitor_order_do_not_affect_simultaneous_updates() -> None:
    players = [competitor("a", rating=1450), competitor("b"), competitor("c", rating=1625)]
    games = [
        RatedGame("a", "b", "1-0"),
        RatedGame("b", "c", "1/2-1/2"),
        RatedGame("c", "a", "0-1"),
    ]

    forward = calculate_rating_batch(players, games)
    reverse = calculate_rating_batch(reversed(players), reversed(games))

    assert forward == reverse


def test_nonparticipant_is_not_returned_or_changed() -> None:
    idle = competitor("idle", rating=1700, games_played=30)

    changes = calculate_rating_batch(
        [competitor("a"), competitor("b"), idle],
        [RatedGame("a", "b", "1-0")],
    )

    assert "idle" not in changes
    assert idle == competitor("idle", rating=1700, games_played=30)


def test_anchor_only_games_produce_no_changes() -> None:
    changes = calculate_rating_batch(
        [competitor("a1", is_anchor=True), competitor("a2", is_anchor=True)],
        [RatedGame("a1", "a2", "1-0")],
    )

    assert changes == {}


@pytest.mark.parametrize("result", ["1", "draw", "*", ""])
def test_rated_game_rejects_invalid_results(result: str) -> None:
    with pytest.raises(ValueError, match="result must be"):
        RatedGame("a", "b", result)  # type: ignore[arg-type]


def test_rated_game_rejects_same_competitor() -> None:
    with pytest.raises(ValueError, match="must be distinct"):
        RatedGame("same", "same", "1-0")


@pytest.mark.parametrize(
    ("game", "message"),
    [
        (RatedGame("missing", "known", "1-0"), "unknown white"),
        (RatedGame("known", "missing", "1-0"), "unknown black"),
    ],
)
def test_batch_rejects_unknown_competitors(game: RatedGame, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        calculate_rating_batch([competitor("known")], [game])


def test_batch_rejects_duplicate_competitor_ids() -> None:
    with pytest.raises(ValueError, match="duplicate competitor_id"):
        calculate_rating_batch([competitor("a"), competitor("a")], [])


def test_batch_rejects_untyped_inputs() -> None:
    with pytest.raises(TypeError, match="only CompetitorRating"):
        calculate_rating_batch([object()], [])  # type: ignore[list-item]
    with pytest.raises(TypeError, match="only RatedGame"):
        calculate_rating_batch([competitor("a")], [object()])  # type: ignore[list-item]


def test_batch_validates_tau_even_when_no_competitor_plays() -> None:
    with pytest.raises(ValueError, match="tau must be greater than zero"):
        calculate_rating_batch([], [], tau=0)
