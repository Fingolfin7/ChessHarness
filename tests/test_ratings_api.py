from __future__ import annotations

from contextlib import contextmanager
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from chessharness.config import Config, EngineProfile, GameConfig, RatingConfig
from chessharness.events import GameStartEvent
from chessharness.ratings.store import GameRecord, RatingChange
from chessharness.ratings.ruleset import STANDARD_RULESET_HASH
from chessharness.web import app as web_app


class RatingsApiTests(unittest.TestCase):
    def _config(self, database_path: Path, *, enabled: bool = True) -> Config:
        return Config(
            game=GameConfig(),
            providers={},
            engines={},
            ratings=RatingConfig(enabled=enabled, database_path=str(database_path)),
        )

    @contextmanager
    def _using_config(self, cfg: Config):
        with patch.object(web_app, "config", cfg):
            try:
                yield
            finally:
                if web_app._rating_manager is not None:
                    web_app._rating_manager.store.close()
                web_app._rating_manager = None
                web_app._rating_store_signature = None

    @staticmethod
    def _seed_finished_game() -> None:
        manager = web_app._get_rating_manager()
        store = manager.store
        projection = manager.projection_id
        store.register_competitor("llm:alpha", "Alpha", "llm")
        store.register_competitor("llm:bravo", "Bravo", "llm")
        store.ensure_current_rating("llm:alpha", algorithm_version=projection)
        store.ensure_current_rating("llm:bravo", algorithm_version=projection)
        store.create_batch(
            "batch-1",
            algorithm_version=projection,
            ruleset_id="standard-v1",
            ruleset_hash=STANDARD_RULESET_HASH,
        )
        store.record_game(
            GameRecord(
                game_uuid="game-1",
                white_competitor_id="llm:alpha",
                black_competitor_id="llm:bravo",
                result="1-0",
                termination="checkmate",
                rated=True,
                ruleset_id="standard-v1",
                ruleset_hash=STANDARD_RULESET_HASH,
                batch_id="batch-1",
            )
        )
        store.finalize_batch(
            "batch-1",
            [
                RatingChange(
                    competitor_id="llm:alpha",
                    algorithm_version=projection,
                    pre_rating=1500,
                    pre_rd=350,
                    pre_volatility=0.06,
                    pre_games_played=0,
                    post_rating=1662.31,
                    post_rd=290.32,
                    post_volatility=0.05999,
                    post_games_played=1,
                ),
                RatingChange(
                    competitor_id="llm:bravo",
                    algorithm_version=projection,
                    pre_rating=1500,
                    pre_rd=350,
                    pre_volatility=0.06,
                    pre_games_played=0,
                    post_rating=1337.69,
                    post_rd=290.32,
                    post_volatility=0.05999,
                    post_games_played=1,
                ),
            ],
        )

    def test_leaderboard_includes_glicko_state_and_wdl(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cfg = self._config(Path(temp_dir) / "ratings.sqlite3")
            with self._using_config(cfg):
                self._seed_finished_game()
                store = web_app._get_rating_manager().store
                store.create_batch("old-batch", algorithm_version="standard-v1:glicko2-old")
                store.record_game(
                    GameRecord(
                        game_uuid="old-game",
                        white_competitor_id="llm:alpha",
                        black_competitor_id="llm:bravo",
                        result="1-0",
                        termination="checkmate",
                        rated=True,
                        ruleset_id="standard-v1",
                        ruleset_hash="old-ruleset-hash",
                        batch_id="old-batch",
                    )
                )
                store.finalize_batch("old-batch", [])
                store.create_batch(
                    "open-batch",
                    algorithm_version=web_app._get_rating_manager().projection_id,
                    ruleset_id="standard-v1",
                    ruleset_hash=STANDARD_RULESET_HASH,
                )
                store.record_game(
                    GameRecord(
                        game_uuid="open-game",
                        white_competitor_id="llm:alpha",
                        black_competitor_id="llm:bravo",
                        result="1-0",
                        termination="checkmate",
                        rated=True,
                        ruleset_id="standard-v1",
                        ruleset_hash=STANDARD_RULESET_HASH,
                        batch_id="open-batch",
                    )
                )
                with TestClient(web_app.app) as client:
                    response = client.get("/api/ratings")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["enabled"])
            self.assertEqual(payload["pool_id"], "standard-v1")
            self.assertEqual(payload["algorithm_version"], "glicko2-v1")
            self.assertEqual([row["name"] for row in payload["ratings"]], ["Alpha", "Bravo"])
            alpha = payload["ratings"][0]
            self.assertAlmostEqual(alpha["rating"], 1662.31)
            self.assertAlmostEqual(alpha["rd"], 290.32)
            self.assertAlmostEqual(alpha["volatility"], 0.05999)
            self.assertEqual(alpha["games"], 1)
            self.assertEqual((alpha["wins"], alpha["draws"], alpha["losses"]), (1, 0, 0))
            self.assertTrue(alpha["is_provisional"])
            self.assertAlmostEqual(alpha["rating_change"], 162.31)

    def test_history_returns_before_and_after_states(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cfg = self._config(Path(temp_dir) / "ratings.sqlite3")
            with self._using_config(cfg):
                self._seed_finished_game()
                with TestClient(web_app.app) as client:
                    response = client.get("/api/ratings/llm%3Aalpha/history")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["competitor_id"], "llm:alpha")
            self.assertEqual(payload["current"]["games"], 1)
            self.assertEqual(len(payload["history"]), 1)
            self.assertEqual(payload["history"][0]["batch_id"], "batch-1")
            self.assertAlmostEqual(payload["history"][0]["rating_before"], 1500)
            self.assertAlmostEqual(payload["history"][0]["rating_after"], 1662.31)

    def test_config_exposes_engine_and_rating_availability(self) -> None:
        with TemporaryDirectory() as temp_dir:
            engine = EngineProfile(path="stockfish-custom")
            cfg = Config(
                game=GameConfig(),
                providers={},
                engines={engine.id: engine},
                ratings=RatingConfig(database_path=str(Path(temp_dir) / "ratings.sqlite3")),
            )
            with self._using_config(cfg):
                with patch.object(web_app.shutil, "which", return_value=None):
                    payload = web_app.get_config()

            self.assertTrue(payload["ratings"]["enabled"])
            self.assertEqual(payload["ratings"]["algorithm_version"], "glicko2-v1")
            self.assertEqual(payload["engines"][0]["uci_elo"], 1600)
            self.assertFalse(payload["engines"][0]["available"])

    def test_tournament_rejects_an_unavailable_engine_profile(self) -> None:
        with TemporaryDirectory() as temp_dir:
            engine = EngineProfile(path="missing-stockfish")
            cfg = Config(
                game=GameConfig(),
                providers={},
                engines={engine.id: engine},
                ratings=RatingConfig(database_path=str(Path(temp_dir) / "ratings.sqlite3")),
            )
            payload = {
                "tournament_type": "round_robin",
                "participants": [
                    {"provider": "engine", "model_id": engine.id, "name": engine.name},
                    {"provider": "mock", "model_id": "model", "name": "Model"},
                ],
            }
            with self._using_config(cfg), patch.object(web_app.shutil, "which", return_value=None):
                web_app._tournament_broadcaster.status = {"state": "idle"}
                with TestClient(web_app.app) as client:
                    response = client.post("/api/tournament/start", json=payload)

            self.assertEqual(response.status_code, 400)
            self.assertIn("unavailable", response.json()["detail"].lower())

    def test_disabled_ratings_do_not_create_database(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "disabled.sqlite3"
            cfg = self._config(database_path, enabled=False)
            with self._using_config(cfg):
                with TestClient(web_app.app) as client:
                    response = client.get("/api/ratings")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["ratings"], [])
            self.assertFalse(database_path.exists())

    def test_unknown_history_returns_404(self) -> None:
        with TemporaryDirectory() as temp_dir:
            cfg = self._config(Path(temp_dir) / "ratings.sqlite3")
            with self._using_config(cfg):
                with TestClient(web_app.app) as client:
                    response = client.get("/api/ratings/missing/history")

            self.assertEqual(response.status_code, 404)

    def test_single_game_broadcaster_records_and_emits_rating_update(self) -> None:
        class FakeStore:
            @staticmethod
            def get_game(game_id):
                return SimpleNamespace(rated=True, unrated_reason=None)

            @staticmethod
            def get_rating_changes(batch_id, *, algorithm_version):
                return [
                    SimpleNamespace(
                        competitor_id="llm:alpha",
                        pre_rating=1500.0,
                        post_rating=1512.5,
                        pre_rd=350.0,
                        post_rd=310.0,
                        post_volatility=0.06,
                        post_games_played=1,
                    )
                ]

        class FakeManager:
            projection_id = "standard-v1:glicko2-v1"
            store = FakeStore()

            async def recorded_game(self, *args, **kwargs):
                self.call_kwargs = kwargs
                yield GameStartEvent(white_name="Alpha", black_name="Bravo")

        fake_manager = FakeManager()
        fake_session = SimpleNamespace(
            config=Config(game=GameConfig(), providers={}),
            white_player=object(),
            black_player=object(),
        )

        async def fake_build(payload):
            return fake_session

        broadcaster = web_app._SingleGameBroadcaster()
        with (
            patch.object(web_app, "_get_rating_manager", return_value=fake_manager),
            patch.object(web_app, "_build_single_game_players", fake_build),
        ):
            asyncio.run(broadcaster._run({}, asyncio.Event()))

        self.assertEqual(fake_manager.call_kwargs["metadata"]["source"], "web-single-game")
        self.assertTrue(fake_manager.call_kwargs["auto_finalize"])
        update = broadcaster.replay_log()[-1]
        self.assertEqual(update["type"], "RatingUpdateEvent")
        self.assertTrue(update["rated"])
        self.assertAlmostEqual(update["changes"][0]["rating_change"], 12.5)

    def test_tournament_broadcaster_passes_the_shared_rating_manager(self) -> None:
        class FakeTournament:
            async def run(self, *args, **kwargs):
                self.kwargs = kwargs
                if False:  # pragma: no cover - preserve async-generator shape
                    yield None

        tournament = FakeTournament()
        manager = object()
        broadcaster = web_app._TournamentBroadcaster()
        cfg = Config(game=GameConfig(), providers={})

        with patch.object(web_app, "_get_rating_manager", return_value=manager):
            asyncio.run(broadcaster._run([], cfg, object(), tournament))

        self.assertIs(tournament.kwargs["rating_manager"], manager)
        self.assertTrue(tournament.kwargs["tournament_id"])


if __name__ == "__main__":
    unittest.main()
