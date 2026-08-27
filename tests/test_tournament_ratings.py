"""Rating-period integration tests for tournament lifecycles."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chessharness.config import Config, GameConfig, ModelEntry
from chessharness.events import GameOverEvent
from chessharness.players.base import GameState, MoveResponse, Player
from chessharness.ratings.manager import RatingManager
from chessharness.ratings.store import RatingStore
from chessharness.tournaments import KnockoutTournament, RoundRobinTournament
from chessharness.tournaments.base import TournamentParticipant


class TournamentTestPlayer(Player):
    def __init__(self, participant: TournamentParticipant) -> None:
        super().__init__(
            participant.display_name,
            player_type="llm",
            competitor_id=participant.competitor_id,
        )

    async def get_move(self, state: GameState, chunk_queue=None) -> MoveResponse:
        return MoveResponse(raw="e2e4", move="e2e4")


def _participants(count: int) -> list[TournamentParticipant]:
    return [
        TournamentParticipant(
            provider_name="mock",
            model=ModelEntry(id=f"model-{number}", name=f"Model {number}"),
            seed=number,
        )
        for number in range(1, count + 1)
    ]


def _terminal(result: str = "1-0") -> GameOverEvent:
    return GameOverEvent(
        result=result,
        reason="checkmate" if result != "1/2-1/2" else "stalemate",
        winner_name="winner" if result != "1/2-1/2" else None,
        pgn="",
        total_moves=8,
    )


class TournamentRatingPeriods(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = RatingStore(Path(self.temp_dir.name) / "ratings.sqlite3")
        self.config = Config(game=GameConfig(save_pgn=False), providers={})
        self.manager = RatingManager(self.store, self.config)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    async def test_round_robin_uses_one_simultaneous_batch_per_round(self) -> None:
        async def white_wins(config, white, black, stop_event=None):
            yield _terminal()

        tournament = RoundRobinTournament()
        with patch("chessharness.ratings.manager.run_game", new=white_wins):
            events = [
                event
                async for event in tournament.run(
                    _participants(4),
                    self.config,
                    TournamentTestPlayer,
                    rating_manager=self.manager,
                    tournament_id="rr-periods",
                )
            ]

        self.assertTrue(events)
        batches = self.store.list_batches()
        self.assertEqual(len(batches), 6)
        self.assertTrue(all(batch.status == "finalized" for batch in batches))
        for round_num in range(1, 7):
            games = self.store.list_games(
                batch_id=f"tournament:rr-periods:round:{round_num}"
            )
            self.assertEqual(len(games), 2)
            self.assertTrue(all(game.rated for game in games))

    async def test_knockout_rematches_share_one_match_batch(self) -> None:
        outcomes = iter(("1/2-1/2", "1-0"))

        async def draw_then_win(config, white, black, stop_event=None):
            yield _terminal(next(outcomes))

        tournament = KnockoutTournament(draw_handling="rematch")
        with patch("chessharness.ratings.manager.run_game", new=draw_then_win):
            events = [
                event
                async for event in tournament.run(
                    _participants(2),
                    self.config,
                    TournamentTestPlayer,
                    rating_manager=self.manager,
                    tournament_id="ko-rematch",
                )
            ]

        self.assertTrue(events)
        batches = self.store.list_batches()
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].status, "finalized")
        games = self.store.list_games(batch_id=batches[0].batch_id)
        self.assertEqual([game.result for game in games], ["1/2-1/2", "1-0"])

    async def test_knockout_bye_creates_no_rating_batch(self) -> None:
        async def white_wins(config, white, black, stop_event=None):
            yield _terminal()

        tournament = KnockoutTournament(draw_handling="seed")
        with patch("chessharness.ratings.manager.run_game", new=white_wins):
            async for _ in tournament.run(
                _participants(3),
                self.config,
                TournamentTestPlayer,
                rating_manager=self.manager,
                tournament_id="ko-bye",
            ):
                pass

        self.assertEqual(len(self.store.list_games()), 2)
        self.assertEqual(len(self.store.list_batches()), 2)

    async def test_failed_round_finalizes_and_releases_competitor_locks(self) -> None:
        async def broken_game(config, white, black, stop_event=None):
            if False:  # pragma: no cover - keeps this an async generator
                yield _terminal()
            raise RuntimeError("game failed")

        participants = _participants(2)
        tournament = RoundRobinTournament()
        with patch("chessharness.ratings.manager.run_game", new=broken_game):
            with self.assertRaisesRegex(RuntimeError, "game failed"):
                async for _ in tournament.run(
                    participants,
                    self.config,
                    TournamentTestPlayer,
                    rating_manager=self.manager,
                    tournament_id="rr-error",
                ):
                    pass

        batch = self.store.get_batch("tournament:rr-error:round:1")
        self.assertIsNotNone(batch)
        self.assertEqual(batch.status, "finalized")
        self.assertFalse(self.store.list_games()[0].rated)

        players = [TournamentTestPlayer(participant) for participant in participants]
        await self.manager.begin_batch("after-error", players)
        await self.manager.release_batch("after-error")
