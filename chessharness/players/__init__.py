"""
Player factory.

create_player() is the single entry point for instantiating any Player.

To add a new player type (e.g., LichessPlayer):
  1. Create chessharness/players/lichess.py implementing Player
  2. Add "lichess" case here
"""

from __future__ import annotations

from chessharness.players.base import Player, GameState
from chessharness.players.llm import LLMPlayer
from chessharness.players.human import HumanPlayer, QueuedHumanPlayer
from chessharness.players.engine import EnginePlayer
from chessharness.providers.base import LLMProvider
from chessharness.config import EngineProfile

__all__ = [
    "Player",
    "GameState",
    "LLMPlayer",
    "HumanPlayer",
    "QueuedHumanPlayer",
    "EnginePlayer",
    "create_player",
]


def create_player(
    provider_name: str,
    display_name: str,
    provider: LLMProvider | None = None,
    show_legal_moves: bool = True,
    move_timeout: int = 120,
    max_output_tokens: int = 5120,
    reasoning_effort: str | None = None,
    model_id: str | None = None,
    engine_profile: EngineProfile | None = None,
) -> Player:
    """
    Instantiate the correct Player.

    For LLM-backed providers, pass a pre-built LLMProvider.
    Special provider names "human" and "engine" don't need a provider instance.
    """
    match provider_name:
        case "human":
            return HumanPlayer(name=display_name)
        case "engine":
            profile = engine_profile or EngineProfile(
                id=model_id or "stockfish-1600",
                name=display_name,
            )
            return EnginePlayer(
                name=display_name,
                engine_path=profile.path,
                uci_elo=profile.uci_elo,
                node_limit=profile.nodes,
                threads=profile.threads,
                hash_mb=profile.hash_mb,
                competitor_id=profile.competitor_id,
            )
        case _:
            if provider is None:
                raise ValueError(
                    f"LLMPlayer for provider '{provider_name}' requires a provider instance"
                )
            return LLMPlayer(
                name=display_name,
                provider=provider,
                show_legal_moves=show_legal_moves,
                move_timeout=move_timeout,
                max_output_tokens=max_output_tokens,
                reasoning_effort=reasoning_effort,
                competitor_id=(
                    f"llm:{provider_name}:{model_id}"
                    if model_id
                    else None
                ),
            )

