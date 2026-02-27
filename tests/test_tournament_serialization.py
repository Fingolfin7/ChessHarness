"""
Tests for _to_json_dict — the serialiser that converts tournament event
dataclasses to JSON-safe dicts and injects a "type" key at every level of
nesting.

The critical invariant: game_event nested inside MatchGameEvent must carry its
own "type" field so the frontend reducer can dispatch on it.  Before the fix,
only the outer dict had "type"; the inner game_event was a plain dict with no
"type", so switch(gameEvent.type) always hit `default` and boards never updated.
"""

import dataclasses
import unittest

from chessharness.web import app as web_app


# ── Local test dataclasses (no production imports needed) ──────────────────

@dataclasses.dataclass(frozen=True)
class _Inner:
    x: int
    label: str


@dataclasses.dataclass(frozen=True)
class _Outer:
    name: str
    inner: _Inner


@dataclasses.dataclass(frozen=True)
class _WithList:
    items: list


# ── Tests ──────────────────────────────────────────────────────────────────

class ToJsonDictTests(unittest.TestCase):

    def test_adds_type_to_top_level(self):
        obj = _Outer(name="hello", inner=_Inner(x=1, label="a"))
        result = web_app._to_json_dict(obj)
        self.assertEqual(result["type"], "_Outer")

    def test_adds_type_to_nested_dataclass(self):
        """The fix: nested dataclasses must also get a 'type' key."""
        obj = _Outer(name="hello", inner=_Inner(x=42, label="z"))
        result = web_app._to_json_dict(obj)
        self.assertIn("inner", result)
        self.assertEqual(result["inner"]["type"], "_Inner")

    def test_nested_scalar_fields_preserved(self):
        obj = _Outer(name="hello", inner=_Inner(x=99, label="q"))
        result = web_app._to_json_dict(obj)
        self.assertEqual(result["name"], "hello")
        self.assertEqual(result["inner"]["x"], 99)
        self.assertEqual(result["inner"]["label"], "q")

    def test_list_of_dataclasses_each_get_type(self):
        obj = _WithList(items=[_Inner(x=1, label="a"), _Inner(x=2, label="b")])
        result = web_app._to_json_dict(obj)
        self.assertEqual(result["items"][0]["type"], "_Inner")
        self.assertEqual(result["items"][1]["type"], "_Inner")
        self.assertEqual(result["items"][0]["x"], 1)
        self.assertEqual(result["items"][1]["x"], 2)

    def test_list_of_scalars_unchanged(self):
        @dataclasses.dataclass(frozen=True)
        class _Plain:
            nums: list
        obj = _Plain(nums=[1, 2, 3])
        result = web_app._to_json_dict(obj)
        self.assertEqual(result["nums"], [1, 2, 3])

    def test_tuple_treated_as_list(self):
        @dataclasses.dataclass(frozen=True)
        class _T:
            pair: tuple
        obj = _T(pair=(_Inner(x=5, label="x"), _Inner(x=6, label="y")))
        result = web_app._to_json_dict(obj)
        self.assertIsInstance(result["pair"], list)
        self.assertEqual(result["pair"][0]["type"], "_Inner")

    # ── Real event types ──────────────────────────────────────────────────

    def test_match_game_event_game_event_has_type(self):
        """
        The actual production scenario: MatchGameEvent wraps a GameEvent.
        The serialised game_event dict must contain "type" so the JS reducer
        can dispatch on it (e.g. "MoveAppliedEvent").
        """
        from chessharness.events import MoveAppliedEvent
        from chessharness.tournaments.events import MatchGameEvent

        move_event = MoveAppliedEvent(
            color="white",
            move_uci="e2e4",
            move_san="e4",
            raw_response="e2e4",
            reasoning="",
            fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
            board_ascii_after="",
            is_check=False,
            move_number=1,
        )
        wrapper = MatchGameEvent(match_id="r1-m1", game_event=move_event)

        result = web_app._to_json_dict(wrapper)

        self.assertEqual(result["type"], "MatchGameEvent")
        self.assertEqual(result["match_id"], "r1-m1")

        game_event = result["game_event"]
        self.assertEqual(game_event["type"], "MoveAppliedEvent",
                         "game_event must carry its own 'type' — this was the board-update bug")
        self.assertEqual(game_event["move_uci"], "e2e4")
        self.assertEqual(game_event["move_san"], "e4")
        self.assertEqual(game_event["color"], "white")

    def test_tournament_start_event_serialised(self):
        from chessharness.tournaments.events import TournamentStartEvent
        evt = TournamentStartEvent(
            tournament_type="knockout",
            participant_names=["Alpha", "Bravo"],
            total_rounds=1,
        )
        result = web_app._to_json_dict(evt)
        self.assertEqual(result["type"], "TournamentStartEvent")
        self.assertEqual(result["tournament_type"], "knockout")
        self.assertEqual(result["participant_names"], ["Alpha", "Bravo"])
        self.assertEqual(result["total_rounds"], 1)
        # timestamp should be present (serialised by json.dumps default=str later)
        self.assertIn("timestamp", result)
