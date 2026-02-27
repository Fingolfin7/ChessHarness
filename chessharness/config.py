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


@dataclass
class GameConfig:
    max_retries: int = 3
    board_input: BoardInputMode = "text"
    show_legal_moves: bool = True
    move_timeout: int = 120   # seconds before a model response is abandoned
    save_pgn: bool = True
    pgn_dir: str = "./games"


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


@dataclass
class Config:
    game: GameConfig
    providers: dict[str, ProviderConfig]

    @property
    def pgn_dir_path(self) -> Path:
        return Path(self.game.pgn_dir)

    def all_models(self) -> list[tuple[str, ModelEntry]]:
        """Return a flat list of (provider_name, ModelEntry) across all providers."""
        return [
            (provider_name, model)
            for provider_name, prov_cfg in self.providers.items()
            for model in prov_cfg.models
        ]


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
            move_timeout=int(game_raw.get("move_timeout", 120)),
            save_pgn=bool(game_raw.get("save_pgn", True)),
            pgn_dir=game_raw.get("pgn_dir", "./games"),
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

        config = Config(game=game_cfg, providers=providers)
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
