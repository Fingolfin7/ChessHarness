"""Game recording, eligibility, batching, and Glicko-2 persistence."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import AsyncIterator, Iterable
from uuid import uuid4

from chessharness.config import Config
from chessharness.events import GameEvent, GameOverEvent, InvalidMoveEvent, MoveAppliedEvent
from chessharness.game import run_game
from chessharness.players.base import Player
from chessharness.ratings.batch import (
    CompetitorRating,
    RatedGame,
    calculate_rating_batch,
)
from chessharness.ratings.glicko2 import Glicko2Rating
from chessharness.ratings.ruleset import evaluate_ruleset
from chessharness.ratings.store import (
    BatchCommitResult,
    GameRecord,
    RatingChange,
    RatingStore,
)


logger = logging.getLogger(__name__)
_OUTPUT_FAILURES = {
    "empty_model_output",
    "illegal_move",
    "ambiguous_move",
    "unparseable_move",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class RecordedGameOutcome:
    game: GameRecord
    rating_commit: BatchCommitResult | None = None


class RatingConflictError(RuntimeError):
    """Raised when a competitor is already active in another rated batch."""


class RatingManager:
    """Own the durable projection and serialize active competitor batches."""

    def __init__(self, store: RatingStore, config: Config) -> None:
        self.store = store
        self.config = config
        self.projection_id = (
            f"{config.ratings.pool_id}:{config.ratings.algorithm_version}"
        )
        self._batch_guard = asyncio.Lock()
        self._active_competitors: dict[str, str] = {}
        self._batch_members: dict[str, set[str]] = {}

    async def begin_batch(
        self,
        batch_id: str,
        players: Iterable[Player],
        *,
        config: Config | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        player_list = tuple(players)
        effective_config = config or self.config
        ruleset = evaluate_ruleset(
            effective_config.game,
            effective_config.ratings.pool_id,
        )
        async with self._batch_guard:
            for player in player_list:
                owner = self._active_competitors.get(player.competitor_id)
                if owner is not None and owner != batch_id:
                    raise RatingConflictError(
                        f"{player.name} is already active in rated batch {owner}"
                    )
            self.store.create_batch(
                batch_id,
                algorithm_version=self.projection_id,
                metadata=metadata,
                ruleset_id=ruleset.pool_id,
                ruleset_hash=ruleset.ruleset_hash,
            )
            # Finish all durable setup before claiming in-memory locks.  A
            # registration/configuration error can then never strand an
            # active competitor until the process restarts.
            for player in player_list:
                self.store.register_competitor(
                    player.competitor_id,
                    player.name,
                    player.player_type,
                    metadata={
                        "anchor_rating": player.anchor_rating,
                    },
                    is_anchor=player.is_rating_anchor,
                )
                if player.player_type != "human":
                    self.store.ensure_current_rating(
                        player.competitor_id,
                        algorithm_version=self.projection_id,
                        rating=player.anchor_rating or 1500.0,
                        rd=(
                            self.config.ratings.benchmark_rd
                            if player.is_rating_anchor
                            else 350.0
                        ),
                        volatility=0.06,
                    )
            members = self._batch_members.setdefault(batch_id, set())
            for player in player_list:
                members.add(player.competitor_id)
                self._active_competitors[player.competitor_id] = batch_id

    async def release_batch(self, batch_id: str) -> None:
        async with self._batch_guard:
            for competitor_id in self._batch_members.pop(batch_id, set()):
                if self._active_competitors.get(competitor_id) == batch_id:
                    del self._active_competitors[competitor_id]

    async def recorded_game(
        self,
        config: Config,
        white: Player,
        black: Player,
        *,
        batch_id: str,
        game_id: str | None = None,
        stop_event: asyncio.Event | None = None,
        auto_finalize: bool = False,
        metadata: dict[str, object] | None = None,
    ) -> AsyncIterator[GameEvent]:
        """Run, record, and close one game while preserving the event stream."""

        game_uuid = game_id or str(uuid4())
        started_at = _utc_now()
        terminal: GameOverEvent | None = None
        all_attempt_failures: list[dict[str, object]] = []
        current_attempt_failures: list[dict[str, object]] = []
        caught: BaseException | None = None
        record_error: BaseException | None = None
        await self.begin_batch(
            batch_id,
            (white, black),
            config=config,
            metadata=metadata,
        )

        try:
            async for event in run_game(config, white, black, stop_event=stop_event):
                if isinstance(event, InvalidMoveEvent):
                    failure = {
                        "failure_kind": event.failure_kind,
                        "color": event.color,
                        "attempt_num": event.attempt_num,
                        "error": event.error,
                        "provider_metadata": event.provider_metadata,
                    }
                    current_attempt_failures.append(failure)
                    all_attempt_failures.append(failure)
                elif isinstance(event, MoveAppliedEvent):
                    current_attempt_failures = []
                elif isinstance(event, GameOverEvent):
                    terminal = event
                yield event
        except BaseException as exc:
            caught = exc
        finally:
            close_results = await asyncio.gather(
                white.close(),
                black.close(),
                return_exceptions=True,
            )
            for close_result in close_results:
                if isinstance(close_result, BaseException):
                    logger.warning("Player cleanup failed: %s", close_result)

            ruleset = evaluate_ruleset(config.game, config.ratings.pool_id)
            rated, unrated_reason = self._eligibility(
                ruleset.eligible,
                ruleset.reason,
                white,
                black,
                terminal,
                current_attempt_failures,
                caught,
            )
            game_metadata = dict(metadata or {})
            if terminal is not None:
                game_metadata.update(
                    {"pgn": terminal.pgn, "total_moves": terminal.total_moves}
                )
            if caught is not None:
                game_metadata["exception"] = f"{type(caught).__name__}: {caught}"

            result = terminal.result if terminal is not None else "*"
            termination = (
                terminal.reason
                if terminal is not None
                else "engine_error" if any(p.player_type == "engine" for p in (white, black))
                else "interrupted"
            )
            try:
                self.store.record_game(
                    GameRecord(
                        game_uuid=game_uuid,
                        white_competitor_id=white.competitor_id,
                        black_competitor_id=black.competitor_id,
                        result=result,
                        termination=termination,
                        rated=rated,
                        ruleset_id=ruleset.pool_id,
                        ruleset_hash=ruleset.ruleset_hash,
                        unrated_reason=unrated_reason,
                        batch_id=batch_id,
                        attempt_failures=all_attempt_failures,
                        metadata=game_metadata,
                        started_at=started_at,
                        completed_at=_utc_now(),
                    )
                )
            except BaseException as exc:
                record_error = exc

        if record_error is not None:
            if auto_finalize:
                await self.release_batch(batch_id)
            raise record_error
        if caught is not None:
            if auto_finalize:
                await self.finalize_batch(batch_id)
            raise caught
        if auto_finalize:
            await self.finalize_batch(batch_id)

    @staticmethod
    def _eligibility(
        ruleset_eligible: bool,
        ruleset_reason: str | None,
        white: Player,
        black: Player,
        terminal: GameOverEvent | None,
        failures: list[dict[str, object]],
        caught: BaseException | None,
    ) -> tuple[bool, str | None]:
        if not ruleset_eligible:
            return False, ruleset_reason
        if white.player_type == "human" or black.player_type == "human":
            return False, "Games involving a human are not rated"
        if white.competitor_id == black.competitor_id:
            return False, "Self-play is not rated"
        if caught is not None or terminal is None:
            return False, "Game did not reach a clean terminal result"
        if terminal.result not in {"1-0", "0-1", "1/2-1/2"}:
            return False, "Incomplete games are not rated"
        if terminal.reason == "interrupted":
            return False, "Interrupted games are not rated"
        if terminal.reason == "max_retries_exceeded":
            kinds = {str(item.get("failure_kind")) for item in failures}
            if not kinds or not kinds.issubset(_OUTPUT_FAILURES):
                return False, "Retry forfeit included a provider or infrastructure failure"
        return True, None

    async def finalize_batch(self, batch_id: str) -> BatchCommitResult:
        """Apply one simultaneous Glicko-2 period and release competitor locks."""

        try:
            existing_batch = self.store.get_batch(batch_id)
            if existing_batch is not None and existing_batch.status == "finalized":
                return BatchCommitResult(
                    batch=existing_batch,
                    changes=tuple(
                        self.store.get_rating_changes(
                            batch_id,
                            algorithm_version=self.projection_id,
                        )
                    ),
                    idempotent=True,
                )
            games = self.store.list_games(batch_id=batch_id, rated=True)
            rated_games = [
                RatedGame(
                    white_id=game.white_competitor_id,
                    black_id=game.black_competitor_id,
                    result=game.result,  # type: ignore[arg-type]
                )
                for game in games
            ]
            competitor_ids = {
                competitor_id
                for game in games
                for competitor_id in (
                    game.white_competitor_id,
                    game.black_competitor_id,
                )
            }
            snapshot: list[CompetitorRating] = []
            for competitor_id in sorted(competitor_ids):
                competitor = self.store.get_competitor(competitor_id)
                current = self.store.get_current_rating(
                    competitor_id,
                    algorithm_version=self.projection_id,
                )
                if competitor is None or current is None:
                    raise RuntimeError(f"Missing rating state for {competitor_id}")
                snapshot.append(
                    CompetitorRating(
                        competitor_id=competitor_id,
                        rating=Glicko2Rating(
                            current.rating,
                            current.rd,
                            current.volatility,
                        ),
                        games_played=current.games_played,
                        is_anchor=competitor.is_anchor,
                    )
                )

            calculated = calculate_rating_batch(
                snapshot,
                rated_games,
                tau=self.config.ratings.tau,
            )
            changes = [
                RatingChange(
                    competitor_id=competitor_id,
                    algorithm_version=self.projection_id,
                    pre_rating=change.before.rating.rating,
                    pre_rd=change.before.rating.deviation,
                    pre_volatility=change.before.rating.volatility,
                    pre_games_played=change.before.games_played,
                    post_rating=change.after.rating.rating,
                    post_rd=change.after.rating.deviation,
                    post_volatility=change.after.rating.volatility,
                    post_games_played=change.after.games_played,
                )
                for competitor_id, change in calculated.items()
            ]
            return self.store.finalize_batch(batch_id, changes)
        finally:
            await self.release_batch(batch_id)
