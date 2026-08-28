"""
Async game loop - the core orchestrator.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from pathlib import Path
from typing import AsyncGenerator

import chess

from chessharness.board import ChessBoard
from chessharness.config import Config
from chessharness.events import (
    CheckEvent,
    GameEvent,
    GameOverEvent,
    GameStartEvent,
    InvalidMoveEvent,
    MoveAppliedEvent,
    MoveRequestedEvent,
    ReasoningChunkEvent,
    TurnStartEvent,
)
from chessharness.players.base import GameState, Player
from chessharness.providers.base import ProviderError
from chessharness.renderer import is_png_available, render_ascii, render_png

logger = logging.getLogger(__name__)
_MOVE_CANCEL_GRACE_SECONDS = 0.5


async def run_game(
    config: Config,
    white_player: Player,
    black_player: Player,
    stop_event: asyncio.Event | None = None,
) -> AsyncGenerator[GameEvent, None]:
    """
    Run a complete chess game, yielding events for every significant action.

    The generator completes when the game ends normally (checkmate, stalemate,
    draw, retries exhausted) or when stop_event is set (user interruption).
    """
    board = ChessBoard(fen=config.game.starting_fen or None)
    board.set_players(white_player.name, black_player.name)

    use_images = config.game.board_input == "image" and is_png_available()

    yield GameStartEvent(
        white_name=white_player.name,
        black_name=black_player.name,
        starting_fen=board.fen,
        white_player_type=white_player.player_type,
        black_player_type=black_player.player_type,
        white_competitor_id=white_player.competitor_id,
        black_competitor_id=black_player.competitor_id,
    )

    while not board.is_game_over:
        if stop_event and stop_event.is_set():
            board.set_result("*")
            pgn = board.to_pgn(include_comments=config.game.annotate_pgn)
            yield GameOverEvent(
                result="*",
                reason="interrupted",
                winner_name=None,
                pgn=pgn,
                total_moves=len(board.move_history_san()),
            )
            if config.game.save_pgn:
                await _save_pgn(pgn, config.pgn_dir_path)
            return

        current_color = board.turn
        current_player = white_player if current_color == "white" else black_player

        yield TurnStartEvent(
            color=current_color,
            player_name=current_player.name,
            move_number=board.fullmove_number,
            fen=board.fen,
            board_ascii=render_ascii(board._board),
            legal_moves_san=board.legal_moves_san(),
            move_history_san=board.move_history_san(),
            player_type=current_player.player_type,
        )

        applied = False
        previous_invalid: str | None = None
        previous_error: str | None = None
        model_attempt = 1
        request_attempt = 0
        provider_failures = 0
        terminal_provider_error: ProviderError | None = None
        interrupted = False
        turn_deadline = (
            asyncio.get_running_loop().time() + config.game.move_timeout
            if current_player.player_type != "human"
            else None
        )

        while model_attempt <= config.game.max_retries:
            if stop_event and stop_event.is_set():
                interrupted = True
                break
            request_attempt += 1
            attempt = model_attempt
            logger.info(
                "Requesting move [move=%s attempt=%s request=%s color=%s player=%s]",
                board.fullmove_number,
                attempt,
                request_attempt,
                current_color,
                current_player.name,
            )
            yield MoveRequestedEvent(
                color=current_color,
                attempt_num=attempt,
                player_type=current_player.player_type,
            )

            board_image: bytes | None = None
            if use_images:
                last_move = board._board.peek() if board._board.move_stack else None
                board_image = render_png(board._board, last_move)
                if board_image is None:
                    logger.warning(
                        "Image mode requested but PNG render returned None [move=%s color=%s]. Falling back to text-only prompt for this request.",
                        board.fullmove_number,
                        current_color,
                    )
                else:
                    logger.debug(
                        "Rendered board PNG [move=%s color=%s bytes=%s]",
                        board.fullmove_number,
                        current_color,
                        len(board_image),
                    )

            state = GameState(
                fen=board.fen,
                board_ascii=render_ascii(board._board),
                legal_moves_uci=board.legal_moves_uci(),
                legal_moves_san=board.legal_moves_san(),
                move_history_san=board.move_history_san(),
                color=current_color,
                move_number=board.fullmove_number,
                board_image_bytes=board_image,
                previous_invalid_move=previous_invalid,
                previous_error=previous_error,
                attempt_num=attempt,
            )

            chunk_queue: asyncio.Queue = asyncio.Queue()

            async def _get_and_signal(player=current_player, s=state, q=chunk_queue):
                try:
                    return await player.get_move(s, chunk_queue=q)
                finally:
                    await q.put(None)

            move_task = asyncio.create_task(_get_and_signal())
            stop_task = (
                asyncio.create_task(stop_event.wait())
                if stop_event is not None
                else None
            )
            queue_task: asyncio.Task | None = None
            request_error: ProviderError | None = None
            timed_out = False
            response = None
            try:
                while True:
                    remaining = _remaining_seconds(turn_deadline)
                    if remaining is not None and remaining <= 0:
                        if stop_event is not None and stop_event.is_set():
                            interrupted = True
                        else:
                            timed_out = True
                        break

                    queue_task = asyncio.create_task(chunk_queue.get())
                    waiters: set[asyncio.Task] = {queue_task}
                    if stop_task is not None:
                        waiters.add(stop_task)
                    done, _ = await asyncio.wait(
                        waiters,
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        queue_task.cancel()
                        await asyncio.gather(queue_task, return_exceptions=True)
                        if stop_event is not None and stop_event.is_set():
                            interrupted = True
                        else:
                            timed_out = True
                        break
                    if stop_task is not None and stop_task in done:
                        queue_task.cancel()
                        await asyncio.gather(queue_task, return_exceptions=True)
                        interrupted = True
                        break

                    chunk = queue_task.result()
                    if chunk is None:
                        break
                    yield ReasoningChunkEvent(color=current_color, chunk=chunk)

                if not timed_out and not interrupted:
                    try:
                        response = await move_task
                    except ProviderError as exc:
                        request_error = exc
                    except TimeoutError as exc:
                        request_error = ProviderError(
                            current_player.__class__.__name__,
                            str(exc) or "Player request timed out.",
                            cause=exc,
                            kind="timeout",
                            retryable=True,
                        )
            finally:
                # asyncio.wait() does not take ownership of its child tasks.
                # If the generator is cancelled while waiting, the queue
                # waiter would otherwise survive this request (and a
                # cancellation-resistant player may never enqueue its
                # completion sentinel).
                if queue_task is not None and not queue_task.done():
                    queue_task.cancel()
                if queue_task is not None:
                    await asyncio.gather(queue_task, return_exceptions=True)
                try:
                    await _cancel_and_drain_move_task(move_task, current_player)
                finally:
                    if stop_task is not None and not stop_task.done():
                        stop_task.cancel()
                    if stop_task is not None:
                        await asyncio.gather(stop_task, return_exceptions=True)

            if interrupted:
                break
            if timed_out:
                request_error = ProviderError(
                    current_player.__class__.__name__,
                    f"Turn exceeded the shared {config.game.move_timeout}s deadline.",
                    cause=TimeoutError(),
                    kind="timeout",
                    retryable=True,
                )

            if request_error is not None:
                exc = request_error
                error = f"API error: {exc}"
                logger.error(
                    "Provider failure [move=%s attempt=%d request=%d color=%s "
                    "player=%s kind=%s retryable=%s]: %s",
                    board.fullmove_number,
                    attempt,
                    request_attempt,
                    current_color,
                    current_player.name,
                    getattr(exc, "kind", "unknown"),
                    getattr(exc, "retryable", False),
                    exc,
                )
                yield InvalidMoveEvent(
                    color=current_color,
                    attempted_move="",
                    raw_response="",
                    reasoning="",
                    error=error,
                    attempt_num=attempt,
                    provider_metadata={},
                    failure_kind=_provider_failure_kind(
                        exc,
                        current_player.player_type,
                    ),
                )
                previous_invalid = ""
                previous_error = error
                provider_failures += 1
                can_retry_provider = (
                    provider_failures <= 1
                    and getattr(exc, "retryable", True)
                    and _deadline_has_time(turn_deadline)
                )
                if can_retry_provider:
                    await _provider_retry_backoff(stop_event, turn_deadline)
                    continue
                terminal_provider_error = exc
                break

            assert response is not None

            if not response.raw.strip():
                provider_empty = _is_zero_token_provider_response(
                    response.provider_metadata
                )
                error = (
                    "Provider completed without returning any output tokens."
                    if provider_empty
                    else "Model returned an empty response."
                )
                logger.warning(
                    "Rejected empty response [move=%s attempt=%s color=%s player=%s provider_metadata=%s]",
                    board.fullmove_number,
                    attempt,
                    current_color,
                    current_player.name,
                    response.provider_metadata,
                )
                yield InvalidMoveEvent(
                    color=current_color,
                    attempted_move="",
                    raw_response="",
                    reasoning="",
                    error=_augment_error_with_provider_context(error, response.provider_metadata),
                    attempt_num=attempt,
                    provider_metadata=response.provider_metadata,
                    failure_kind=(
                        "provider_empty_response"
                        if provider_empty
                        else "empty_model_output"
                    ),
                )
                previous_invalid = ""
                previous_error = _augment_error_with_provider_context(error, response.provider_metadata)
                if provider_empty:
                    provider_failures += 1
                    if provider_failures <= 1 and _deadline_has_time(turn_deadline):
                        await _provider_retry_backoff(stop_event, turn_deadline)
                        continue
                    terminal_provider_error = ProviderError(
                        current_player.__class__.__name__,
                        error,
                        kind="empty_response",
                        retryable=True,
                    )
                    break
                model_attempt += 1
                continue

            parsed, error_kind = board.parse_move(response.move)
            if parsed is None:
                if error_kind == "illegal":
                    error = f"'{response.move}' is illegal"
                    if config.game.show_legal_moves:
                        error += " - choose from the legal moves listed above."
                elif error_kind == "ambiguous":
                    error = (
                        f"'{response.move}' is ambiguous - multiple pieces can make that move. "
                        f"Use a disambiguated form (e.g. include the file or rank: Rbd3, R1d3). "
                        f"Legal moves: {', '.join(board.legal_moves_san())}"
                    )
                else:
                    error = (
                        f"'{response.move}' could not be parsed as a valid move. "
                        f"Use SAN notation (e.g. e4, Nf3, cxd4, O-O) or UCI (e.g. e2e4, g1f3, a7a8q)."
                    )
                error = _augment_error_with_provider_context(error, response.provider_metadata)
                logger.warning(
                    "Rejected move attempt [move=%s attempt=%s color=%s player=%s parsed_move=%r error_kind=%s raw_length=%s provider_metadata=%s]",
                    board.fullmove_number,
                    attempt,
                    current_color,
                    current_player.name,
                    response.move,
                    error_kind,
                    len(response.raw),
                    response.provider_metadata,
                )
                yield InvalidMoveEvent(
                    color=current_color,
                    attempted_move=response.move,
                    raw_response=response.raw,
                    reasoning=response.reasoning,
                    error=error,
                    attempt_num=attempt,
                    provider_metadata=response.provider_metadata,
                    failure_kind={
                        "illegal": "illegal_move",
                        "ambiguous": "ambiguous_move",
                    }.get(error_kind, "unparseable_move"),
                )
                previous_invalid = response.move
                previous_error = error
                model_attempt += 1
                continue

            move_number_before = state.move_number
            san = board.push_move(parsed)
            logger.info(
                "Applying move [move=%s attempt=%s color=%s player=%s parsed_move=%r san=%s provider_metadata=%s]",
                move_number_before,
                attempt,
                current_color,
                current_player.name,
                response.move,
                san,
                response.provider_metadata,
            )
            if config.game.annotate_pgn and response.reasoning.strip():
                board.annotate_last_move(_reasoning_comment(response.reasoning))
            yield MoveAppliedEvent(
                color=current_color,
                move_uci=response.move,
                move_san=san,
                raw_response=response.raw,
                reasoning=response.reasoning,
                provider_metadata=response.provider_metadata,
                fen_after=board.fen,
                board_ascii_after=render_ascii(board._board),
                is_check=board.is_check,
                move_number=move_number_before,
            )

            if board.is_check and not board.is_game_over:
                yield CheckEvent(
                    color_in_check=board.turn,
                    checking_move_san=san,
                )

            applied = True
            break

        if interrupted:
            board.set_result("*")
            pgn = board.to_pgn(include_comments=config.game.annotate_pgn)
            yield GameOverEvent(
                result="*",
                reason="interrupted",
                winner_name=None,
                pgn=pgn,
                total_moves=len(board.move_history_san()),
            )
            if config.game.save_pgn:
                await _save_pgn(pgn, config.pgn_dir_path)
            return

        if terminal_provider_error is not None:
            # Infrastructure failures are not chess losses. Propagate after
            # recording the attempt so single games and tournaments can mark
            # the run failed and the rating ledger can keep it unrated.
            raise terminal_provider_error

        if not applied:
            winner = black_player if current_color == "white" else white_player
            result: str = "0-1" if current_color == "white" else "1-0"
            board.set_result(result)
            pgn = board.to_pgn(include_comments=config.game.annotate_pgn)
            logger.warning(
                "Max retries exceeded [move=%s color=%s player=%s last_invalid=%r last_error=%s]",
                board.fullmove_number,
                current_color,
                current_player.name,
                previous_invalid,
                previous_error,
            )
            yield GameOverEvent(
                result=result,
                reason="max_retries_exceeded",
                winner_name=winner.name,
                pgn=pgn,
                total_moves=len(board.move_history_san()),
            )
            if config.game.save_pgn:
                await _save_pgn(pgn, config.pgn_dir_path)
            return

    result = board.result()
    board.set_result(result)
    pgn = board.to_pgn(include_comments=config.game.annotate_pgn)

    winner_color = board.winner_color()
    winner_name: str | None = None
    if winner_color == "white":
        winner_name = white_player.name
    elif winner_color == "black":
        winner_name = black_player.name

    yield GameOverEvent(
        result=result,
        reason=board.game_over_reason(),
        winner_name=winner_name,
        pgn=pgn,
        total_moves=len(board.move_history_san()),
    )

    if config.game.save_pgn:
        await _save_pgn(pgn, config.pgn_dir_path)


async def _save_pgn(pgn: str, pgn_dir: Path) -> None:
    """Write PGN to a timestamped file, creating the directory if needed."""
    pgn_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pgn_path = pgn_dir / f"game_{timestamp}.pgn"
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: pgn_path.write_text(pgn, encoding="utf-8"))


def _remaining_seconds(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - asyncio.get_running_loop().time())


def _deadline_has_time(deadline: float | None) -> bool:
    remaining = _remaining_seconds(deadline)
    return remaining is None or remaining > 0.05


async def _provider_retry_backoff(
    stop_event: asyncio.Event | None,
    deadline: float | None,
) -> None:
    """Pause briefly without extending the turn deadline or delaying stop."""

    remaining = _remaining_seconds(deadline)
    delay = 0.25 if remaining is None else min(0.25, remaining)
    if delay <= 0:
        return
    if stop_event is None:
        await asyncio.sleep(delay)
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except TimeoutError:
        pass


async def _cancel_and_drain_move_task(
    move_task: asyncio.Task,
    player: Player,
) -> None:
    """Cancel a move without letting a cancellation-resistant SDK hang the game."""

    if move_task.done():
        await asyncio.gather(move_task, return_exceptions=True)
        return

    move_task.cancel()
    done, _ = await asyncio.wait(
        {move_task},
        timeout=_MOVE_CANCEL_GRACE_SECONDS,
    )
    if done:
        await asyncio.gather(move_task, return_exceptions=True)
        return

    logger.warning(
        "Move task did not stop after cancellation; closing player resources "
        "[player=%s type=%s]",
        player.name,
        player.player_type,
    )
    force_close = getattr(player, "force_close", None)
    close_method = force_close if callable(force_close) else player.close
    close_task = asyncio.create_task(close_method())
    close_done, _ = await asyncio.wait(
        {close_task},
        timeout=_MOVE_CANCEL_GRACE_SECONDS,
    )
    if close_done:
        await asyncio.gather(close_task, return_exceptions=True)
    else:
        close_task.cancel()
        close_task.add_done_callback(_consume_task_result)
        logger.error(
            "Player resource cleanup exceeded %.1fs [player=%s type=%s]",
            _MOVE_CANCEL_GRACE_SECONDS,
            player.name,
            player.player_type,
        )

    move_task.cancel()
    done, _ = await asyncio.wait(
        {move_task},
        timeout=_MOVE_CANCEL_GRACE_SECONDS,
    )
    if done:
        await asyncio.gather(move_task, return_exceptions=True)
    else:
        # Python cannot forcibly terminate a coroutine that suppresses
        # cancellation. Built-in providers are closed above; detach only a
        # broken third-party task so the game/tournament lifecycle can finish.
        move_task.add_done_callback(_consume_task_result)
        logger.error(
            "Detached cancellation-resistant move task "
            "[player=%s type=%s]",
            player.name,
            player.player_type,
        )


def _consume_task_result(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


def _provider_failure_kind(exc: ProviderError, player_type: str) -> str:
    if player_type == "engine":
        return "engine_error"
    return {
        "timeout": "provider_timeout",
        "empty_response": "provider_empty_response",
        "image_unsupported": "provider_image_unsupported",
    }.get(getattr(exc, "kind", "unknown"), "provider_error")


def _is_zero_token_provider_response(metadata: dict[str, object]) -> bool:
    """Identify an upstream completion that explicitly reports zero output."""

    if metadata.get("stream_chunk_count") == 0:
        return True
    usage = metadata.get("usage")
    if not isinstance(usage, dict):
        return False
    for key in (
        "completion_tokens",
        "output_tokens",
        "candidates_token_count",
    ):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return value == 0
    return False


def _reasoning_comment(reasoning: str) -> str:
    """Normalize model reasoning for PGN comments."""
    text = " ".join(reasoning.split()).strip()
    text = text.replace("{", "(").replace("}", ")")
    if len(text) > 2000:
        return text[:1997] + "..."
    return text


def _augment_error_with_provider_context(error: str, provider_metadata: dict[str, object]) -> str:
    finish_reason = str(provider_metadata.get("finish_reason") or "").upper()
    if "MAX_TOKENS" not in finish_reason:
        return error

    usage = provider_metadata.get("usage")
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_token_count") or usage.get("prompt_tokens")
        output_tokens = usage.get("candidates_token_count") or usage.get("completion_tokens") or usage.get("output_tokens")
        if prompt_tokens is not None and output_tokens is not None:
            return (
                f"{error} Provider stopped generation after hitting the output token limit "
                f"(prompt={prompt_tokens}, output={output_tokens})."
            )

    return f"{error} Provider stopped generation after hitting the output token limit."
