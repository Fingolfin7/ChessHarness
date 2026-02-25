"""
Interactive model selection for White and Black at game start.

Displays a numbered table of all configured models and prompts the user
to pick one for each colour.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.table import Table
from rich.prompt import IntPrompt

from chessharness.config import Config, ModelEntry

console = Console(legacy_windows=False)


@dataclass
class PlayerSelection:
    provider_name: str
    model: ModelEntry

    @property
    def display_name(self) -> str:
        return self.model.name


def select_players(config: Config) -> tuple[PlayerSelection, PlayerSelection]:
    """
    Display all available models and prompt the user to assign White and Black.

    Returns a (white, black) tuple of PlayerSelection.
    """
    entries = config.all_models()

    if not entries:
        raise ValueError("No models available. Check your config.yaml providers section.")

    _print_model_table(entries)

    choices = [str(i) for i in range(1, len(entries) + 1)]

    white_idx = IntPrompt.ask(
        "\n[bold white]♔  Who plays White?[/]",
        choices=choices,
        show_choices=False,
    )
    black_idx = IntPrompt.ask(
        "[bold bright_black]♚  Who plays Black?[/]",
        choices=choices,
        show_choices=False,
    )

    white_provider, white_model = entries[white_idx - 1]
    black_provider, black_model = entries[black_idx - 1]

    white = PlayerSelection(provider_name=white_provider, model=white_model)
    black = PlayerSelection(provider_name=black_provider, model=black_model)

    console.print(
        f"\n  White: [bold]{white.display_name}[/]  vs  Black: [bold]{black.display_name}[/]\n"
    )

    return white, black


def _print_model_table(entries: list[tuple[str, ModelEntry]]) -> None:
    table = Table(
        title="Available Models",
        show_header=True,
        header_style="bold",
        border_style="dim",
        show_lines=False,
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Name", min_width=20)
    table.add_column("Provider", style="dim", min_width=12)
    table.add_column("Model ID", style="dim")

    for i, (provider_name, model) in enumerate(entries, 1):
        table.add_row(str(i), model.name, provider_name, model.id)

    console.print()
    console.print(table)
