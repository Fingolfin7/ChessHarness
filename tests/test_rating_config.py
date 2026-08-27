from pathlib import Path

import pytest

from chessharness.config import load_config


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_missing_engine_and_rating_sections_use_reproducible_defaults(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, "game: {}\nproviders: {}\n"))

    profile = config.engines["stockfish-1600"]
    assert profile.path == "stockfish"
    assert profile.uci_elo == 1600
    assert profile.nodes == 100_000
    assert config.ratings.pool_id == "standard-v1"


def test_explicit_engine_and_rating_settings_are_loaded(tmp_path: Path) -> None:
    config = load_config(
        _write(
            tmp_path,
            """
game:
  starting_fen: " 8/8/8/8/8/8/8/K6k w - - 0 1 "
providers: {}
engines:
  sf-test:
    name: Test fish
    path: C:/engines/stockfish.exe
    uci_elo: 1800
    nodes: 250000
    threads: 2
    hash_mb: 128
ratings:
  database_path: ./custom.sqlite3
  tau: 0.3
  benchmark_rd: 75
""",
        )
    )

    assert config.game.starting_fen == "8/8/8/8/8/8/8/K6k w - - 0 1"
    assert list(config.engines) == ["sf-test"]
    assert config.engines["sf-test"].nodes == 250_000
    assert config.ratings.database_path == "./custom.sqlite3"
    assert config.ratings.tau == pytest.approx(0.3)


@pytest.mark.parametrize(
    "section",
    [
        "engines:\n  bad:\n    nodes: 0",
        "ratings:\n  tau: 0",
        "ratings:\n  benchmark_rd: 0",
    ],
)
def test_invalid_rating_or_engine_settings_are_rejected(
    tmp_path: Path, section: str
) -> None:
    path = _write(tmp_path, f"game: {{}}\nproviders: {{}}\n{section}\n")

    with pytest.raises(ValueError):
        load_config(path)
