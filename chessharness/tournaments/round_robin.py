"""Double round-robin tournament.

Every participant plays every other participant twice, once with each colour.
Games in the same round run concurrently; rounds run sequentially.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import TYPE_CHECKING, AsyncIterator
from uuid import uuid4

from chessharness.config import Config
from chessharness.events import GameOverEvent
from chessharness.game import run_game
from chessharness.players.base import (
    DEFAULT_PLAYER_CLOSE_TIMEOUT,
    close_players_bounded,
)
from chessharness.tournaments.base import (
    MatchResult,
    PlayerFactory,
    StandingEntry,
    Tournament,
    TournamentMatchError,
    TournamentParticipant,
)
from chessharness.tournaments.events import (
    MatchCompleteEvent,
    MatchFailedEvent,
    MatchGameEvent,
    MatchStartEvent,
    RoundCompleteEvent,
    RoundStartEvent,
    TournamentCompleteEvent,
    TournamentEvent,
    TournamentStartEvent,
)

if TYPE_CHECKING:
    from chessharness.ratings.manager import RatingManager

logger = logging.getLogger(__name__)

Pairing = tuple[TournamentParticipant, TournamentParticipant]


class RoundRobinTournament(Tournament):
    """A double round robin with one point for a win and half for a draw."""

    def __init__(self) -> None:
        self._standings: dict[TournamentParticipant, StandingEntry] = {}
        self._all_results: list[MatchResult] = []

    async def run(
        self,
        participants: list[TournamentParticipant],
        config: Config,
        player_factory: PlayerFactory,
        *,
        rating_manager: RatingManager | None = None,
        tournament_id: str | None = None,
    ) -> AsyncIterator[TournamentEvent]:
        if len(participants) < 2:
            raise ValueError("Round-robin tournament requires at least 2 participants.")

        self._standings = {
            participant: StandingEntry(participant=participant)
            for participant in participants
        }
        self._all_results = []

        schedule = _build_schedule(participants)
        total_rounds = len(schedule)
        run_id = tournament_id or str(uuid4())

        yield TournamentStartEvent(
            tournament_type="round_robin",
            participant_names=[p.display_name for p in participants],
            total_rounds=total_rounds,
        )

        for round_num, round_pairings in enumerate(schedule, 1):
            rating_batch_id = f"tournament:{run_id}:round:{round_num}"
            identified_pairings = [
                (f"R{round_num}-M{match_num}", white, black)
                for match_num, (white, black) in enumerate(round_pairings, 1)
            ]
            yield RoundStartEvent(
                round_num=round_num,
                total_rounds=total_rounds,
                pairings=[
                    (match_id, white.display_name, black.display_name)
                    for match_id, white, black in identified_pairings
                ],
            )

            event_queue: asyncio.Queue[TournamentEvent | None] = asyncio.Queue()
            tasks = [
                asyncio.create_task(
                    self._run_match(
                        match_id=match_id,
                        white=white,
                        black=black,
                        round_num=round_num,
                        config=config,
                        player_factory=player_factory,
                        out_queue=event_queue,
                        rating_manager=rating_manager,
                        rating_batch_id=rating_batch_id,
                        tournament_id=run_id,
                    )
                )
                for match_id, white, black in identified_pairings
            ]

            primary_error: BaseException | None = None
            try:
                active_count = len(tasks)
                while active_count:
                    event = await event_queue.get()
                    if event is None:
                        active_count -= 1
                    else:
                        yield event
                        if isinstance(event, MatchFailedEvent):
                            # The match puts this event in the queue before
                            # re-raising.  Raise now so a failed match cannot
                            # leave the round waiting on unrelated siblings.
                            raise TournamentMatchError(
                                event.match_id,
                                event.round_num,
                                event.error,
                            )

                round_results = list(await asyncio.gather(*tasks))
            except BaseException as exc:
                primary_error = exc
                raise
            finally:
                unfinished = [task for task in tasks if not task.done()]
                for task in unfinished:
                    task.cancel()
                # Gather every task, including the one that failed, so its
                # exception is retrieved and no task is left orphaned.
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                if rating_manager is not None:
                    try:
                        await _finalize_rating_batch(rating_manager, rating_batch_id)
                    except BaseException:
                        if primary_error is None:
                            raise
                        logger.exception(
                            "Rating finalization also failed for round %d",
                            round_num,
                        )

            self._all_results.extend(round_results)

            yield RoundCompleteEvent(
                round_num=round_num,
                results=round_results,
                standings=self.standings(),
            )

        final_standings = self.standings()
        yield TournamentCompleteEvent(
            winner_name=final_standings[0].participant.display_name,
            final_standings=final_standings,
            all_results=list(self._all_results),
        )

    def standings(self) -> list[StandingEntry]:
        # Wins are the first tie-break, then the original seed provides a stable
        # deterministic result when players remain level.
        return sorted(
            self._standings.values(),
            key=lambda entry: (
                -entry.points,
                -entry.wins,
                entry.participant.seed,
            ),
        )

    async def _run_match(
        self,
        match_id: str,
        white: TournamentParticipant,
        black: TournamentParticipant,
        round_num: int,
        config: Config,
        player_factory: PlayerFactory,
        out_queue: asyncio.Queue[TournamentEvent | None],
        rating_manager: RatingManager | None,
        rating_batch_id: str,
        tournament_id: str,
    ) -> MatchResult:
        try:
            await out_queue.put(
                MatchStartEvent(
                    match_id=match_id,
                    white_name=white.display_name,
                    black_name=black.display_name,
                    round_num=round_num,
                )
            )

            sub_config = replace(config, game=replace(config.game, save_pgn=False))
            white_player = player_factory(white)
            black_player = player_factory(black)
            game_over: GameOverEvent | None = None
            game_events = (
                rating_manager.recorded_game(
                    sub_config,
                    white_player,
                    black_player,
                    batch_id=rating_batch_id,
                    game_id=f"{rating_batch_id}:match:{match_id}",
                    auto_finalize=False,
                        metadata={
                            "tournament_id": tournament_id,
                            "tournament_type": "round_robin",
                            "round_num": round_num,
                            "match_id": match_id,
                        },
                )
                if rating_manager is not None
                else run_game(sub_config, white_player, black_player)
            )
            try:
                async for game_event in game_events:
                    await out_queue.put(MatchGameEvent(match_id=match_id, game_event=game_event))
                    if isinstance(game_event, GameOverEvent):
                        game_over = game_event
            finally:
                await close_players_bounded(
                    (
                        player
                        for player in (white_player, black_player)
                        if callable(getattr(player, "close", None))
                    ),
                    timeout=DEFAULT_PLAYER_CLOSE_TIMEOUT,
                    log=logger,
                )

            if game_over is None:
                logger.warning("Match %s ended without GameOverEvent; recording a draw", match_id)
                game_result = "1/2-1/2"
                winner = None
                pgn = ""
                total_moves = 0
            else:
                game_result = game_over.result
                winner = _winner_for_result(game_result, white, black)
                pgn = game_over.pgn
                total_moves = game_over.total_moves

            if winner is white:
                self._standings[white].wins += 1
                self._standings[black].losses += 1
            elif winner is black:
                self._standings[black].wins += 1
                self._standings[white].losses += 1
            else:
                self._standings[white].draws += 1
                self._standings[black].draws += 1

            result = MatchResult(
                match_id=match_id,
                white=white,
                black=black,
                game_result=game_result,
                pgn=pgn,
                total_moves=total_moves,
                winner=winner,
            )
            await out_queue.put(
                MatchCompleteEvent(
                    match_id=match_id,
                    result=result,
                    advancing_name=winner.display_name if winner else "",
                    round_num=round_num,
                    is_elimination=False,
                )
            )
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await out_queue.put(
                MatchFailedEvent(
                    match_id=match_id,
                    round_num=round_num,
                    error=f"{type(exc).__name__}: {exc}",
                    is_elimination=False,
                )
            )
            raise
        finally:
            await out_queue.put(None)


async def _finalize_rating_batch(
    rating_manager: RatingManager,
    batch_id: str,
) -> None:
    """Finalize a started period, or release it if setup failed pre-batch."""

    from chessharness.ratings.store import BatchNotFoundError

    try:
        await rating_manager.finalize_batch(batch_id)
    except BatchNotFoundError:
        await rating_manager.release_batch(batch_id)


def _build_schedule(participants: list[TournamentParticipant]) -> list[list[Pairing]]:
    """Build a double round-robin schedule using the circle method.

    For an odd field, a ``None`` rotation slot represents the bye. Bye entries
    are omitted from the returned rounds because they do not award points.
    The second leg mirrors the first with every colour assignment reversed.
    """
    if len(participants) < 2:
        return []

    rotation: list[TournamentParticipant | None] = list(participants)
    if len(rotation) % 2:
        rotation.append(None)

    first_leg: list[list[Pairing]] = []
    for round_index in range(len(rotation) - 1):
        round_pairings: list[Pairing] = []
        for index in range(len(rotation) // 2):
            left = rotation[index]
            right = rotation[-index - 1]
            if left is None or right is None:
                continue
            if round_index % 2:
                round_pairings.append((right, left))
            else:
                round_pairings.append((left, right))
        first_leg.append(round_pairings)
        rotation = [rotation[0], rotation[-1], *rotation[1:-1]]

    second_leg = [
        [(black, white) for white, black in round_pairings]
        for round_pairings in first_leg
    ]
    return first_leg + second_leg


def _winner_for_result(
    result: str,
    white: TournamentParticipant,
    black: TournamentParticipant,
) -> TournamentParticipant | None:
    if result == "1-0":
        return white
    if result == "0-1":
        return black
    return None
