import unittest

import chess

from chessharness.board import ChessBoard
from chessharness.game import _augment_error_with_provider_context, _reasoning_comment


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

    def test_max_token_provider_context_is_appended_to_error(self) -> None:
        error = _augment_error_with_provider_context(
            "'' could not be parsed as a valid move.",
            {
                "finish_reason": "FinishReason.MAX_TOKENS",
                "usage": {
                    "prompt_token_count": 3208,
                    "candidates_token_count": 205,
                },
            },
        )
        self.assertIn("output token limit", error)
        self.assertIn("prompt=3208", error)
        self.assertIn("output=205", error)
