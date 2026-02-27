import unittest

import chess

from chessharness.renderer import render_png


class RendererTests(unittest.TestCase):
    def test_render_png_returns_png_bytes_when_available(self) -> None:
        board = chess.Board()
        png = render_png(board)
        if png is None:
            self.skipTest("No PNG renderer available in this environment")
        self.assertGreater(len(png), 8)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")

