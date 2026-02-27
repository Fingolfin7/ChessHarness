"""
Interactive participant selection for tournament mode.

Extends the model-selection pattern from cli/selector.py:
users pick models one at a time until they're done (minimum 2).
"""

from __future__ import annotations

from rich.console import Console
from rich.prompt import IntPrompt, Prompt
from rich.table import Table

from chessharness.cli.selector import PlayerSelection, _print_model_table
from chessharness.config import Config
from chessharness.tournaments.base import DrawHandling, TournamentParticipant
from chessharness.tournaments.events import TournamentType

console = Console(legacy_windows=False)


def select_tournament_participants(config: Config) -> list[TournamentParticipant]:
    """
    Display available models and let the user pick an arbitrary number of
    participants (min 2).  Returns them as TournamentParticipant objects
    with seeds assigned in selection order (first picked = seed 1).
    """
    entries = config.all_models()
    if not entries:
        raise ValueError("No models available. Check your config.yaml providers section.")

    _print_model_table(entries)

    console.print(
        "\n[bold]Tournament participant selection[/]\n"
        "  Enter a model number to add it to the tournament.\n"
        "  Press [bold]Enter[/] with no input when you're done (minimum 2).\n"
    )

    choices = [str(i) for i in range(1, len(entries) + 1)]
    selections: list[PlayerSelection] = []

    while True:
        prompt = f"  Add participant #{len(selections) + 1}"
        if len(selections) >= 2:
            prompt += " (or Enter to finish)"

        raw = Prompt.ask(prompt, default="", show_default=False)
        if raw.strip() == "":
            if len(selections) < 2:
                console.print("  [red]Need at least 2 participants.[/]")
                continue
            break

        if raw not in choices:
            console.print(f"  [red]Invalid choice. Enter a number between 1 and {len(entries)}.[/]")
            continue

        idx = int(raw) - 1
        provider_name, model = entries[idx]
        sel = PlayerSelection(provider_name=provider_name, model=model)
        selections.append(sel)
        console.print(f"  [green]✓[/] Added [bold]{sel.display_name}[/] (seed {len(selections)})")

    participants = [
        TournamentParticipant(
            provider_name=sel.provider_name,
            model=sel.model,
            seed=i,
        )
        for i, sel in enumerate(selections, 1)
    ]

    _print_participant_summary(participants)
    return participants


def select_tournament_settings() -> tuple[TournamentType, DrawHandling]:
    """
    Prompt the user to choose tournament type and draw-handling strategy.
    Returns (tournament_type, draw_handling).
    """
    console.print()

    # Tournament type
    console.print("[bold]Tournament format:[/]")
    console.print("  1. Knock-out (single elimination)")
    console.print("  2. Round Robin  [dim](not yet implemented)[/]")
    console.print("  3. Swiss        [dim](not yet implemented)[/]")
    console.print("  4. Arena        [dim](not yet implemented)[/]")

    type_choice = IntPrompt.ask("\nSelect format", choices=["1", "2", "3", "4"], default=1)
    type_map: dict[int, TournamentType] = {
        1: "knockout",
        2: "round_robin",
        3: "swiss",
        4: "arena",
    }
    tournament_type = type_map[type_choice]

    if tournament_type != "knockout":
        console.print(f"  [yellow]{tournament_type} is not yet implemented.[/]")
        raise SystemExit(1)

    # Draw handling (knockout only for now)
    console.print("\n[bold]Draw handling:[/]")
    console.print("  1. Rematch with colours swapped  [dim](default)[/]")
    console.print("  2. Coin flip")
    console.print("  3. Higher seed advances")

    draw_choice = IntPrompt.ask("Select", choices=["1", "2", "3"], default=1)
    draw_map: dict[int, DrawHandling] = {1: "rematch", 2: "coin_flip", 3: "seed"}
    draw_handling = draw_map[draw_choice]

    console.print()
    return tournament_type, draw_handling


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _print_participant_summary(participants: list[TournamentParticipant]) -> None:
    table = Table(
        title="Tournament Line-up",
        show_header=True,
        header_style="bold",
        border_style="green",
        show_lines=False,
    )
    table.add_column("Seed", style="dim", width=5, justify="right")
    table.add_column("Name", min_width=20)
    table.add_column("Provider", style="dim", min_width=12)
    table.add_column("Model ID", style="dim")

    for p in participants:
        table.add_row(str(p.seed), p.display_name, p.provider_name, p.model.id)

    n = len(participants)
    import math
    slots = 1 << math.ceil(math.log2(max(n, 2)))
    byes = slots - n

    console.print()
    console.print(table)
    if byes:
        console.print(
            f"  [dim]ℹ  {byes} bye(s) will be awarded to the top {byes} seed(s) in round 1.[/]"
        )
    console.print()
