import unittest

from chessharness.config import Config, GameConfig
from chessharness.events import GameStartEvent, InvalidMoveEvent
from chessharness.game import run_game
from chessharness.players.base import GameState, MoveResponse, Player


class ScriptedPlayer(Player):
    def __init__(self, name: str, competitor_id: str, moves: list[str]) -> None:
        super().__init__(name, player_type="llm", competitor_id=competitor_id)
        self.moves = iter(moves)

    async def get_move(self, state: GameState, chunk_queue=None) -> MoveResponse:
        move = next(self.moves)
        return MoveResponse(raw=move, move=move)


class RatingEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_game_start_carries_stable_identity_and_player_type(self) -> None:
        white = ScriptedPlayer("Same", "llm:a:model", ["e4"])
        black = ScriptedPlayer("Same", "llm:b:model", ["e5"])
        stop = __import__("asyncio").Event()
        stop.set()

        events = [event async for event in run_game(
            Config(GameConfig(save_pgn=False), {}), white, black, stop
        )]

        start = events[0]
        assert isinstance(start, GameStartEvent)
        assert start.white_competitor_id == "llm:a:model"
        assert start.black_competitor_id == "llm:b:model"
        assert start.white_player_type == "llm"
        assert start.black_player_type == "llm"

    async def test_invalid_move_has_machine_readable_failure_kind(self) -> None:
        white = ScriptedPlayer("White", "llm:a:model", ["not-a-move"])
        black = ScriptedPlayer("Black", "llm:b:model", ["e5"])
        config = Config(GameConfig(max_retries=1, save_pgn=False), {})

        events = [event async for event in run_game(config, white, black)]

        invalid = next(event for event in events if isinstance(event, InvalidMoveEvent))
        assert invalid.failure_kind == "unparseable_move"


if __name__ == "__main__":
    unittest.main()
