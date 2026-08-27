"""Tests for double round-robin scheduling, scoring, and events."""

from __future__ import annotations

import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

from chessharness.config import Config, GameConfig, ModelEntry
from chessharness.events import GameOverEvent
from chessharness.tournaments import RoundRobinTournament, TournamentParticipant
from chessharness.tournaments.events import (
    MatchCompleteEvent,
    MatchStartEvent,
    RoundCompleteEvent,
    RoundStartEvent,
    TournamentCompleteEvent,
    TournamentStartEvent,
)
from chessharness.tournaments.round_robin import _build_schedule


def make_participants(count: int) -> list[TournamentParticipant]:
    return [
        TournamentParticipant(
            provider_name="mock",
            model=ModelEntry(id=f"model-{index}", name=chr(64 + index)),
            seed=index,
        )
        for index in range(1, count + 1)
    ]


TEST_CONFIG = Config(game=GameConfig(save_pgn=False), providers={})


class TestRoundRobinSchedule:
    def test_four_players_play_every_opponent_twice_with_swapped_colours(self):
        participants = make_participants(4)
        schedule = _build_schedule(participants)

        assert len(schedule) == 6
        assert all(len(round_pairings) == 2 for round_pairings in schedule)

        unordered = Counter(
            frozenset((white.seed, black.seed))
            for round_pairings in schedule
            for white, black in round_pairings
        )
        assert len(unordered) == 6
        assert set(unordered.values()) == {2}

        ordered = Counter(
            (white.seed, black.seed)
            for round_pairings in schedule
            for white, black in round_pairings
        )
        for first in participants:
            for second in participants:
                if first is not second:
                    assert ordered[(first.seed, second.seed)] == 1

    def test_odd_field_has_one_bye_per_player_per_leg(self):
        participants = make_participants(3)
        schedule = _build_schedule(participants)

        assert len(schedule) == 6
        assert all(len(round_pairings) == 1 for round_pairings in schedule)
        appearances = Counter(
            participant.seed
            for round_pairings in schedule
            for pairing in round_pairings
            for participant in pairing
        )
        assert appearances == Counter({1: 4, 2: 4, 3: 4})


class TestRoundRobinRun(unittest.IsolatedAsyncioTestCase):
    async def test_events_results_and_standings(self):
        participants = make_participants(3)

        async def fake_run_game(config, white, black):
            if white.name == "A":
                result, winner = "1-0", "A"
            elif black.name == "A":
                result, winner = "0-1", "A"
            else:
                result, winner = "1/2-1/2", None
            yield GameOverEvent(
                result=result,
                reason="draw" if winner is None else "checkmate",
                winner_name=winner,
                pgn=f"[White \"{white.name}\"]\n[Black \"{black.name}\"]",
                total_moves=12,
            )

        tournament = RoundRobinTournament()
        events = []
        with patch("chessharness.tournaments.round_robin.run_game", new=fake_run_game):
            async for event in tournament.run(
                participants,
                TEST_CONFIG,
                lambda p: SimpleNamespace(name=p.display_name),
            ):
                events.append(event)

        start = next(event for event in events if isinstance(event, TournamentStartEvent))
        self.assertEqual(start.tournament_type, "round_robin")
        self.assertEqual(start.total_rounds, 6)
        self.assertEqual(len([e for e in events if isinstance(e, RoundStartEvent)]), 6)
        self.assertEqual(len([e for e in events if isinstance(e, RoundCompleteEvent)]), 6)
        self.assertEqual(len([e for e in events if isinstance(e, MatchStartEvent)]), 6)

        match_completions = [e for e in events if isinstance(e, MatchCompleteEvent)]
        self.assertEqual(len(match_completions), 6)
        self.assertTrue(all(not event.is_elimination for event in match_completions))

        complete = next(event for event in events if isinstance(event, TournamentCompleteEvent))
        self.assertEqual(complete.winner_name, "A")
        self.assertEqual(len(complete.all_results), 6)

        standings = tournament.standings()
        self.assertEqual([entry.participant.display_name for entry in standings], ["A", "B", "C"])
        self.assertEqual((standings[0].wins, standings[0].draws, standings[0].losses), (4, 0, 0))
        self.assertEqual(standings[0].points, 4.0)
        self.assertEqual((standings[1].wins, standings[1].draws, standings[1].losses), (0, 2, 2))

    async def test_draws_award_half_point_to_each_player(self):
        participants = make_participants(2)

        async def drawn_game(config, white, black):
            yield GameOverEvent(
                result="1/2-1/2",
                reason="stalemate",
                winner_name=None,
                pgn="",
                total_moves=20,
            )

        tournament = RoundRobinTournament()
        with patch("chessharness.tournaments.round_robin.run_game", new=drawn_game):
            async for _ in tournament.run(participants, TEST_CONFIG, lambda p: object()):
                pass

        standings = tournament.standings()
        self.assertEqual([entry.points for entry in standings], [1.0, 1.0])
        self.assertEqual([entry.draws for entry in standings], [2, 2])
        self.assertEqual(standings[0].participant.seed, 1)

    async def test_requires_two_participants(self):
        tournament = RoundRobinTournament()
        with self.assertRaisesRegex(ValueError, "at least 2"):
            async for _ in tournament.run(make_participants(1), TEST_CONFIG, lambda p: object()):
                pass
