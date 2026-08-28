"""
Abstract Player interface and the GameState snapshot passed to each player per turn.

GameState contains everything a player needs to make a decision - whether that's
an LLM API call, stdin input, a chess engine query, or a remote API call.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
from collections.abc import Iterable

from chessharness.events import Color


logger = logging.getLogger(__name__)
DEFAULT_PLAYER_CLOSE_TIMEOUT = 0.5


@dataclass
class MoveResponse:
    """Returned by Player.get_move(). Carries the raw output, parsed reasoning, and extracted move."""

    raw: str
    move: str
    reasoning: str = ""
    provider_metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class GameState:
    """Immutable snapshot of the game at the start of a player's turn."""

    fen: str
    board_ascii: str
    legal_moves_uci: list[str]
    legal_moves_san: list[str]
    move_history_san: list[str]
    color: Color
    move_number: int

    # Set when board_input == "image" and cairosvg is available
    board_image_bytes: bytes | None = None

    # Populated on retry attempts (attempt_num > 1)
    previous_invalid_move: str | None = None
    previous_error: str | None = None
    attempt_num: int = 1


class Player(ABC):
    """Abstract base class for all chess players."""

    def __init__(
        self,
        name: str,
        player_type: str = "unknown",
        competitor_id: str | None = None,
    ) -> None:
        self.name = name
        self.player_type = player_type
        self.competitor_id = competitor_id or f"{player_type}:{name.strip().casefold()}"
        self.is_rating_anchor = False
        self.anchor_rating: float | None = None

    async def close(self) -> None:
        """Release player-owned resources. Most players have nothing to close."""

    async def force_close(self) -> None:
        """Best-effort resource termination that may bypass normal cleanup locks.

        Most players have no separate forceful shutdown path, so the default
        delegates to ``close``. Players that own a process or another
        cancellation-resistant resource can override this method.
        """
        await self.close()

    @abstractmethod
    async def get_move(
        self,
        state: GameState,
        chunk_queue: asyncio.Queue | None = None,
    ) -> MoveResponse:
        """
        Given the current game state, return a MoveResponse.

        MoveResponse.raw  - the unmodified output (full model text, stdin line, etc.)
        MoveResponse.move - the extracted move string to validate

        The game loop validates move - players may return invalid moves;
        the loop will call get_move() again with retry context.

        Must be async. Implementations may await LLM API calls, stdin reads,
        engine queries, etc.
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"


async def close_players_bounded(
    players: Iterable[Player],
    *,
    timeout: float = DEFAULT_PLAYER_CLOSE_TIMEOUT,
    log: logging.Logger | None = None,
) -> None:
    """Close players without letting a broken SDK stall game teardown.

    The close task is shielded from the timeout and caller cancellation. If
    the deadline expires, cleanup continues in the background and its eventual
    result is consumed/logged. Caller cancellation is still re-raised after the
    cleanup task is detached so cancellation semantics are preserved.
    """

    active_logger = log or logger
    player_list = tuple(players)
    if not player_list:
        return

    async def _close_all() -> list[BaseException | None]:
        async def _close_one(player: Player) -> BaseException | None:
            try:
                await player.close()
            except BaseException as exc:  # cleanup must not cancel siblings
                return exc
            return None

        return await asyncio.gather(
            *(_close_one(player) for player in player_list),
        )

    close_task = asyncio.create_task(_close_all())
    try:
        outcomes = await asyncio.wait_for(
            asyncio.shield(close_task),
            timeout=max(0.0, timeout),
        )
    except TimeoutError:
        active_logger.error(
            "Player cleanup exceeded %.1fs; continuing in background [players=%s]",
            timeout,
            ", ".join(player.name for player in player_list),
        )
        close_task.add_done_callback(
            lambda task: _report_deferred_cleanup(task, active_logger)
        )
        return
    except asyncio.CancelledError:
        # Preserve the independent cleanup task, but never consume the
        # caller's cancellation: tournament stop relies on it propagating.
        active_logger.warning(
            "Player cleanup interrupted; continuing in background [players=%s]",
            ", ".join(player.name for player in player_list),
        )
        close_task.add_done_callback(
            lambda task: _report_deferred_cleanup(task, active_logger)
        )
        raise

    _report_cleanup_outcomes(outcomes, player_list, active_logger)


def _report_deferred_cleanup(
    task: asyncio.Task[list[BaseException | None]],
    log: logging.Logger,
) -> None:
    if task.cancelled():
        return
    try:
        outcomes = task.result()
    except BaseException as exc:
        log.warning("Deferred player cleanup failed: %s", exc)
        return
    # The task result is already consumed, but player names are only needed
    # for the synchronous completion path.  Deferred failures are intentionally
    # logged generically because the task no longer needs to retain player refs.
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            log.warning("Deferred player cleanup failed: %s", outcome)


def _report_cleanup_outcomes(
    outcomes: list[BaseException | None],
    players: tuple[Player, ...],
    log: logging.Logger,
) -> None:
    for player, outcome in zip(players, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning(
                "Player cleanup failed [player=%s type=%s]: %s",
                player.name,
                player.player_type,
                outcome,
            )
