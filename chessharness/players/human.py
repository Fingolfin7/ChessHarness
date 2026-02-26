"""
HumanPlayer — reads moves from stdin.

Uses run_in_executor so that the blocking input() call doesn't stall
the asyncio event loop (important once a web UI is added).

The board is already displayed by cli/display.py before get_move() is called,
so we only need to prompt for the move itself.
"""

from __future__ import annotations

import asyncio

from chessharness.players.base import Player, GameState, MoveResponse


class HumanPlayer(Player):
    async def get_move(self, state: GameState, chunk_queue: asyncio.Queue | None = None) -> MoveResponse:
        legal_preview = ", ".join(state.legal_moves_san[:10])
        if len(state.legal_moves_san) > 10:
            legal_preview += f" … ({len(state.legal_moves_san)} total)"

        prompt = (
            f"\n[{state.color.upper()}] Your move (UCI, e.g. e2e4) "
            f"[legal: {legal_preview}]: "
        )
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, input, prompt)
        move = raw.strip().lower()
        return MoveResponse(raw=move, move=move)
