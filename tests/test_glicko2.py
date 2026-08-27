"""Tests for the canonical Glicko-2 calculation."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import math

import pytest

from chessharness.ratings.glicko2 import Glicko2Rating, Glicko2Result, update_rating


def test_official_glickman_worked_example() -> None:
    updated = update_rating(
        Glicko2Rating(rating=1500, deviation=200, volatility=0.06),
        [
            Glicko2Result(1400, 30, 1.0),
            Glicko2Result(1550, 100, 0.0),
            Glicko2Result(1700, 300, 0.0),
        ],
    )

    assert updated.rating == pytest.approx(1464.06, abs=0.01)
    assert updated.deviation == pytest.approx(151.52, abs=0.01)
    assert updated.volatility == pytest.approx(0.059996, abs=0.000001)


def test_equal_players_draw_without_changing_rating() -> None:
    current = Glicko2Rating()

    updated = update_rating(current, [Glicko2Result(1500, 350, 0.5)])

    assert updated.rating == pytest.approx(current.rating)
    assert updated.deviation < current.deviation
    assert updated.volatility > 0


def test_multiple_draws_are_applied_as_one_rating_period() -> None:
    current = Glicko2Rating(rating=1600, deviation=120, volatility=0.06)
    games = [Glicko2Result(1500, 80, 0.5), Glicko2Result(1700, 80, 0.5)]

    updated = update_rating(current, games)

    assert updated.rating == pytest.approx(1600)
    assert updated.deviation < current.deviation


def test_no_results_returns_the_unchanged_immutable_state() -> None:
    current = Glicko2Rating(rating=1725, deviation=90, volatility=0.05)

    assert update_rating(current, []) is current
    with pytest.raises(FrozenInstanceError):
        current.rating = 1000  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"rating": math.nan}, "rating must be finite"),
        ({"deviation": 0}, "deviation must be greater than zero"),
        ({"deviation": -1}, "deviation must be greater than zero"),
        ({"volatility": 0}, "volatility must be greater than zero"),
        ({"volatility": math.inf}, "volatility must be finite"),
    ],
)
def test_rating_rejects_invalid_state(kwargs: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Glicko2Rating(**kwargs)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((1500, 0, 0.5), "opponent_deviation must be greater than zero"),
        ((1500, 100, -0.1), "score must be between zero and one"),
        ((1500, 100, 1.1), "score must be between zero and one"),
        ((math.inf, 100, 0.5), "opponent_rating must be finite"),
        ((1500, math.nan, 0.5), "opponent_deviation must be finite"),
    ],
)
def test_result_rejects_invalid_values(args: tuple[float, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Glicko2Result(*args)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"tau": 0}, "tau must be greater than zero"),
        ({"tau": math.inf}, "tau must be finite"),
        ({"convergence_tolerance": 0}, "convergence_tolerance must be greater than zero"),
        ({"convergence_tolerance": math.nan}, "convergence_tolerance must be finite"),
    ],
)
def test_update_rejects_invalid_solver_settings(
    kwargs: dict[str, float], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        update_rating(Glicko2Rating(), [], **kwargs)


def test_update_requires_typed_state_and_results() -> None:
    with pytest.raises(TypeError, match="current must be a Glicko2Rating"):
        update_rating(object(), [])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="results must be an iterable"):
        update_rating(Glicko2Rating(), None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="only Glicko2Result"):
        update_rating(Glicko2Rating(), [object()])  # type: ignore[list-item]


def test_benchmark_opponent_does_not_need_volatility() -> None:
    benchmark_result = Glicko2Result(
        opponent_rating=1600,
        opponent_deviation=60,
        score=1,
    )

    updated = update_rating(Glicko2Rating(), [benchmark_result])

    assert updated.rating > 1500
