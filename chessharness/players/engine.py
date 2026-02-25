"""
EnginePlayer stub — UCI chess engine opponent (e.g., Stockfish).

Implementation outline (not yet wired):
  1. Launch engine: transport, engine = await chess.engine.popen_uci("stockfish")
  2. Reconstruct board from FEN
  3. result = await engine.play(board, chess.engine.Limit(time=1.0))
  4. return result.move.uci()
  5. Manage engine lifecycle via close() / async context manager

The engine process should be started once and reused across moves,
not re-launched every turn.
"""

from __future__ import annotations

from chessharness.players.base import Player, GameState, MoveResponse


class EnginePlayer(Player):
    """
    Stub for a UCI chess engine player.

    Args:
        name: Display name.
        engine_path: Path to the engine binary (default: "stockfish" on PATH).
        think_time: Seconds per move (default: 1.0).
    """

    def __init__(
        self,
        name: str,
        engine_path: str = "stockfish",
        think_time: float = 1.0,
    ) -> None:
        super().__init__(name)
        self._engine_path = engine_path
        self._think_time = think_time

    async def get_move(self, state: GameState) -> MoveResponse:
        raise NotImplementedError(
            "EnginePlayer is not yet implemented. "
            "See the docstring in engine.py for the implementation outline."
        )

    async def close(self) -> None:
        """Shut down the engine process cleanly."""
        pass
