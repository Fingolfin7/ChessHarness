"""
Rich-based CLI event consumer.

This is the ONLY place where terminal output happens.
It translates GameEvent objects into formatted Rich output.

Future web UI replacement: swap this module for a WebSocket broadcaster
that serializes events via dataclasses.asdict() and sends JSON to clients.
The game loop (game.py) requires zero changes.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from chessharness.events import (
    GameEvent,
    GameStartEvent,
    TurnStartEvent,
    MoveRequestedEvent,
    InvalidMoveEvent,
    ReasoningChunkEvent,
    MoveAppliedEvent,
    CheckEvent,
    GameOverEvent,
)

console = Console(legacy_windows=False)


def display_event(event: GameEvent) -> None:
    """Dispatch a GameEvent to the appropriate display function."""
    match event:
        case GameStartEvent():
            _game_start(event)
        case TurnStartEvent():
            _turn_start(event)
        case MoveRequestedEvent():
            console.print("  [dim]thinking…[/] ", end="")
        case ReasoningChunkEvent():
            console.print(event.chunk, end="", highlight=False, markup=False)
        case InvalidMoveEvent():
            _invalid_move(event)
        case MoveAppliedEvent():
            _move_applied(event)
        case CheckEvent():
            _check(event)
        case GameOverEvent():
            _game_over(event)


# --------------------------------------------------------------------------- #
# Display functions                                                            #
# --------------------------------------------------------------------------- #

def _game_start(event: GameStartEvent) -> None:
    console.print()
    console.print(
        Panel(
            f"[bold white]{event.white_name}[/] [dim](White)[/]  vs  "
            f"[bold white]{event.black_name}[/] [dim](Black)[/]\n"
            f"[dim]{event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}[/]",
            title="[bold green] Chess Harness [/]",
            border_style="green",
            expand=False,
        )
    )


def _turn_start(event: TurnStartEvent) -> None:
    symbol = "♔" if event.color == "white" else "♚"
    color_style = "bold white" if event.color == "white" else "bold bright_black"

    console.print()
    console.print(
        f"[dim]Move {event.move_number}[/]  "
        f"[{color_style}]{symbol}  {event.player_name}[/] to move"
    )
    console.print(
        Panel(
            f"[green]{event.board_ascii}[/]",
            subtitle=f"[dim]{event.fen}[/]",
            border_style="dim",
            padding=(0, 1),
        )
    )

    if event.move_history_san:
        history = " ".join(event.move_history_san)
        console.print(f"[dim]History:[/] {history}")


def _invalid_move(event: InvalidMoveEvent) -> None:
    console.print()  # end streaming line
    raw_preview = repr(event.raw_response) if event.raw_response else "''"
    console.print(
        f"  [red]✗[/] Attempt {event.attempt_num}: "
        f"[yellow]{event.attempted_move!r}[/] rejected — {event.error}\n"
        f"    [dim]raw: {raw_preview}[/]"
    )


def _move_applied(event: MoveAppliedEvent) -> None:
    console.print()  # end streaming line
    check_tag = "  [bold red]+[/]" if event.is_check else ""
    console.print(
        f"  [green]✓[/] [bold]{event.move_san}[/]{check_tag}"
        f"  [dim]({event.move_uci})[/]"
    )


def _check(event: CheckEvent) -> None:
    console.print(
        f"  [bold red]CHECK![/] "
        f"{event.color_in_check.upper()} is in check after [bold]{event.checking_move_san}[/]"
    )


def _game_over(event: GameOverEvent) -> None:
    result_styles: dict[str, str] = {
        "1-0": "bold green",
        "0-1": "bold red",
        "1/2-1/2": "bold yellow",
        "*": "dim",
    }
    style = result_styles.get(event.result, "white")
    reason = event.reason.replace("_", " ").title()

    if event.reason == "interrupted":
        outcome_text = "[yellow]Game stopped by user[/]"
    elif event.winner_name:
        outcome_text = f"Winner: [bold]{event.winner_name}[/]"
    else:
        outcome_text = "[yellow]Draw[/]"

    console.print()
    console.print(
        Panel(
            f"[{style}]{event.result}[/]  —  {reason}\n"
            f"{outcome_text}\n"
            f"[dim]Total moves: {event.total_moves}[/]",
            title="[bold]Game Over[/]",
            border_style=style.replace("bold ", ""),
            expand=False,
        )
    )

    console.print()
    console.rule("[dim]PGN[/]")
    console.print(event.pgn)
    console.rule()
