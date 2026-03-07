"""
HumanPlayer implementations.

HumanPlayer uses stdin for CLI play.
QueuedHumanPlayer exposes an async submit_move() API so a web session can
wait for a browser move without coupling the core game loop to WebSocket code.
"""

from __future__ import annotations

import asyncio

from chessharness.players.base import Player, GameState, MoveResponse


class HumanPlayer(Player):
    def __init__(self, name: str) -> None:
        super().__init__(name=name, player_type="human")

    async def get_move(self, state: GameState, chunk_queue: asyncio.Queue | None = None) -> MoveResponse:
        legal_preview = ", ".join(state.legal_moves_san[:10])
        if len(state.legal_moves_san) > 10:
            legal_preview += f" ... ({len(state.legal_moves_san)} total)"

        prompt = (
            f"\n[{state.color.upper()}] Your move (SAN or UCI) "
            f"[legal: {legal_preview}]: "
        )
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, input, prompt)
        move = raw.strip()
        return MoveResponse(raw=raw, move=move)


class QueuedHumanPlayer(Player):
    """Human player backed by an async queue populated by the web layer."""

    def __init__(self, name: str) -> None:
        super().__init__(name=name, player_type="human")
        self._moves: asyncio.Queue[str] = asyncio.Queue()

    def submit_move(self, move: str) -> None:
        self._moves.put_nowait(move)

    async def get_move(self, state: GameState, chunk_queue: asyncio.Queue | None = None) -> MoveResponse:
        raw = await self._moves.get()
        move = raw.strip()
        return MoveResponse(raw=raw, move=move)
