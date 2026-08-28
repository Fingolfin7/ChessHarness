import unittest
from unittest.mock import patch

from chessharness.config import Config, GameConfig
from chessharness.web import app as web_app


class WebUiConfigurationTests(unittest.TestCase):
    def test_config_exposes_timeout_and_starting_position(self) -> None:
        cfg = Config(
            game=GameConfig(move_timeout=600, starting_fen="custom-fen"),
            providers={},
            engines={},
        )

        with patch.object(web_app, "config", cfg):
            payload = web_app.get_config()

        self.assertEqual(payload["move_timeout"], 600)
        self.assertEqual(payload["starting_fen"], "custom-fen")
        self.assertEqual(payload["move_timeout_min"], 1)
        self.assertEqual(payload["move_timeout_max"], 3600)
        self.assertIn(120, payload["move_timeout_presets"])

    def test_ui_timeout_is_bounded_without_changing_token_budget(self) -> None:
        cfg = Config(
            game=GameConfig(move_timeout=120, max_output_tokens=5120),
            providers={},
            engines={},
        )

        with patch.object(web_app, "config", cfg):
            bounded = web_app._apply_ui_game_settings(
                {"move_timeout": 99999, "max_output_tokens": 20480}
            )
            lower_bounded = web_app._apply_ui_game_settings({"move_timeout": 0})

        self.assertEqual(bounded.move_timeout, 3600)
        self.assertEqual(bounded.max_output_tokens, 20480)
        self.assertEqual(lower_bounded.move_timeout, 1)


class TournamentMatchFailureStateTests(unittest.TestCase):
    def test_match_failure_updates_match_and_game_snapshots(self) -> None:
        broadcaster = web_app._TournamentBroadcaster()
        broadcaster._apply_payload_to_state(
            {
                "type": "RoundStartEvent",
                "round_num": 1,
                "pairings": [["R1-M1", "Alpha", "Bravo"]],
            }
        )
        broadcaster._apply_payload_to_state(
            {
                "type": "MatchStartEvent",
                "match_id": "R1-M1",
                "white_name": "Alpha",
                "black_name": "Bravo",
                "round_num": 1,
                "game_num": 1,
            }
        )
        broadcaster._apply_payload_to_state(
            {
                "type": "MatchFailedEvent",
                "match_id": "R1-M1",
                "round_num": 1,
                "error": "Engine subprocess unavailable",
                "is_elimination": False,
            }
        )

        match = broadcaster._tournament_state["matches"]["R1-M1"]
        self.assertEqual(match["status"], "failed")
        self.assertFalse(match["thinking"])
        self.assertEqual(match["error"], "Engine subprocess unavailable")

        snapshot = broadcaster.game_snapshot_payload("R1-M1")
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["phase"], "over")
        self.assertFalse(snapshot["thinking"])
        self.assertEqual(snapshot["error"], "Engine subprocess unavailable")


if __name__ == "__main__":
    unittest.main()

