"""
Chess Harness — Tournament entry point.

Usage:
    uv run python tournament_main.py

Wires together:
    config → participant selector → tournament settings →
    providers → player factory → tournament loop → CLI display
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from chessharness.cli.tournament_display import console, display_tournament_event
from chessharness.cli.tournament_selector import (
    select_tournament_participants,
    select_tournament_settings,
)
from chessharness.config import load_config
from chessharness.players import create_player
from chessharness.players.base import Player
from chessharness.providers import create_provider
from chessharness.tournaments import create_tournament
from chessharness.tournaments.base import PlayerFactory, TournamentParticipant
from chessharness.tournaments.events import TournamentCompleteEvent


async def _main() -> None:
    config_path = Path("config.yaml")
    try:
        config = load_config(config_path)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/] {exc}")
        sys.exit(1)
    except ValueError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        sys.exit(1)

    # ── Select participants ──────────────────────────────────────────── #
    participants = select_tournament_participants(config)

    # ── Select tournament settings ───────────────────────────────────── #
    tournament_type, draw_handling = select_tournament_settings()

    # ── Build the player factory ─────────────────────────────────────── #
    # Called once per game so each game starts with a fresh LLMPlayer
    # (no conversation history carried over between games).

    def player_factory(participant: TournamentParticipant) -> Player:
        provider = create_provider(
            participant.provider_name,
            participant.model.id,
            config.providers,
            supports_vision_override=participant.model.supports_vision,
        )
        return create_player(
            participant.provider_name,
            participant.display_name,
            provider,
            config.game.show_legal_moves,
            config.game.move_timeout,
            config.game.max_output_tokens,
            config.game.reasoning_effort,
        )

    # ── Create and run the tournament ────────────────────────────────── #
    tournament = create_tournament(tournament_type, draw_handling=draw_handling)

    console.print(
        f"\n[dim]Starting [bold]{tournament_type}[/] tournament with "
        f"[bold]{len(participants)}[/] participants…[/]\n"
    )

    async for event in tournament.run(participants, config, player_factory):
        display_tournament_event(event)
        if isinstance(event, TournamentCompleteEvent) and config.game.save_pgn:
            _save_all_pgns(tournament, config)


def _save_all_pgns(tournament, config) -> None:
    """Save each match PGN from the tournament to pgn_dir."""
    from datetime import datetime

    pgn_dir = config.pgn_dir_path
    pgn_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for i, result in enumerate(tournament._all_results, 1):
        if result.pgn:
            fname = pgn_dir / f"tournament_{timestamp}_match_{result.match_id.replace('/', '-')}.pgn"
            fname.write_text(result.pgn, encoding="utf-8")


def main() -> None:
    async def _run() -> None:
        stop_requested = False
        loop = asyncio.get_running_loop()
        original_sigint = signal.getsignal(signal.SIGINT)

        def _on_sigint(sig: int, frame: object) -> None:
            nonlocal stop_requested
            if not stop_requested:
                stop_requested = True
                console.print("\n[yellow]Stopping after current game…[/]")
                signal.signal(signal.SIGINT, original_sigint)
            else:
                sys.exit(1)

        signal.signal(signal.SIGINT, _on_sigint)
        await _main()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
