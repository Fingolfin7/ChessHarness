"""
Tests for KnockoutTournament — bracket generation, bye logic, advancement,
and draw handling.  Uses a MockPlayer that instantly returns the first legal
move so no LLM calls are made.
"""

from __future__ import annotations

import asyncio
import math
import unittest

from chessharness.config import Config, GameConfig, ModelEntry
from chessharness.players.base import GameState, MoveResponse, Player
from chessharness.tournaments import KnockoutTournament, TournamentParticipant
from chessharness.tournaments.base import DrawHandling
from chessharness.tournaments.events import (
    MatchCompleteEvent,
    MatchStartEvent,
    RoundCompleteEvent,
    RoundStartEvent,
    TournamentCompleteEvent,
    TournamentStartEvent,
)
from chessharness.tournaments.knockout import (
    _build_bracket,
    _next_power_of_two,
    _resolve_round_pairings,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def make_participant(name: str, seed: int) -> TournamentParticipant:
    return TournamentParticipant(
        provider_name="mock",
        model=ModelEntry(id=f"mock-{name.lower()}", name=name),
        seed=seed,
    )


def make_participants(n: int) -> list[TournamentParticipant]:
    names = [
        "Alpha", "Bravo", "Charlie", "Delta",
        "Echo", "Foxtrot", "Golf", "Hotel",
        "India", "Juliet", "Kilo", "Lima",
        "Mike", "November", "Oscar", "Papa",
    ]
    return [make_participant(names[i], i + 1) for i in range(n)]


class MockPlayer(Player):
    """Returns the first legal move immediately, no LLM involved."""

    async def get_move(self, state: GameState, chunk_queue=None) -> MoveResponse:
        move = state.legal_moves_uci[0]
        return MoveResponse(raw=move, move=move, reasoning="")


def mock_player_factory(p: TournamentParticipant) -> Player:
    return MockPlayer(name=p.display_name)


FAST_GAME_CONFIG = Config(
    game=GameConfig(
        max_retries=1,
        board_input="text",
        show_legal_moves=True,
        move_timeout=30,
        save_pgn=False,
    ),
    providers={},
)


async def collect_events(tournament, participants, config=FAST_GAME_CONFIG):
    """Run a tournament and collect all events."""
    events = []
    async for event in tournament.run(participants, config, mock_player_factory):
        events.append(event)
    return events


# --------------------------------------------------------------------------- #
# Bracket generation                                                           #
# --------------------------------------------------------------------------- #

class TestBracketGeneration:
    def test_next_power_of_two(self):
        assert _next_power_of_two(2) == 2
        assert _next_power_of_two(3) == 4
        assert _next_power_of_two(4) == 4
        assert _next_power_of_two(5) == 8
        assert _next_power_of_two(8) == 8
        assert _next_power_of_two(9) == 16

    def test_bracket_two_players(self):
        participants = make_participants(2)
        bracket = _build_bracket(participants)
        assert len(bracket) == 1   # only a final
        assert len(bracket[0]) == 1  # one match

    def test_bracket_four_players(self):
        participants = make_participants(4)
        bracket = _build_bracket(participants)
        assert len(bracket) == 2   # SF + F
        assert len(bracket[0]) == 2  # two semi-finals
        assert len(bracket[1]) == 1  # one final

    def test_bracket_eight_players(self):
        participants = make_participants(8)
        bracket = _build_bracket(participants)
        assert len(bracket) == 3   # QF + SF + F
        assert len(bracket[0]) == 4

    def test_bracket_has_byes_for_non_power_of_two(self):
        participants = make_participants(3)
        bracket = _build_bracket(participants)
        # 3 participants → padded to 4 slots → 1 bye
        assert len(bracket) == 2
        round1 = bracket[0]
        none_slots = [b for _, b in round1 if b is None]
        assert len(none_slots) == 1

    def test_bracket_byes_go_to_top_seeds(self):
        participants = make_participants(5)  # 5 → 8 slots → 3 byes
        bracket = _build_bracket(participants)
        round1 = bracket[0]
        bye_seeds = [a for a, b in round1 if b is None]
        # Top 3 seeds should receive byes
        for seed in bye_seeds:
            assert seed <= 3

    def test_bracket_six_players(self):
        participants = make_participants(6)  # 6 → 8 slots → 2 byes
        bracket = _build_bracket(participants)
        round1 = bracket[0]
        byes = [b for _, b in round1 if b is None]
        assert len(byes) == 2


# --------------------------------------------------------------------------- #
# Tournament run — event sequence                                              #
# --------------------------------------------------------------------------- #

class TestKnockoutEventSequence(unittest.IsolatedAsyncioTestCase):
    async def test_two_player_event_order(self):
        participants = make_participants(2)
        tournament = KnockoutTournament(draw_handling="seed")
        events = await collect_events(tournament, participants)

        types = [type(e).__name__ for e in events]
        self.assertEqual(types[0], "TournamentStartEvent")
        self.assertEqual(types[-1], "TournamentCompleteEvent")
        self.assertIn("RoundStartEvent", types)
        self.assertIn("MatchStartEvent", types)
        self.assertIn("MatchCompleteEvent", types)
        self.assertIn("RoundCompleteEvent", types)

    async def test_tournament_start_event(self):
        participants = make_participants(2)
        tournament = KnockoutTournament(draw_handling="seed")
        events = await collect_events(tournament, participants)
        start = next(e for e in events if isinstance(e, TournamentStartEvent))
        self.assertEqual(start.tournament_type, "knockout")
        self.assertEqual(len(start.participant_names), 2)
        self.assertEqual(start.total_rounds, 1)

    async def test_four_player_total_rounds(self):
        participants = make_participants(4)
        tournament = KnockoutTournament(draw_handling="seed")
        events = await collect_events(tournament, participants)
        start = next(e for e in events if isinstance(e, TournamentStartEvent))
        self.assertEqual(start.total_rounds, 2)

        round_starts = [e for e in events if isinstance(e, RoundStartEvent)]
        self.assertEqual(len(round_starts), 2)

    async def test_complete_event_has_winner(self):
        participants = make_participants(2)
        tournament = KnockoutTournament(draw_handling="seed")
        events = await collect_events(tournament, participants)
        complete = next(e for e in events if isinstance(e, TournamentCompleteEvent))
        self.assertIn(complete.winner_name, {p.display_name for p in participants})

    async def test_match_complete_advancing_name(self):
        participants = make_participants(2)
        tournament = KnockoutTournament(draw_handling="seed")
        events = await collect_events(tournament, participants)
        complete = next(e for e in events if isinstance(e, TournamentCompleteEvent))
        match_complete = next(e for e in events if isinstance(e, MatchCompleteEvent))
        self.assertEqual(match_complete.advancing_name, complete.winner_name)

    async def test_standings_populated_after_run(self):
        participants = make_participants(4)
        tournament = KnockoutTournament(draw_handling="seed")
        await collect_events(tournament, participants)
        standings = tournament.standings()
        self.assertEqual(len(standings), 4)
        self.assertGreater(standings[0].points, 0)

    async def test_eight_player_correct_match_count(self):
        participants = make_participants(8)
        tournament = KnockoutTournament(draw_handling="seed")
        events = await collect_events(tournament, participants)
        match_starts = [e for e in events if isinstance(e, MatchStartEvent)]
        # 8-player KO: 4 QF + 2 SF + 1 F = 7 matches (no rematches with "seed" handling)
        self.assertEqual(len(match_starts), 7)


# --------------------------------------------------------------------------- #
# Bye handling                                                                 #
# --------------------------------------------------------------------------- #

class TestByeHandling(unittest.IsolatedAsyncioTestCase):
    async def test_three_players_one_bye(self):
        participants = make_participants(3)
        tournament = KnockoutTournament(draw_handling="seed")
        events = await collect_events(tournament, participants)

        complete = next(e for e in events if isinstance(e, TournamentCompleteEvent))
        self.assertIn(complete.winner_name, {p.display_name for p in participants})

    async def test_five_players_three_byes(self):
        participants = make_participants(5)
        tournament = KnockoutTournament(draw_handling="seed")
        events = await collect_events(tournament, participants)

        complete = next(e for e in events if isinstance(e, TournamentCompleteEvent))
        self.assertIn(complete.winner_name, {p.display_name for p in participants})

    async def test_bye_participant_advances_without_game(self):
        participants = make_participants(3)
        tournament = KnockoutTournament(draw_handling="seed")
        events = await collect_events(tournament, participants)

        round1_start = next(
            e for e in events
            if isinstance(e, RoundStartEvent) and e.round_num == 1
        )
        bye_pairings = [p for p in round1_start.pairings if p[2] == "BYE"]
        self.assertEqual(len(bye_pairings), 1)


# --------------------------------------------------------------------------- #
# Draw handling                                                                #
# --------------------------------------------------------------------------- #

class TestDrawHandling(unittest.IsolatedAsyncioTestCase):
    async def test_seed_draw_handling_produces_winner(self):
        """seed draw handling should always produce a winner without rematch."""
        participants = make_participants(2)
        tournament = KnockoutTournament(draw_handling="seed")
        events = await collect_events(tournament, participants)
        complete = next(e for e in events if isinstance(e, TournamentCompleteEvent))
        self.assertTrue(complete.winner_name)

    async def test_coin_flip_draw_handling(self):
        participants = make_participants(2)
        tournament = KnockoutTournament(draw_handling="coin_flip")
        events = await collect_events(tournament, participants)
        complete = next(e for e in events if isinstance(e, TournamentCompleteEvent))
        self.assertIn(complete.winner_name, {p.display_name for p in participants})


# --------------------------------------------------------------------------- #
# Minimum participants validation                                              #
# --------------------------------------------------------------------------- #

class TestValidation(unittest.IsolatedAsyncioTestCase):
    async def test_single_participant_raises(self):
        participants = make_participants(1)
        tournament = KnockoutTournament()
        with self.assertRaisesRegex(ValueError, "at least 2"):
            async for _ in tournament.run(participants, FAST_GAME_CONFIG, mock_player_factory):
                pass

    async def test_empty_participants_raises(self):
        tournament = KnockoutTournament()
        with self.assertRaisesRegex(ValueError, "at least 2"):
            async for _ in tournament.run([], FAST_GAME_CONFIG, mock_player_factory):
                pass


# --------------------------------------------------------------------------- #
# TournamentParticipant hashability                                           #
# --------------------------------------------------------------------------- #

class TestParticipantHashability:
    def test_participant_usable_as_dict_key(self):
        p1 = make_participant("Alpha", 1)
        p2 = make_participant("Bravo", 2)
        d = {p1: "a", p2: "b"}
        assert d[p1] == "a"
        assert d[p2] == "b"

    def test_same_participant_equal(self):
        p1 = make_participant("Alpha", 1)
        p2 = make_participant("Alpha", 1)
        assert p1 == p2
        assert hash(p1) == hash(p2)

    def test_different_seed_not_equal(self):
        p1 = make_participant("Alpha", 1)
        p2 = make_participant("Alpha", 2)
        assert p1 != p2
