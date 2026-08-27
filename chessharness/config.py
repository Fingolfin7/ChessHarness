"""
Configuration loading from config.yaml.

Uses typed dataclasses throughout so the rest of the app gets IDE
completion and type-checker support without touching raw dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

BoardInputMode = Literal["text", "image"]
ReasoningEffort = Literal["low", "medium", "high"]


@dataclass
class GameConfig:
    max_retries: int = 3
    board_input: BoardInputMode = "text"
    show_legal_moves: bool = True
    annotate_pgn: bool = False
    max_output_tokens: int = 5120
    reasoning_effort: ReasoningEffort | None = None
    move_timeout: int = 120   # seconds before a model response is abandoned
    save_pgn: bool = True
    pgn_dir: str = "./games"
    starting_fen: str | None = None  # None = standard starting position


@dataclass
class ModelEntry:
    id: str    # model ID sent to the API
    name: str  # display name shown in the UI
    supports_vision: bool | None = None  # explicit override; None = auto-detect


@dataclass
class ProviderConfig:
    api_key: str = ""
    bearer_token: str = ""
    models: list[ModelEntry] = field(default_factory=list)
    base_url: str | None = None

    @property
    def auth_token(self) -> str:
        """Prefer bearer_token when present, else fall back to api_key."""
        return self.bearer_token or self.api_key


@dataclass(frozen=True)
class EngineProfile:
    """A reproducible UCI engine benchmark profile."""

    id: str = "stockfish-1600"
    name: str = "Stockfish 1600"
    path: str = "stockfish"
    uci_elo: int = 1600
    nodes: int = 100_000
    threads: int = 1
    hash_mb: int = 64

    @property
    def competitor_id(self) -> str:
        return (
            f"engine:stockfish:{self.id}:uci-{self.uci_elo}:"
            f"nodes-{self.nodes}:threads-{self.threads}:hash-{self.hash_mb}"
        )


def _default_engines() -> dict[str, EngineProfile]:
    profile = EngineProfile()
    return {profile.id: profile}


@dataclass(frozen=True)
class RatingConfig:
    enabled: bool = True
    database_path: str = "./data/ratings.sqlite3"
    pool_id: str = "standard-v1"
    algorithm_version: str = "glicko2-v1"
    tau: float = 0.5
    benchmark_rd: float = 60.0


@dataclass
class Config:
    game: GameConfig
    providers: dict[str, ProviderConfig]
    engines: dict[str, EngineProfile] = field(default_factory=_default_engines)
    ratings: RatingConfig = field(default_factory=RatingConfig)

    @property
    def pgn_dir_path(self) -> Path:
        return Path(self.game.pgn_dir)

    def all_models(self) -> list[tuple[str, ModelEntry]]:
        """Return selectable LLM and engine profiles using one picker shape."""
        models = [
            (provider_name, model)
            for provider_name, prov_cfg in self.providers.items()
            for model in prov_cfg.models
        ]
        models.extend(
            (
                "engine",
                ModelEntry(id=profile.id, name=profile.name, supports_vision=False),
            )
            for profile in self.engines.values()
        )
        return models


def load_config(path: str | Path = "config.yaml") -> Config:
    """
    Load and validate config.yaml.

    Raises:
        FileNotFoundError: config.yaml is missing.
        ValueError: required fields are absent or invalid.
    """
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {cfg_path.resolve()}\n"
            "Copy config.example.yaml to config.yaml and fill in your API keys."
        )

    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    try:
        game_raw = raw.get("game") or {}
        game_cfg = GameConfig(
            max_retries=int(game_raw.get("max_retries", 3)),
            board_input=game_raw.get("board_input", "text"),
            show_legal_moves=bool(game_raw.get("show_legal_moves", True)),
            annotate_pgn=bool(game_raw.get("annotate_pgn", False)),
            max_output_tokens=int(game_raw.get("max_output_tokens", 5120)),
            reasoning_effort=_parse_reasoning_effort(game_raw.get("reasoning_effort")),
            move_timeout=int(game_raw.get("move_timeout", 120)),
            save_pgn=bool(game_raw.get("save_pgn", True)),
            pgn_dir=game_raw.get("pgn_dir", "./games"),
            starting_fen=(str(game_raw["starting_fen"]).strip() or None)
            if game_raw.get("starting_fen") is not None
            else None,
        )

        providers_raw = raw.get("providers") or {}
        providers: dict[str, ProviderConfig] = {}
        for provider_name, prov_raw in providers_raw.items():
            prov_raw = prov_raw or {}
            models = [
                ModelEntry(
                    id=str(m["id"]),
                    name=str(m["name"]),
                    supports_vision=_parse_supports_vision(m.get("supports_vision")),
                )
                for m in prov_raw.get("models", [])
            ]
            providers[provider_name] = ProviderConfig(
                api_key=str(prov_raw.get("api_key", "")),
                bearer_token=str(prov_raw.get("bearer_token", "")),
                models=models,
                base_url=prov_raw.get("base_url"),
            )

        engines_raw = raw.get("engines")
        engines = _default_engines() if engines_raw is None else {}
        for profile_id, engine_raw in (engines_raw or {}).items():
            engine_raw = engine_raw or {}
            profile = EngineProfile(
                id=str(profile_id),
                name=str(engine_raw.get("name", profile_id)),
                path=str(engine_raw.get("path", "stockfish")),
                uci_elo=int(engine_raw.get("uci_elo", 1600)),
                nodes=int(engine_raw.get("nodes", 100_000)),
                threads=int(engine_raw.get("threads", 1)),
                hash_mb=int(engine_raw.get("hash_mb", 64)),
            )
            engines[profile.id] = profile

        ratings_raw = raw.get("ratings") or {}
        ratings = RatingConfig(
            enabled=bool(ratings_raw.get("enabled", True)),
            database_path=str(ratings_raw.get("database_path", "./data/ratings.sqlite3")),
            pool_id=str(ratings_raw.get("pool_id", "standard-v1")),
            algorithm_version=str(ratings_raw.get("algorithm_version", "glicko2-v1")),
            tau=float(ratings_raw.get("tau", 0.5)),
            benchmark_rd=float(ratings_raw.get("benchmark_rd", 60.0)),
        )

        config = Config(
            game=game_cfg,
            providers=providers,
            engines=engines,
            ratings=ratings,
        )
        _validate(config)
        return config

    except (KeyError, TypeError) as exc:
        raise ValueError(f"Invalid config.yaml structure: {exc}") from exc


def _validate(config: Config) -> None:
    valid_modes = ("text", "image")
    if config.game.board_input not in valid_modes:
        raise ValueError(
            f"game.board_input must be one of {valid_modes}, got '{config.game.board_input}'"
        )
    if config.game.max_retries < 1:
        raise ValueError("game.max_retries must be >= 1")
    if config.game.max_output_tokens < 1:
        raise ValueError("game.max_output_tokens must be >= 1")
    for profile in config.engines.values():
        if profile.uci_elo < 1:
            raise ValueError("engine uci_elo must be >= 1")
        if profile.nodes < 1 or profile.threads < 1 or profile.hash_mb < 1:
            raise ValueError("engine nodes, threads, and hash_mb must be >= 1")
    if config.ratings.tau <= 0:
        raise ValueError("ratings.tau must be > 0")
    if config.ratings.benchmark_rd <= 0:
        raise ValueError("ratings.benchmark_rd must be > 0")
    # Providers and their model lists are now optional in config.yaml.
    # Connections and model discovery are handled at runtime via the web UI.


def _parse_supports_vision(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise ValueError(
        f"model.supports_vision must be true/false when provided, got {value!r}"
    )


def _parse_reasoning_effort(value: object) -> ReasoningEffort | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if not text or text in {"none", "default", "auto"}:
            return None
        if text in {"low", "medium", "high"}:
            return text  # type: ignore[return-value]
    raise ValueError(
        f"game.reasoning_effort must be one of low/medium/high/null, got {value!r}"
    )
