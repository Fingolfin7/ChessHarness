"""Canonical Glicko-2 rating-period updates.

The public API deliberately models opponents with only a rating and rating
deviation.  An opponent's volatility is not part of the Glicko-2 update for
the active competitor, which also makes fixed benchmark opponents natural to
represent.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


GLICKO2_SCALE = 173.7178
DEFAULT_RATING = 1500.0
DEFAULT_DEVIATION = 350.0
DEFAULT_VOLATILITY = 0.06
DEFAULT_TAU = 0.5
DEFAULT_CONVERGENCE_TOLERANCE = 1e-6


def _require_finite(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be a real number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True, slots=True)
class Glicko2Rating:
    """An immutable competitor state on the conventional Glicko scale."""

    rating: float = DEFAULT_RATING
    deviation: float = DEFAULT_DEVIATION
    volatility: float = DEFAULT_VOLATILITY

    def __post_init__(self) -> None:
        _require_finite("rating", self.rating)
        _require_finite("deviation", self.deviation)
        _require_finite("volatility", self.volatility)
        if self.deviation <= 0:
            raise ValueError("deviation must be greater than zero")
        if self.volatility <= 0:
            raise ValueError("volatility must be greater than zero")


@dataclass(frozen=True, slots=True)
class Glicko2Result:
    """One result from the active competitor's perspective.

    ``score`` is 1 for a win, 0.5 for a draw, and 0 for a loss.  Intermediate
    values are accepted because the canonical equations allow fractional
    scores, even though ordinary chess games use those three values.
    """

    opponent_rating: float
    opponent_deviation: float
    score: float

    def __post_init__(self) -> None:
        _require_finite("opponent_rating", self.opponent_rating)
        _require_finite("opponent_deviation", self.opponent_deviation)
        _require_finite("score", self.score)
        if self.opponent_deviation <= 0:
            raise ValueError("opponent_deviation must be greater than zero")
        if not 0 <= self.score <= 1:
            raise ValueError("score must be between zero and one")


def _impact(opponent_phi: float) -> float:
    """Return Glickman's g(phi), using hypot to avoid needless overflow."""

    return 1.0 / math.hypot(1.0, math.sqrt(3.0) * opponent_phi / math.pi)


def _expected_score(mu: float, opponent_mu: float, impact: float) -> float:
    """Return E(mu, mu_j, phi_j) with a numerically stable logistic."""

    exponent = impact * (mu - opponent_mu)
    if exponent >= 0:
        return 1.0 / (1.0 + math.exp(-exponent))
    exp_exponent = math.exp(exponent)
    return exp_exponent / (1.0 + exp_exponent)


def _new_volatility(
    *,
    phi: float,
    volatility: float,
    variance: float,
    improvement: float,
    tau: float,
    tolerance: float,
) -> float:
    """Solve the canonical Glicko-2 volatility equation (algorithm step 5)."""

    alpha = math.log(volatility * volatility)

    def objective(x: float) -> float:
        exp_x = math.exp(x)
        denominator = phi * phi + variance + exp_x
        return (
            exp_x
            * (improvement * improvement - phi * phi - variance - exp_x)
            / (2.0 * denominator * denominator)
            - (x - alpha) / (tau * tau)
        )

    lower = alpha
    if improvement * improvement > phi * phi + variance:
        upper = math.log(improvement * improvement - phi * phi - variance)
    else:
        steps = 1
        upper = alpha - steps * tau
        while objective(upper) < 0:
            steps += 1
            upper = alpha - steps * tau

    f_lower = objective(lower)
    f_upper = objective(upper)
    while abs(upper - lower) > tolerance:
        candidate = lower + (lower - upper) * f_lower / (f_upper - f_lower)
        f_candidate = objective(candidate)
        if f_candidate * f_upper <= 0:
            lower = upper
            f_lower = f_upper
        else:
            f_lower /= 2.0
        upper = candidate
        f_upper = f_candidate

    return math.exp(lower / 2.0)


def update_rating(
    current: Glicko2Rating,
    results: Iterable[Glicko2Result],
    *,
    tau: float = DEFAULT_TAU,
    convergence_tolerance: float = DEFAULT_CONVERGENCE_TOLERANCE,
) -> Glicko2Rating:
    """Update one competitor from all results in a single rating period.

    When ``results`` is empty, ``current`` is returned unchanged.  In
    particular, this module does not inflate deviation for inactivity.
    """

    if not isinstance(current, Glicko2Rating):
        raise TypeError("current must be a Glicko2Rating")
    _require_finite("tau", tau)
    _require_finite("convergence_tolerance", convergence_tolerance)
    if tau <= 0:
        raise ValueError("tau must be greater than zero")
    if convergence_tolerance <= 0:
        raise ValueError("convergence_tolerance must be greater than zero")

    try:
        period_results = tuple(results)
    except TypeError as exc:
        raise TypeError("results must be an iterable of Glicko2Result values") from exc
    if any(not isinstance(result, Glicko2Result) for result in period_results):
        raise TypeError("results must contain only Glicko2Result values")
    if not period_results:
        return current

    mu = (current.rating - DEFAULT_RATING) / GLICKO2_SCALE
    phi = current.deviation / GLICKO2_SCALE
    information = 0.0
    score_difference = 0.0

    for result in period_results:
        opponent_mu = (result.opponent_rating - DEFAULT_RATING) / GLICKO2_SCALE
        opponent_phi = result.opponent_deviation / GLICKO2_SCALE
        impact = _impact(opponent_phi)
        expected = _expected_score(mu, opponent_mu, impact)
        information += impact * impact * expected * (1.0 - expected)
        score_difference += impact * (result.score - expected)

    if information <= 0 or not math.isfinite(information):
        raise ValueError("results do not provide numerically usable rating information")

    variance = 1.0 / information
    improvement = variance * score_difference
    volatility = _new_volatility(
        phi=phi,
        volatility=current.volatility,
        variance=variance,
        improvement=improvement,
        tau=float(tau),
        tolerance=float(convergence_tolerance),
    )

    pre_rating_phi = math.hypot(phi, volatility)
    new_phi = 1.0 / math.sqrt(1.0 / (pre_rating_phi * pre_rating_phi) + 1.0 / variance)
    new_mu = mu + new_phi * new_phi * score_difference

    return Glicko2Rating(
        rating=new_mu * GLICKO2_SCALE + DEFAULT_RATING,
        deviation=new_phi * GLICKO2_SCALE,
        volatility=volatility,
    )
