"""
Thin facade over python-chess Board and PGN machinery.

Provides the exact interface game.py needs without leaking python-chess
internals into the rest of the codebase (easier to unit-test and swap out).
"""

from __future__ import annotations

import chess
import chess.pgn
from datetime import datetime
from io import StringIO

from chessharness.events import Color, GameResult


class ChessBoard:
    """Facade over chess.Board + chess.pgn.Game."""

    def __init__(self, fen: str | None = None) -> None:
        self._starting_fen = fen
        self._board = chess.Board(fen) if fen else chess.Board()
        self._game = chess.pgn.Game()
        if fen:
            self._game.setup(self._board)
        self._node: chess.pgn.GameNode = self._game
        self._game.headers["Date"] = datetime.now().strftime("%Y.%m.%d")
        self._game.headers["Event"] = "LLM Chess Harness"

    # ------------------------------------------------------------------ #
    # State queries                                                        #
    # ------------------------------------------------------------------ #

    @property
    def fen(self) -> str:
        return self._board.fen()

    @property
    def turn(self) -> Color:
        return "white" if self._board.turn == chess.WHITE else "black"

    @property
    def fullmove_number(self) -> int:
        return self._board.fullmove_number

    @property
    def is_check(self) -> bool:
        return self._board.is_check()

    @property
    def is_game_over(self) -> bool:
        # Treat claimable draws (threefold repetition, fifty-move) as terminal
        # for automated play where no external "claim draw" actor exists.
        return self._board.is_game_over(claim_draw=True)

    def legal_moves_uci(self) -> list[str]:
        return [m.uci() for m in self._board.legal_moves]

    def legal_moves_san(self) -> list[str]:
        return [self._board.san(m) for m in self._board.legal_moves]

    def move_history_san(self) -> list[str]:
        """All moves played so far in SAN notation (replays from start)."""
        board_copy = chess.Board(self._starting_fen) if self._starting_fen else chess.Board()
        san_moves: list[str] = []
        for move in self._board.move_stack:
            san_moves.append(board_copy.san(move))
            board_copy.push(move)
        return san_moves

    # ------------------------------------------------------------------ #
    # Move application                                                    #
    # ------------------------------------------------------------------ #

    def try_parse_move(self, move_str: str) -> chess.Move | None:
        """
        Parse a move string in either UCI or SAN notation.

        Tries UCI first (e.g. e2e4, g1f3, a7a8q), then falls back to SAN
        (e.g. e4, Nf3, cxd4, O-O). SAN parsing requires the current board
        position, so it lives here rather than in the player layer.

        Returns None if neither format matches.
        """
        move, _ = self.parse_move(move_str)
        return move

    def parse_move(self, move_str: str) -> tuple[chess.Move | None, str]:
        """
        Parse and validate a move string, returning (move, error_kind).

        error_kind values:
          ""          — success; move is legal
          "illegal"   — valid notation but not legal in this position
          "ambiguous" — valid SAN but needs disambiguation (e.g. "Rd3" when
                        two rooks can reach d3)
          "format"    — could not be parsed as UCI or SAN at all
        """
        s = move_str.strip()

        # UCI: from_uci() validates syntax only; legality is a separate check.
        try:
            move = chess.Move.from_uci(s)
            if move in self._board.legal_moves:
                return move, ""
            return None, "illegal"
        except (ValueError, chess.InvalidMoveError):
            pass

        # SAN: parse_san() is board-aware and raises specific subclasses.
        try:
            move = self._board.parse_san(s)
            return move, ""
        except chess.AmbiguousMoveError:
            return None, "ambiguous"
        except chess.IllegalMoveError:
            return None, "illegal"
        except (ValueError, chess.InvalidMoveError):
            pass

        return None, "format"

    def is_legal(self, move: chess.Move) -> bool:
        return move in self._board.legal_moves

    def push_move(self, move: chess.Move) -> str:
        """Apply a validated legal move. Returns its SAN string."""
        san = self._board.san(move)
        self._board.push(move)
        self._node = self._node.add_variation(move)
        return san

    # ------------------------------------------------------------------ #
    # Game-over info                                                      #
    # ------------------------------------------------------------------ #

    def game_over_reason(self) -> str:
        outcome = self._board.outcome(claim_draw=True)
        if outcome is None:
            return "unknown"
        match outcome.termination:
            case chess.Termination.CHECKMATE:
                return "checkmate"
            case chess.Termination.STALEMATE:
                return "stalemate"
            case chess.Termination.THREEFOLD_REPETITION:
                return "threefold_repetition"
            case chess.Termination.FIFTY_MOVES:
                return "fifty_move"
            case chess.Termination.INSUFFICIENT_MATERIAL:
                return "insufficient_material"
            case _:
                return "draw"

    def result(self) -> GameResult:
        outcome = self._board.outcome(claim_draw=True)
        if outcome is None:
            return "*"
        return outcome.result()  # type: ignore[return-value]

    def winner_color(self) -> Color | None:
        outcome = self._board.outcome(claim_draw=True)
        if outcome is None or outcome.winner is None:
            return None
        return "white" if outcome.winner == chess.WHITE else "black"

    # ------------------------------------------------------------------ #
    # PGN                                                                 #
    # ------------------------------------------------------------------ #

    def set_players(self, white_name: str, black_name: str) -> None:
        self._game.headers["White"] = white_name
        self._game.headers["Black"] = black_name

    def set_result(self, result: str) -> None:
        self._game.headers["Result"] = result

    def annotate_last_move(self, comment: str) -> None:
        """Attach a PGN comment to the most recently played move node."""
        self._node.comment = comment

    def to_pgn(self, *, include_comments: bool = False) -> str:
        exporter = chess.pgn.StringExporter(
            headers=True,
            variations=False,
            comments=include_comments,
        )
        return self._game.accept(exporter)
