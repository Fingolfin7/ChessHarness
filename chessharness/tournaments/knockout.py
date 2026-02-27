"""
Knock-out (single-elimination) tournament.

Rules:
- Each match is 1 game.  Colour is assigned randomly.
- Lose once → eliminated.
- If N participants is not a power of 2, top seeds receive byes in round 1.
- Draw handling (configurable):
    "rematch"   — play again with colours swapped, repeat until there's a winner.
    "coin_flip" — random advancement, no rematch.
    "seed"      — higher seed (lower number) advances, no rematch.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from dataclasses import replace
from typing import AsyncIterator

from chessharness.config import Config, GameConfig
from chessharness.events import GameOverEvent
from chessharness.game import run_game
from chessharness.tournaments.base import (
    DrawHandling,
    MatchResult,
    PlayerFactory,
    StandingEntry,
    Tournament,
    TournamentParticipant,
)
from chessharness.tournaments.events import (
    MatchCompleteEvent,
    MatchGameEvent,
    MatchStartEvent,
    RoundCompleteEvent,
    RoundStartEvent,
    TournamentCompleteEvent,
    TournamentStartEvent,
    TournamentEvent,
)

logger = logging.getLogger(__name__)


class KnockoutTournament(Tournament):
    """Single-elimination knockout tournament."""

    def __init__(self, draw_handling: DrawHandling = "rematch") -> None:
        self.draw_handling = draw_handling
        self._standings: dict[TournamentParticipant, StandingEntry] = {}
        self._all_results: list[MatchResult] = []

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

    async def run(
        self,
        participants: list[TournamentParticipant],
        config: Config,
        player_factory: PlayerFactory,
    ) -> AsyncIterator[TournamentEvent]:
        if len(participants) < 2:
            raise ValueError("Knockout tournament requires at least 2 participants.")

        # Initialise standings for all participants
        for p in participants:
            self._standings[p] = StandingEntry(participant=p)

        bracket = _build_bracket(participants)
        total_rounds = len(bracket)

        yield TournamentStartEvent(
            tournament_type="knockout",
            participant_names=[p.display_name for p in participants],
            total_rounds=total_rounds,
        )

        survivors = list(participants)

        for round_num, round_pairings in enumerate(bracket, 1):
            # Build the pairing list for this round, using current survivors
            # for non-bye slots.  bracket[round_num-1] gives us (seed_a, seed_b | None).
            match_pairings = _resolve_round_pairings(round_pairings, survivors, round_num)

            pairings_display = [
                (mid, w.display_name, b.display_name if b else "BYE")
                for mid, w, b in match_pairings
            ]
            yield RoundStartEvent(
                round_num=round_num,
                total_rounds=total_rounds,
                pairings=pairings_display,
            )

            round_results: list[MatchResult] = []
            next_survivors: list[TournamentParticipant] = []

            # Run all matches in this round concurrently
            event_queue: asyncio.Queue[TournamentEvent | None] = asyncio.Queue()
            active_count = len(match_pairings)

            tasks = [
                asyncio.create_task(
                    self._run_match(
                        match_id=mid,
                        participant_a=w,
                        participant_b=b,
                        round_num=round_num,
                        config=config,
                        player_factory=player_factory,
                        out_queue=event_queue,
                    )
                )
                for mid, w, b in match_pairings
            ]

            # Drain the shared queue, yielding events as they arrive
            while active_count > 0:
                event = await event_queue.get()
                if event is None:
                    active_count -= 1
                else:
                    yield event

            # Collect results from tasks (each task returns (MatchResult, winner))
            for task in tasks:
                result, winner = await task
                round_results.append(result)
                self._all_results.append(result)
                next_survivors.append(winner)

            survivors = next_survivors

            yield RoundCompleteEvent(
                round_num=round_num,
                results=round_results,
                standings=self.standings(),
            )

        # The sole survivor is the champion
        champion = survivors[0]
        yield TournamentCompleteEvent(
            winner_name=champion.display_name,
            final_standings=self.standings(),
            all_results=self._all_results,
        )

    def standings(self) -> list[StandingEntry]:
        return sorted(
            self._standings.values(),
            key=lambda e: (-e.points, e.participant.seed),
        )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _run_match(
        self,
        match_id: str,
        participant_a: TournamentParticipant,
        participant_b: TournamentParticipant | None,  # None = bye
        round_num: int,
        config: Config,
        player_factory: PlayerFactory,
        out_queue: asyncio.Queue,
    ) -> tuple[MatchResult, TournamentParticipant]:
        """
        Run a single match (with possible rematches on draw).
        Puts TournamentEvents into out_queue; sends None sentinel when done.
        Returns (MatchResult of final deciding game, winning TournamentParticipant).
        """
        try:
            # Handle bye
            if participant_b is None:
                bye_result = MatchResult(
                    match_id=match_id,
                    white=participant_a,
                    black=participant_a,  # placeholder
                    game_result="1-0",
                    pgn="",
                    total_moves=0,
                    winner=participant_a,
                )
                self._standings[participant_a].wins += 1
                await out_queue.put(
                    MatchCompleteEvent(
                        match_id=match_id,
                        result=bye_result,
                        advancing_name=participant_a.display_name,
                        round_num=round_num,
                    )
                )
                return bye_result, participant_a

            # Assign colours randomly for first game
            white, black = _random_colors(participant_a, participant_b)
            game_num = 1

            while True:
                await out_queue.put(
                    MatchStartEvent(
                        match_id=match_id,
                        white_name=white.display_name,
                        black_name=black.display_name,
                        round_num=round_num,
                        game_num=game_num,
                    )
                )

                # Disable PGN auto-save in tournament sub-games; tournament_main handles it
                sub_game_cfg = replace(config.game, save_pgn=False)
                sub_config = replace(config, game=sub_game_cfg)

                white_player = player_factory(white)
                black_player = player_factory(black)

                game_over: GameOverEvent | None = None
                async for game_event in run_game(sub_config, white_player, black_player):
                    await out_queue.put(MatchGameEvent(match_id=match_id, game_event=game_event))
                    if isinstance(game_event, GameOverEvent):
                        game_over = game_event

                if game_over is None:
                    logger.warning("Match %s game %d ended without GameOverEvent", match_id, game_num)
                    # Treat as a draw and fall through to draw handling
                    game_result = "1/2-1/2"
                    winner_participant = None
                else:
                    game_result = game_over.result
                    winner_participant = _determine_winner(game_over, white, black)

                result = MatchResult(
                    match_id=match_id,
                    white=white,
                    black=black,
                    game_result=game_result,
                    pgn=game_over.pgn if game_over else "",
                    total_moves=game_over.total_moves if game_over else 0,
                    winner=winner_participant,
                )

                if winner_participant is not None:
                    # Clear winner — update standings and finish
                    self._standings[winner_participant].wins += 1
                    loser = black if winner_participant is white else white
                    self._standings[loser].losses += 1

                    await out_queue.put(
                        MatchCompleteEvent(
                            match_id=match_id,
                            result=result,
                            advancing_name=winner_participant.display_name,
                            round_num=round_num,
                        )
                    )
                    return result, winner_participant

                # Draw — apply draw_handling
                self._standings[white].draws += 1
                self._standings[black].draws += 1

                if self.draw_handling == "rematch":
                    # Swap colours and play again
                    white, black = black, white
                    game_num += 1
                    logger.info("Match %s draw — rematch (game %d), colours swapped", match_id, game_num)
                    continue

                elif self.draw_handling == "coin_flip":
                    winner_participant = random.choice([participant_a, participant_b])
                    logger.info(
                        "Match %s draw — coin flip → %s advances",
                        match_id,
                        winner_participant.display_name,
                    )
                else:  # "seed"
                    winner_participant = min(participant_a, participant_b, key=lambda p: p.seed)
                    logger.info(
                        "Match %s draw — seed rule → %s (seed %d) advances",
                        match_id,
                        winner_participant.display_name,
                        winner_participant.seed,
                    )

                loser = participant_b if winner_participant is participant_a else participant_a
                self._standings[winner_participant].wins += 1
                self._standings[loser].losses += 1

                final_result = MatchResult(
                    match_id=match_id,
                    white=white,
                    black=black,
                    game_result=game_result,
                    pgn=result.pgn,
                    total_moves=result.total_moves,
                    winner=winner_participant,
                )
                await out_queue.put(
                    MatchCompleteEvent(
                        match_id=match_id,
                        result=final_result,
                        advancing_name=winner_participant.display_name,
                        round_num=round_num,
                    )
                )
                return final_result, winner_participant

        finally:
            await out_queue.put(None)  # sentinel: signals this match is done


# ------------------------------------------------------------------ #
# Bracket helpers                                                     #
# ------------------------------------------------------------------ #

def _next_power_of_two(n: int) -> int:
    return 1 << math.ceil(math.log2(max(n, 2)))


def _build_bracket(
    participants: list[TournamentParticipant],
) -> list[list[tuple[int, int | None]]]:
    """
    Build a single-elimination bracket.

    Returns a list of rounds.  Each round is a list of (seed_a, seed_b) pairs
    where seed_b=None means a bye for seed_a.

    Seeding follows standard single-elimination conventions:
      - Round 1: seed 1 vs seed N, seed 2 vs seed N-1, …
      - Byes are awarded to top seeds when count is not a power of 2.
    """
    n = len(participants)
    slots = _next_power_of_two(n)

    # Place seeds into standard bracket positions
    # Standard bracket: positions [0, slots-1] matched as seed 1 vs seed slots, etc.
    # Byes fill the bottom (highest-numbered) slots
    seeded = [p.seed for p in sorted(participants, key=lambda p: p.seed)]
    bye_count = slots - n
    # Top seeds get byes: seeds 1..bye_count get byes
    # They occupy the first bye_count positions in the bracket
    # We represent this as: seeds 1..bye_count paired with None in round 1
    # remaining seeds paired normally

    rounds = []
    current_slots: list[int | None] = list(range(1, n + 1)) + [None] * bye_count
    # Interleave so seed 1 faces the lowest remaining opponent, etc.
    current_slots = _interleave_seedings(current_slots)

    while len(current_slots) > 1:
        round_pairings = [
            (current_slots[i], current_slots[i + 1])
            for i in range(0, len(current_slots), 2)
        ]
        rounds.append(round_pairings)
        # Winners placeholder — actual advancement done at runtime
        current_slots = [None] * (len(current_slots) // 2)

    return rounds


def _interleave_seedings(slots: list[int | None]) -> list[int | None]:
    """
    Arrange seeds so that seed 1 and seed 2 can only meet in the final,
    following standard bracket seeding (1 vs N, 2 vs N-1 style folding).
    """
    n = len(slots)
    if n <= 1:
        return slots
    result: list[int | None] = [None] * n
    ordered = sorted(slots, key=lambda x: (x is None, x or 0))
    # Place seed 1 at position 0, seed 2 at position n//2, etc.
    half = n // 2
    top = ordered[:half]
    bottom = ordered[half:][::-1]  # reverse so they face each other last
    for i, (t, b) in enumerate(zip(top, bottom)):
        result[i * 2] = t
        result[i * 2 + 1] = b
    return result


def _resolve_round_pairings(
    bracket_round: list[tuple[int, int | None]],
    survivors: list[TournamentParticipant],
    round_num: int,
) -> list[tuple[str, TournamentParticipant, TournamentParticipant | None]]:
    """
    Map bracket seed slots to actual TournamentParticipant objects.

    For round 1 the seeds are known; for later rounds we use the survivors
    list in the order they arrived (winners from left to right in the bracket).
    """
    seed_map = {p.seed: p for p in survivors}
    pairings: list[tuple[str, TournamentParticipant, TournamentParticipant | None]] = []

    if round_num == 1:
        for i, (seed_a, seed_b) in enumerate(bracket_round, 1):
            match_id = f"R{round_num}-M{i}"
            pa = seed_map[seed_a]
            pb = seed_map[seed_b] if seed_b is not None else None
            pairings.append((match_id, pa, pb))
    else:
        # survivors are in bracket order (left to right); pair them up
        for i in range(0, len(survivors), 2):
            match_id = _round_label(round_num, i // 2 + 1, len(survivors) // 2)
            pa = survivors[i]
            pb = survivors[i + 1] if i + 1 < len(survivors) else None
            pairings.append((match_id, pa, pb))

    return pairings


def _round_label(round_num: int, match_num: int, matches_in_round: int) -> str:
    """Return a human-readable match ID."""
    if matches_in_round == 1 and match_num == 1:
        return "F"     # Final
    if matches_in_round == 2:
        return f"SF-{match_num}"   # Semi-final
    if matches_in_round == 4:
        return f"QF-{match_num}"   # Quarter-final
    return f"R{round_num}-M{match_num}"


def _random_colors(
    a: TournamentParticipant, b: TournamentParticipant
) -> tuple[TournamentParticipant, TournamentParticipant]:
    """Return (white, black) with random colour assignment."""
    if random.random() < 0.5:
        return a, b
    return b, a


def _determine_winner(
    event: GameOverEvent,
    white: TournamentParticipant,
    black: TournamentParticipant,
) -> TournamentParticipant | None:
    """Map a GameOverEvent result to a winner participant, or None on draw."""
    if event.result == "1-0":
        return white
    if event.result == "0-1":
        return black
    return None  # draw
