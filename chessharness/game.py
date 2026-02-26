"""
Async game loop — the core orchestrator.

This module is UI-agnostic. It yields typed GameEvent objects and never prints,
never writes files directly, and has no Rich/CLI dependencies.

Consumers:
  CLI  → chessharness/cli/display.py
  Web  → FastAPI WebSocket handler (future)
  Tests → async for event in run_game(...): assert ...

Usage:
    async for event in run_game(config, white_player, black_player):
        display_event(event)
"""

from __future__ import annotations

import asyncio
import chess
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from chessharness.board import ChessBoard
from chessharness.config import Config
from chessharness.events import (
    GameEvent,
    GameStartEvent,
    TurnStartEvent,
    MoveRequestedEvent,
    InvalidMoveEvent,
    MoveAppliedEvent,
    CheckEvent,
    GameOverEvent,
)
from chessharness.players.base import Player, GameState
from chessharness.providers.base import ProviderError
from chessharness.renderer import render_ascii, render_png, is_png_available


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
    board = ChessBoard()
    board.set_players(white_player.name, black_player.name)

    use_images = config.game.board_input == "image" and is_png_available()

    yield GameStartEvent(
        white_name=white_player.name,
        black_name=black_player.name,
    )

    while not board.is_game_over:
        if stop_event and stop_event.is_set():
            board.set_result("*")
            pgn = board.to_pgn()
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
        )

        applied = False
        previous_invalid: str | None = None
        previous_error: str | None = None

        for attempt in range(1, config.game.max_retries + 1):
            yield MoveRequestedEvent(color=current_color, attempt_num=attempt)

            board_image: bytes | None = None
            if use_images:
                # Get last move for highlight arrow (if any)
                last_move = board._board.peek() if board._board.move_stack else None
                board_image = render_png(board._board, last_move)

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

            try:
                response = await current_player.get_move(state)
            except ProviderError as exc:
                error = f"API error: {exc}"
                yield InvalidMoveEvent(
                    color=current_color,
                    attempted_move="",
                    raw_response="",
                    reasoning="",
                    error=error,
                    attempt_num=attempt,
                )
                previous_invalid = ""
                previous_error = error
                continue

            # --- Validate: non-empty response ---
            if not response.raw.strip():
                error = "Model returned an empty response."
                yield InvalidMoveEvent(
                    color=current_color,
                    attempted_move="",
                    raw_response="",
                    reasoning="",
                    error=error,
                    attempt_num=attempt,
                )
                previous_invalid = ""
                previous_error = error
                continue

            # --- Validate: UCI format ---
            parsed = board.try_parse_move(response.move)
            if parsed is None:
                error = (
                    f"'{response.move}' is not a recognised move. "
                    "Use UCI (e.g. e2e4, a7a8q) or SAN (e.g. e4, Nf3, cxd4, O-O)."
                )
                yield InvalidMoveEvent(
                    color=current_color,
                    attempted_move=response.move,
                    raw_response=response.raw,
                    reasoning=response.reasoning,
                    error=error,
                    attempt_num=attempt,
                )
                previous_invalid = response.move
                previous_error = error
                continue

            # --- Validate: move is legal in this position ---
            if not board.is_legal(parsed):
                error = (
                    f"'{response.move}' is syntactically valid UCI but not legal here. "
                    f"Legal moves: {', '.join(board.legal_moves_san())}"
                )
                yield InvalidMoveEvent(
                    color=current_color,
                    attempted_move=response.move,
                    raw_response=response.raw,
                    reasoning=response.reasoning,
                    error=error,
                    attempt_num=attempt,
                )
                previous_invalid = response.move
                previous_error = error
                continue

            # --- Apply the move ---
            move_number_before = state.move_number  # capture before push (fullmove_number increments after black)
            san = board.push_move(parsed)
            yield MoveAppliedEvent(
                color=current_color,
                move_uci=response.move,
                move_san=san,
                raw_response=response.raw,
                reasoning=response.reasoning,
                fen_after=board.fen,
                board_ascii_after=render_ascii(board._board),
                is_check=board.is_check,
                move_number=move_number_before,
            )

            # Announce check (if the game isn't already over)
            if board.is_check and not board.is_game_over:
                yield CheckEvent(
                    color_in_check=board.turn,  # the side that is now to move is in check
                    checking_move_san=san,
                )

            applied = True
            break

        if not applied:
            # Player forfeits by exhausting retries
            winner = black_player if current_color == "white" else white_player
            result: str = "0-1" if current_color == "white" else "1-0"
            board.set_result(result)
            pgn = board.to_pgn()

            yield GameOverEvent(
                result=result,  # type: ignore[arg-type]
                reason="max_retries_exceeded",
                winner_name=winner.name,
                pgn=pgn,
                total_moves=len(board.move_history_san()),
            )
            if config.game.save_pgn:
                await _save_pgn(pgn, config.pgn_dir_path)
            return

    # --- Normal game termination ---
    result = board.result()
    board.set_result(result)
    pgn = board.to_pgn()

    winner_color = board.winner_color()
    winner_name: str | None = None
    if winner_color == "white":
        winner_name = white_player.name
    elif winner_color == "black":
        winner_name = black_player.name

    yield GameOverEvent(
        result=result,
        reason=board.game_over_reason(),  # type: ignore[arg-type]
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
    await loop.run_in_executor(
        None, lambda: pgn_path.write_text(pgn, encoding="utf-8")
    )
