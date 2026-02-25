"""
Conversation logger — writes the full back-and-forth with one model to a text file.

Two loggers are created per game (one per player), both using the same game_id
so their filenames sort together:

    logs/game_20260225_143000_white_GPT4o.log
    logs/game_20260225_143000_black_ClaudeOpus.log

Each API call is recorded as a block showing the messages sent and the raw response.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from chessharness.providers.base import Message

_SEP = "=" * 80
_THIN = "-" * 80


class ConversationLogger:
    def __init__(
        self,
        log_dir: Path,
        game_id: str,
        player_name: str,
        color: str,
    ) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        self._player_name = player_name
        safe = _safe(player_name)
        self._path = log_dir / f"game_{game_id}_{color}_{safe}.log"
        self._write(
            f"{_SEP}\n"
            f"  LLM Chess Harness — Conversation Log\n"
            f"  {player_name} playing {color.upper()}\n"
            f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"{_SEP}\n"
        )

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def log_request(
        self,
        *,
        color: str,
        move_number: int,
        attempt: int,
        messages: list[Message],
    ) -> None:
        lines: list[str] = [
            f"\n{_SEP}",
            f"  {color.upper()} | Move {move_number} | Attempt {attempt}",
            f"  {datetime.now().strftime('%H:%M:%S')}",
            _SEP,
        ]
        for msg in messages:
            lines.append(f"\n[{msg.role.upper()}]")
            lines.append(msg.content)
            if msg.image_bytes:
                lines.append(f"<image: {len(msg.image_bytes)} bytes>")
        self._write("\n".join(lines) + "\n")

    def log_response(self, *, raw: str) -> None:
        lines = [
            f"\n{_THIN}",
            "[RESPONSE]",
            raw if raw else "(empty)",
            _THIN,
        ]
        self._write("\n".join(lines) + "\n")

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _write(self, text: str) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(text)

    @property
    def path(self) -> Path:
        return self._path


def _safe(name: str) -> str:
    """Strip characters that are problematic in filenames."""
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip()
