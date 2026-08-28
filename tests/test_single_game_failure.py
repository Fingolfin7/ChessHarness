from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from chessharness.config import Config, GameConfig, RatingConfig
from chessharness.events import GameStartEvent, InvalidMoveEvent
from chessharness.providers.base import ProviderError
from chessharness.web import app as web_app


class SingleGameFailureTests(unittest.TestCase):
    def _run_mixed_timeout(
        self,
        *,
        failing_player_type: str,
        attempt_failure_kind: str,
    ) -> dict:
        class FakePlayer:
            def __init__(self, name: str, player_type: str) -> None:
                self.name = name
                self.player_type = player_type
                self.competitor_id = f"{player_type}:{name.casefold()}"

            async def close(self) -> None:
                return None

        class FakeStore:
            @staticmethod
            def get_game(game_id: str):
                return SimpleNamespace(
                    rated=False,
                    unrated_reason="Provider or infrastructure failure",
                )

            @staticmethod
            def get_rating_changes(batch_id: str, *, algorithm_version: str):
                return []

        class FakeManager:
            projection_id = "standard-v1:glicko2-v1"
            store = FakeStore()

            async def recorded_game(self, *args, **kwargs):
                yield GameStartEvent(
                    white_name="Failing",
                    black_name="Opponent",
                    white_player_type=failing_player_type,
                    black_player_type=(
                        "engine" if failing_player_type == "llm" else "llm"
                    ),
                )
                yield InvalidMoveEvent(
                    color="white",
                    attempted_move="",
                    raw_response="",
                    reasoning="",
                    error="request timed out",
                    attempt_num=1,
                    failure_kind=attempt_failure_kind,
                )
                raise ProviderError(
                    "test",
                    "request timed out",
                    kind="timeout",
                )

        opponent_type = "engine" if failing_player_type == "llm" else "llm"
        cfg = Config(
            game=GameConfig(),
            providers={},
            ratings=RatingConfig(enabled=True, database_path="unused.sqlite3"),
        )
        fake_session = SimpleNamespace(
            config=cfg,
            white_player=FakePlayer("Failing", failing_player_type),
            black_player=FakePlayer("Opponent", opponent_type),
        )

        async def fake_build(payload):
            return fake_session

        broadcaster = web_app._SingleGameBroadcaster()
        with (
            patch.object(web_app, "config", cfg),
            patch.object(web_app, "_get_rating_manager", return_value=FakeManager()),
            patch.object(web_app, "_build_single_game_players", fake_build),
        ):
            asyncio.run(broadcaster._run({}, asyncio.Event()))

        return next(
            event
            for event in broadcaster.replay_log()
            if event["type"] == "GameFailureEvent"
        )

    def test_provider_failure_closes_ui_and_publishes_unrated_rating(self) -> None:
        class FakePlayer:
            def __init__(self, name: str, competitor_id: str) -> None:
                self.name = name
                self.player_type = "llm"
                self.competitor_id = competitor_id

            async def close(self) -> None:
                return None

        class FakeStore:
            def __init__(self) -> None:
                self.game_ids: list[str] = []
                self.change_batches: list[str] = []

            def get_game(self, game_id: str):
                self.game_ids.append(game_id)
                return SimpleNamespace(
                    rated=False,
                    unrated_reason="Provider or infrastructure failure",
                )

            def get_rating_changes(self, batch_id: str, *, algorithm_version: str):
                self.change_batches.append(batch_id)
                self.algorithm_version = algorithm_version
                return []

        class FakeManager:
            projection_id = "standard-v1:glicko2-v1"

            def __init__(self) -> None:
                self.store = FakeStore()
                self.call_kwargs: dict = {}

            async def recorded_game(self, *args, **kwargs):
                self.call_kwargs = kwargs
                yield GameStartEvent(
                    white_name="Alpha",
                    black_name="Bravo",
                    white_player_type="llm",
                    black_player_type="llm",
                )
                raise ProviderError("openai", "upstream request timed out", kind="timeout")

        cfg = Config(
            game=GameConfig(),
            providers={},
            ratings=RatingConfig(enabled=True, database_path="unused.sqlite3"),
        )
        fake_manager = FakeManager()
        white = FakePlayer("Alpha", "llm:alpha")
        black = FakePlayer("Bravo", "llm:bravo")
        fake_session = SimpleNamespace(
            config=cfg,
            white_player=white,
            black_player=black,
        )

        async def fake_build(payload):
            return fake_session

        broadcaster = web_app._SingleGameBroadcaster()
        with (
            patch.object(web_app, "config", cfg),
            patch.object(web_app, "_get_rating_manager", return_value=fake_manager),
            patch.object(web_app, "_build_single_game_players", fake_build),
        ):
            asyncio.run(broadcaster._run({}, asyncio.Event()))

        events = broadcaster.replay_log()
        event_types = [event["type"] for event in events]
        self.assertIn("GameFailureEvent", event_types)
        self.assertIn("RatingUpdateEvent", event_types)
        self.assertLess(
            event_types.index("GameFailureEvent"),
            event_types.index("RatingUpdateEvent"),
        )

        failure = next(event for event in events if event["type"] == "GameFailureEvent")
        self.assertEqual(failure["failure_kind"], "timeout")
        self.assertIn("upstream request timed out", failure["error"])
        self.assertEqual(broadcaster._state["phase"], "over")
        self.assertFalse(broadcaster._state["thinking"])
        self.assertEqual(broadcaster._state["error"], failure["error"])
        snapshot = broadcaster.snapshot_payload()
        self.assertEqual(snapshot["phase"], "over")
        self.assertFalse(snapshot["thinking"])
        self.assertEqual(snapshot["error"], failure["error"])

        rating_update = next(
            event for event in events if event["type"] == "RatingUpdateEvent"
        )
        self.assertFalse(rating_update["rated"])
        self.assertEqual(
            rating_update["unrated_reason"],
            "Provider or infrastructure failure",
        )
        self.assertEqual(fake_manager.store.game_ids, [rating_update["game_id"]])
        self.assertEqual(fake_manager.store.change_batches, [rating_update["batch_id"]])

    def test_llm_timeout_against_engine_is_provider_failure_in_ui(self) -> None:
        failure = self._run_mixed_timeout(
            failing_player_type="llm",
            attempt_failure_kind="provider_timeout",
        )

        self.assertEqual(failure["failure_kind"], "timeout")
        self.assertEqual(failure["reason"], "provider_error")

    def test_engine_timeout_is_engine_failure_in_ui(self) -> None:
        failure = self._run_mixed_timeout(
            failing_player_type="engine",
            attempt_failure_kind="engine_error",
        )

        self.assertEqual(failure["failure_kind"], "engine_error")
        self.assertEqual(failure["reason"], "engine_error")


if __name__ == "__main__":
    unittest.main()
