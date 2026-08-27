from dataclasses import replace

import pytest

from chessharness.config import GameConfig
from chessharness.ratings.ruleset import (
    STANDARD_RULESET_HASH,
    STANDARD_RULESET_ID,
    evaluate_ruleset,
)


def test_default_game_config_is_standard_v1() -> None:
    result = evaluate_ruleset(GameConfig())

    assert result.eligible is True
    assert result.reason is None
    assert result.pool_id == STANDARD_RULESET_ID
    assert result.ruleset_hash == STANDARD_RULESET_HASH


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("starting_fen", "8/8/8/8/8/8/8/K6k w - - 0 1"),
        ("board_input", "image"),
        ("show_legal_moves", False),
        ("max_retries", 4),
        ("max_output_tokens", 4096),
        ("reasoning_effort", "high"),
        ("move_timeout", 60),
    ],
)
def test_behavior_changes_make_a_game_unrated(field: str, value: object) -> None:
    result = evaluate_ruleset(replace(GameConfig(), **{field: value}))

    assert result.eligible is False
    assert field in (result.reason or "")


def test_non_behavioral_pgn_settings_do_not_change_eligibility() -> None:
    result = evaluate_ruleset(
        replace(GameConfig(), annotate_pgn=True, save_pgn=False, pgn_dir="elsewhere")
    )

    assert result.eligible is True


def test_unknown_pool_is_unrated() -> None:
    result = evaluate_ruleset(GameConfig(), pool_id="standard-v2")

    assert result.eligible is False
    assert result.reason == "Unknown rated pool: standard-v2"
