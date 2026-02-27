"""
Integration tests for the tournament WebSocket broadcaster.

Covers two scenarios critical to resilience:

1. Replay on (re)connect — a client that connects after events have already
   been emitted receives the full log and can reconstruct state, just as a
   reconnecting browser would.

2. Nested-type serialisation — MatchGameEvent payloads in the log carry
   game_event.type so the frontend reducer can dispatch correctly.

Uses FastAPI's synchronous TestClient for WebSocket testing (no external
server required).
"""

import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from chessharness.events import GameStartEvent, MoveAppliedEvent
from chessharness.tournaments.events import (
    MatchGameEvent,
    TournamentStartEvent,
)
from chessharness.web import app as web_app


def _make_replay_log():
    """Build the kind of event log the broadcaster accumulates during a run."""
    start = web_app._to_json_dict(
        TournamentStartEvent(
            tournament_type="knockout",
            participant_names=["Alpha", "Bravo"],
            total_rounds=1,
        )
    )
    game_start = web_app._to_json_dict(
        MatchGameEvent(
            match_id="r1-m1",
            game_event=GameStartEvent(white_name="Alpha", black_name="Bravo"),
        )
    )
    move = web_app._to_json_dict(
        MatchGameEvent(
            match_id="r1-m1",
            game_event=MoveAppliedEvent(
                color="white",
                move_uci="e2e4",
                move_san="e4",
                raw_response="e2e4",
                reasoning="",
                fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
                board_ascii_after="",
                is_check=False,
                move_number=1,
            ),
        )
    )
    return [start, game_start, move]


class TournamentReplayTests(unittest.TestCase):

    def test_late_subscriber_receives_full_replay(self):
        """
        A client that connects after events were emitted should immediately
        receive all past events (the replay log), in order.
        """
        log = _make_replay_log()

        with patch.object(web_app._tournament_broadcaster, '_tournament_log', list(log)):
            with TestClient(web_app.app) as client:
                with client.websocket_connect("/ws/tournament") as ws:
                    received = []
                    for _ in range(len(log)):
                        received.append(ws.receive_json())

        self.assertEqual(len(received), 3)
        self.assertEqual(received[0]["type"], "TournamentStartEvent")
        self.assertEqual(received[1]["type"], "MatchGameEvent")
        self.assertEqual(received[2]["type"], "MatchGameEvent")

    def test_replayed_match_game_event_has_nested_type(self):
        """
        The fix for boards not updating: replayed MatchGameEvent payloads must
        include game_event.type so the JS reducer can dispatch on it.
        """
        log = _make_replay_log()

        with patch.object(web_app._tournament_broadcaster, '_tournament_log', list(log)):
            with TestClient(web_app.app) as client:
                with client.websocket_connect("/ws/tournament") as ws:
                    ws.receive_json()   # TournamentStartEvent
                    ws.receive_json()   # MatchGameEvent(GameStartEvent)
                    move_payload = ws.receive_json()  # MatchGameEvent(MoveAppliedEvent)

        self.assertEqual(move_payload["type"], "MatchGameEvent")
        game_event = move_payload["game_event"]
        self.assertIn("type", game_event,
                      "game_event must have a 'type' key for the JS reducer to dispatch")
        self.assertEqual(game_event["type"], "MoveAppliedEvent")
        self.assertEqual(game_event["move_uci"], "e2e4")
        self.assertEqual(game_event["color"], "white")

    def test_per_match_game_replay_log(self):
        """
        The per-match replay endpoint (/ws/tournament/game/{match_id}) should
        replay the game-event subset for that match only.
        """
        # Build a game-level log (already unwrapped — these are raw GameEvent dicts)
        game_start = web_app._to_json_dict(
            GameStartEvent(white_name="Alpha", black_name="Bravo")
        )
        move = web_app._to_json_dict(
            MoveAppliedEvent(
                color="white",
                move_uci="d2d4",
                move_san="d4",
                raw_response="d2d4",
                reasoning="",
                fen_after="rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq d3 0 1",
                board_ascii_after="",
                is_check=False,
                move_number=1,
            )
        )
        game_log = {"r1-m1": [game_start, move]}

        with patch.object(web_app._tournament_broadcaster, '_game_log', game_log):
            with TestClient(web_app.app) as client:
                with client.websocket_connect("/ws/tournament/game/r1-m1") as ws:
                    evt1 = ws.receive_json()
                    evt2 = ws.receive_json()

        self.assertEqual(evt1["type"], "GameStartEvent")
        self.assertEqual(evt2["type"], "MoveAppliedEvent")
        self.assertEqual(evt2["move_uci"], "d2d4")

    def test_reconnect_simulation_state_is_reconstructable(self):
        """
        Simulates a reconnect: state built from replayed events should match
        what it would have been if the client had been connected from the start.

        Uses the same reducer logic as useTournamentSocket to verify Python-side.
        """
        log = _make_replay_log()

        # Replay all events through a simple Python dict reducer
        state = {"status": "idle", "matches": {}}

        for evt in log:
            if evt["type"] == "TournamentStartEvent":
                state = {
                    "status": "running",
                    "matches": {},
                    "participants": evt["participant_names"],
                }
            elif evt["type"] == "MatchGameEvent":
                match_id = evt["match_id"]
                ge = evt["game_event"]
                if ge["type"] == "GameStartEvent":
                    state["matches"][match_id] = {
                        "fen": "start",
                        "plies": [],
                        "white": ge["white_name"],
                        "black": ge["black_name"],
                    }
                elif ge["type"] == "MoveAppliedEvent":
                    if match_id in state["matches"]:
                        m = state["matches"][match_id]
                        m["fen"] = ge["fen_after"]
                        m["plies"].append(ge["move_san"])

        self.assertEqual(state["status"], "running")
        self.assertIn("r1-m1", state["matches"])
        match = state["matches"]["r1-m1"]
        self.assertEqual(match["white"], "Alpha")
        self.assertEqual(match["fen"],
                         "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1")
        self.assertEqual(match["plies"], ["e4"])
