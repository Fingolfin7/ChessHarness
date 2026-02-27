import unittest

import chess

from chessharness.board import ChessBoard
from chessharness.game import _reasoning_comment


class AnnotatedPgnTests(unittest.TestCase):
    def test_pgn_includes_comments_only_when_enabled(self) -> None:
        board = ChessBoard()
        move = chess.Move.from_uci("e2e4")
        board.push_move(move)
        board.annotate_last_move("Center control")
        board.set_result("*")

        plain = board.to_pgn(include_comments=False)
        annotated = board.to_pgn(include_comments=True)

        self.assertNotIn("{Center control}", plain)
        self.assertIn("{ Center control }", annotated)

    def test_reasoning_comment_is_sanitized(self) -> None:
        raw = "  Keep {pressure} on e5.\n\nThen castle.  "
        comment = _reasoning_comment(raw)
        self.assertEqual(comment, "Keep (pressure) on e5. Then castle.")

