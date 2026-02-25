"""
LLM Chess Harness — entry point.

Wires together:  config → selector → providers → players → game loop → CLI display
"""

from __future__ import annotations

import asyncio
import signal
import sys
from datetime import datetime
from pathlib import Path

from chessharness.config import load_config
from chessharness.providers import create_provider
from chessharness.players import create_player
from chessharness.players.llm import LLMPlayer
from chessharness.game import run_game
from chessharness.cli.display import display_event, console
from chessharness.cli.selector import select_players
from chessharness.conv_logger import ConversationLogger


async def _main(stop_event: asyncio.Event) -> None:
    config_path = Path("config.yaml")
    try:
        config = load_config(config_path)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/] {exc}")
        sys.exit(1)
    except ValueError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        sys.exit(1)

    # Interactive model selection
    white_sel, black_sel = select_players(config)

    # Build providers (LLM API clients)
    white_provider = create_provider(white_sel.provider_name, white_sel.model.id, config.providers)
    black_provider = create_provider(black_sel.provider_name, black_sel.model.id, config.providers)

    # Build players
    white_player = create_player(white_sel.provider_name, white_sel.display_name, white_provider, config.game.show_legal_moves, config.game.move_timeout)
    black_player = create_player(black_sel.provider_name, black_sel.display_name, black_provider, config.game.show_legal_moves, config.game.move_timeout)

    # Attach per-player conversation loggers (shared game_id keeps filenames paired)
    log_dir = Path("./logs")
    game_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    for player, color in ((white_player, "white"), (black_player, "black")):
        if isinstance(player, LLMPlayer):
            player._logger = ConversationLogger(
                log_dir=log_dir,
                game_id=game_id,
                player_name=player.name,
                color=color,
            )
    console.print(f"[dim]Logs: {log_dir}/game_{game_id}_white_*.log / ..._black_*.log[/]\n")

    # Run game — consume events and display them
    async for event in run_game(config, white_player, black_player, stop_event=stop_event):
        display_event(event)


def main() -> None:
    async def _run() -> None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        original_sigint = signal.getsignal(signal.SIGINT)

        def _on_sigint(sig: int, frame: object) -> None:
            # Schedule the event set on the event loop thread (safe on Windows)
            loop.call_soon_threadsafe(stop_event.set)
            # Restore the original handler so a second Ctrl+C force-quits
            signal.signal(signal.SIGINT, original_sigint)

        signal.signal(signal.SIGINT, _on_sigint)
        await _main(stop_event)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
