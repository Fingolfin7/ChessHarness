"""
Rich-based CLI consumer for TournamentEvent objects.

Per-game events (MatchGameEvent) are delegated directly to the existing
display_event() function so game output is identical to a standalone game.

Tournament-level events (bracket, round, match result) are rendered with
additional Rich panels and tables.
"""

from __future__ import annotations

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from chessharness.cli.display import display_event
from chessharness.tournaments.events import (
    MatchCompleteEvent,
    MatchGameEvent,
    MatchStartEvent,
    RoundCompleteEvent,
    RoundStartEvent,
    TournamentCompleteEvent,
    TournamentEvent,
    TournamentStartEvent,
)

console = Console(legacy_windows=False)


def display_tournament_event(event: TournamentEvent) -> None:
    """Dispatch a TournamentEvent to the appropriate display function."""
    match event:
        case TournamentStartEvent():
            _tournament_start(event)
        case RoundStartEvent():
            _round_start(event)
        case MatchStartEvent():
            _match_start(event)
        case MatchGameEvent():
            # Delegate to the single-game display
            display_event(event.game_event)
        case MatchCompleteEvent():
            _match_complete(event)
        case RoundCompleteEvent():
            _round_complete(event)
        case TournamentCompleteEvent():
            _tournament_complete(event)


# --------------------------------------------------------------------------- #
# Display functions                                                            #
# --------------------------------------------------------------------------- #

def _tournament_start(event: TournamentStartEvent) -> None:
    names = "  •  ".join(event.participant_names)
    console.print()
    console.print(
        Panel(
            f"[bold]{event.tournament_type.replace('_', ' ').title()} Tournament[/]\n\n"
            f"[dim]Participants ({len(event.participant_names)}):[/]\n{names}\n\n"
            f"[dim]Rounds: {event.total_rounds}  •  "
            f"{event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}[/]",
            title="[bold green] Chess Harness Tournament [/]",
            border_style="green",
            expand=False,
        )
    )


def _round_start(event: RoundStartEvent) -> None:
    console.print()
    console.rule(
        f"[bold]Round {event.round_num} of {event.total_rounds}[/]",
        style="bright_blue",
    )
    console.print()

    table = Table(show_header=True, header_style="bold", border_style="dim", show_lines=False)
    table.add_column("Match", style="dim", width=8)
    table.add_column("White", min_width=20)
    table.add_column("", width=3, justify="center")
    table.add_column("Black", min_width=20)

    for match_id, white_name, black_name in event.pairings:
        if black_name == "BYE":
            table.add_row(match_id, f"[bold]{white_name}[/]", "→", "[dim]BYE[/]")
        else:
            table.add_row(match_id, f"[bold]{white_name}[/]", "vs", f"[bold]{black_name}[/]")

    console.print(table)
    console.print()


def _match_start(event: MatchStartEvent) -> None:
    label = f"[bold bright_blue]▶ Match {event.match_id}[/]"
    if event.game_num > 1:
        label += f"  [yellow](Rematch — game {event.game_num})[/]"
    console.print()
    console.print(
        f"{label}  "
        f"[bold white]{event.white_name}[/] [dim](White)[/]  vs  "
        f"[bold]{event.black_name}[/] [dim](Black)[/]"
    )


def _match_complete(event: MatchCompleteEvent) -> None:
    r = event.result
    if r.winner:
        summary = (
            f"[green]✓[/] [bold]{event.advancing_name}[/] advances  "
            f"[dim]({r.game_result} in {r.total_moves} moves)[/]"
        )
    else:
        summary = (
            f"[yellow]½[/] Draw — [bold]{event.advancing_name}[/] advances "
            f"[dim]({r.game_result})[/]"
        )
    console.print(f"\n  {summary}")


def _round_complete(event: RoundCompleteEvent) -> None:
    console.print()
    console.rule(f"[dim]Round {event.round_num} complete[/]", style="dim")

    if not event.standings:
        return

    table = Table(
        title=f"Standings after Round {event.round_num}",
        show_header=True,
        header_style="bold",
        border_style="dim",
        show_lines=False,
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Name", min_width=20)
    table.add_column("W", justify="center", width=4)
    table.add_column("D", justify="center", width=4)
    table.add_column("L", justify="center", width=4)
    table.add_column("Pts", justify="right", width=5)

    for i, entry in enumerate(event.standings, 1):
        table.add_row(
            str(i),
            entry.participant.display_name,
            str(entry.wins),
            str(entry.draws),
            str(entry.losses),
            f"{entry.points:.1f}",
        )

    console.print()
    console.print(table)


def _tournament_complete(event: TournamentCompleteEvent) -> None:
    console.print()
    console.print(
        Panel(
            f"[bold yellow]★  {event.winner_name}[/]\n\n"
            f"[dim]{event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}[/]",
            title="[bold green] Tournament Champion [/]",
            border_style="yellow",
            expand=False,
        )
    )

    # Final standings table
    table = Table(
        title="Final Standings",
        show_header=True,
        header_style="bold",
        border_style="dim",
        show_lines=False,
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Name", min_width=20)
    table.add_column("W", justify="center", width=4)
    table.add_column("D", justify="center", width=4)
    table.add_column("L", justify="center", width=4)
    table.add_column("Pts", justify="right", width=5)

    for i, entry in enumerate(event.final_standings, 1):
        style = "bold yellow" if i == 1 else ""
        table.add_row(
            str(i),
            entry.participant.display_name,
            str(entry.wins),
            str(entry.draws),
            str(entry.losses),
            f"{entry.points:.1f}",
            style=style,
        )

    console.print()
    console.print(table)
    console.print()
