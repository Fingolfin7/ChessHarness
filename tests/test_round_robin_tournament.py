"""Tests for double round-robin scheduling, scoring, and events."""

from __future__ import annotations

import asyncio
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

from chessharness.config import Config, GameConfig, ModelEntry
from chessharness.events import GameOverEvent
from chessharness.tournaments import RoundRobinTournament, TournamentParticipant
from chessharness.tournaments.base import TournamentMatchError
from chessharness.tournaments.events import (
    MatchCompleteEvent,
    MatchFailedEvent,
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


class _HangingClosePlayer:
    player_type = "llm"

    def __init__(self, name: str) -> None:
        self.name = name
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self.closed = asyncio.Event()

    async def close(self) -> None:
        self.close_started.set()
        await self.release_close.wait()
        self.closed.set()


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

    async def test_match_failure_is_reported_without_waiting_for_siblings(self):
        """A failed match must stop the round while another match is blocked."""
        participants = make_participants(4)
        sibling_cancelled = asyncio.Event()

        async def failing_or_blocked_game(config, white, black):
            if {white.name, black.name} == {"A", "D"}:
                raise RuntimeError("provider exploded")
            try:
                await asyncio.Event().wait()
            finally:
                sibling_cancelled.set()
            yield  # pragma: no cover - keeps this an async generator

        tournament = RoundRobinTournament()
        events = []
        with patch(
            "chessharness.tournaments.round_robin.run_game",
            new=failing_or_blocked_game,
        ):
            stream = tournament.run(
                participants,
                TEST_CONFIG,
                lambda p: SimpleNamespace(name=p.display_name),
            )
            events.extend([await stream.__anext__(), await stream.__anext__()])

            with self.assertRaisesRegex(TournamentMatchError, r"R1-M1.*provider exploded"):
                while True:
                    event = await asyncio.wait_for(stream.__anext__(), timeout=1)
                    events.append(event)
                    if isinstance(event, MatchFailedEvent):
                        # The failure event is delivered before the generator
                        # raises the tournament-level error.
                        await stream.__anext__()

            await asyncio.wait_for(sibling_cancelled.wait(), timeout=1)

        self.assertTrue(any(isinstance(event, MatchFailedEvent) for event in events))

    async def test_cancellation_is_bounded_when_player_close_hangs(self) -> None:
        participants = make_participants(2)
        game_started = asyncio.Event()
        players: list[_HangingClosePlayer] = []

        async def blocked_game(config, white, black):
            game_started.set()
            await asyncio.Future()
            yield  # pragma: no cover - keeps this an async generator

        def player_factory(participant):
            player = _HangingClosePlayer(participant.display_name)
            players.append(player)
            return player

        tournament = RoundRobinTournament()

        async def consume() -> None:
            async for _ in tournament.run(participants, TEST_CONFIG, player_factory):
                pass

        with (
            patch("chessharness.tournaments.round_robin.run_game", new=blocked_game),
            patch(
                "chessharness.tournaments.round_robin.DEFAULT_PLAYER_CLOSE_TIMEOUT",
                0.01,
            ),
        ):
            task = asyncio.create_task(consume())
            await asyncio.wait_for(game_started.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=0.5)

        self.assertEqual(len(players), 2)
        self.assertTrue(all(player.close_started.is_set() for player in players))
        for player in players:
            player.release_close.set()
        await asyncio.gather(
            *(asyncio.wait_for(player.closed.wait(), timeout=1) for player in players)
        )
