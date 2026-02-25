"""
Conversation logger — writes the full back-and-forth with each model to a text file.

One log file is created per game session, named by timestamp and the two players.
Each API call is recorded as a block showing the messages sent and the raw response.

Log files land in ./logs/ by default (created automatically).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from chessharness.providers.base import Message

_SEP = "=" * 80
_THIN = "-" * 80


class ConversationLogger:
    def __init__(self, log_dir: Path, white_name: str, black_name: str) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Sanitise names for use in filenames
        w = _safe(white_name)
        b = _safe(black_name)
        self._path = log_dir / f"game_{timestamp}_{w}_vs_{b}.log"
        self._write(
            f"{_SEP}\n"
            f"  LLM Chess Harness — Conversation Log\n"
            f"  {white_name} (White) vs {black_name} (Black)\n"
            f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"{_SEP}\n"
        )

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def log_request(
        self,
        *,
        player_name: str,
        color: str,
        move_number: int,
        attempt: int,
        messages: list[Message],
    ) -> None:
        lines: list[str] = [
            f"\n{_SEP}",
            f"  {color.upper()} — {player_name} | Move {move_number} | Attempt {attempt}",
            f"  {datetime.now().strftime('%H:%M:%S')}",
            _SEP,
        ]
        for msg in messages:
            lines.append(f"\n[{msg.role.upper()}]")
            lines.append(msg.content)
            if msg.image_bytes:
                lines.append(f"<image: {len(msg.image_bytes)} bytes>")
        self._write("\n".join(lines) + "\n")

    def log_response(self, *, player_name: str, raw: str) -> None:
        lines = [
            f"\n{_THIN}",
            f"[RESPONSE — {player_name}]",
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
