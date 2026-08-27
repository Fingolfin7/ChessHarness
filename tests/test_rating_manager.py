from __future__ import annotations

import asyncio
from pathlib import Path
import unittest

from chessharness.config import Config, GameConfig, RatingConfig
from chessharness.events import GameOverEvent
from chessharness.players.base import GameState, MoveResponse, Player
from chessharness.players.human import QueuedHumanPlayer
from chessharness.providers.base import ProviderError
from chessharness.ratings.manager import RatingConflictError, RatingManager
from chessharness.ratings.store import RatingStore


class InvalidPlayer(Player):
    def __init__(self, name: str, competitor_id: str) -> None:
        super().__init__(name, "llm", competitor_id)
        self.closed = False

    async def get_move(self, state: GameState, chunk_queue=None) -> MoveResponse:
        return MoveResponse(raw="bad", move="bad")

    async def close(self) -> None:
        self.closed = True


class ProviderFailurePlayer(InvalidPlayer):
    async def get_move(self, state: GameState, chunk_queue=None) -> MoveResponse:
        raise ProviderError("test", "temporary outage")


class RatingManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.db_path = Path(f".test-ratings-{id(self)}.sqlite3")
        self.config = Config(
            GameConfig(save_pgn=False),
            {},
            ratings=RatingConfig(database_path=str(self.db_path)),
        )
        self.store = RatingStore(self.db_path)
        self.manager = RatingManager(self.store, self.config)

    def tearDown(self) -> None:
        self.store.close()
        self.db_path.unlink(missing_ok=True)
        Path(f"{self.db_path}-shm").unlink(missing_ok=True)
        Path(f"{self.db_path}-wal").unlink(missing_ok=True)

    async def test_output_forfeit_is_rated_and_players_are_closed(self) -> None:
        white = InvalidPlayer("White", "llm:test:white")
        black = InvalidPlayer("Black", "llm:test:black")

        events = [event async for event in self.manager.recorded_game(
            self.config,
            white,
            black,
            batch_id="single-1",
            game_id="game-1",
            auto_finalize=True,
        )]

        terminal = next(event for event in events if isinstance(event, GameOverEvent))
        self.assertEqual(terminal.result, "0-1")
        game = self.store.get_game("game-1")
        assert game is not None
        self.assertTrue(game.rated)
        self.assertEqual(len(game.attempt_failures), 3)
        self.assertTrue(white.closed)
        self.assertTrue(black.closed)
        white_rating = self.store.get_current_rating(
            white.competitor_id,
            algorithm_version=self.manager.projection_id,
        )
        black_rating = self.store.get_current_rating(
            black.competitor_id,
            algorithm_version=self.manager.projection_id,
        )
        assert white_rating is not None and black_rating is not None
        self.assertLess(white_rating.rating, 1500)
        self.assertGreater(black_rating.rating, 1500)

        retry = await self.manager.finalize_batch("single-1")
        self.assertTrue(retry.idempotent)
        self.assertEqual(len(retry.changes), 2)

    async def test_provider_failure_tainted_forfeit_is_unrated(self) -> None:
        white = ProviderFailurePlayer("White", "llm:test:provider-fail")
        black = InvalidPlayer("Black", "llm:test:opponent")

        _ = [event async for event in self.manager.recorded_game(
            self.config,
            white,
            black,
            batch_id="single-provider-error",
            game_id="game-provider-error",
            auto_finalize=True,
        )]

        game = self.store.get_game("game-provider-error")
        assert game is not None
        self.assertFalse(game.rated)
        self.assertIn("provider", (game.unrated_reason or "").lower())
        self.assertEqual(
            self.store.get_rating_changes("single-provider-error"),
            [],
        )

    async def test_human_and_interrupted_game_is_recorded_unrated(self) -> None:
        human = QueuedHumanPlayer("Human")
        model = InvalidPlayer("Model", "llm:test:model")
        stop = asyncio.Event()
        stop.set()

        _ = [event async for event in self.manager.recorded_game(
            self.config,
            human,
            model,
            batch_id="single-human",
            game_id="game-human",
            stop_event=stop,
            auto_finalize=True,
        )]

        game = self.store.get_game("game-human")
        assert game is not None
        self.assertFalse(game.rated)
        self.assertIn("human", (game.unrated_reason or "").lower())
        self.assertIsNone(
            self.store.get_current_rating(
                human.competitor_id,
                algorithm_version=self.manager.projection_id,
            )
        )

    async def test_overlapping_competitor_batches_are_rejected(self) -> None:
        shared = InvalidPlayer("Shared", "llm:test:shared")
        first = InvalidPlayer("First", "llm:test:first")
        second = InvalidPlayer("Second", "llm:test:second")
        await self.manager.begin_batch("batch-a", (shared, first))

        with self.assertRaises(RatingConflictError):
            await self.manager.begin_batch("batch-b", (shared, second))

        await self.manager.finalize_batch("batch-a")

    async def test_batch_uses_the_effective_game_ruleset(self) -> None:
        white = InvalidPlayer("White", "llm:test:ruleset-white")
        black = InvalidPlayer("Black", "llm:test:ruleset-black")
        custom = Config(
            GameConfig(save_pgn=False, show_legal_moves=False),
            {},
            ratings=self.config.ratings,
        )

        _ = [event async for event in self.manager.recorded_game(
            custom,
            white,
            black,
            batch_id="custom-ruleset",
            game_id="custom-ruleset-game",
            auto_finalize=True,
        )]

        game = self.store.get_game("custom-ruleset-game")
        batch = self.store.get_batch("custom-ruleset")
        assert game is not None and batch is not None
        self.assertEqual(batch.ruleset_hash, game.ruleset_hash)
        self.assertFalse(game.rated)
        self.assertIn("show_legal_moves", game.unrated_reason or "")


if __name__ == "__main__":
    unittest.main()
