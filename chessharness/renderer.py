"""
Board rendering with graceful fallbacks.

PNG render pipeline (tried in order):
  1. svglib + reportlab  — pure Python, no system deps, bundled Cairo DLL in wheel
  2. cairosvg            — faster, but requires a separately installed GTK runtime on Windows
  3. None               — image mode silently falls back to ASCII text

SVG is always available via python-chess.
ASCII is always available via python-chess.
"""

from __future__ import annotations

from io import BytesIO

import chess
import chess.svg

# --- svglib (primary PNG renderer, pure Python) ---
try:
    from svglib.svglib import svg2rlg as _svg2rlg
    from reportlab.graphics import renderPM as _renderPM
    _SVGLIB_AVAILABLE = True
except ImportError:
    _svg2rlg = None  # type: ignore[assignment]
    _renderPM = None  # type: ignore[assignment]
    _SVGLIB_AVAILABLE = False

# --- cairosvg (secondary PNG renderer, faster but needs GTK on Windows) ---
try:
    import cairosvg as _cairosvg
    _CAIROSVG_AVAILABLE = True
except (ImportError, OSError):
    _cairosvg = None  # type: ignore[assignment]
    _CAIROSVG_AVAILABLE = False

_PNG_AVAILABLE = _SVGLIB_AVAILABLE or _CAIROSVG_AVAILABLE


def render_ascii(board: chess.Board) -> str:
    """Standard ASCII board via python-chess."""
    return str(board)


def render_svg(board: chess.Board, last_move: chess.Move | None = None) -> str:
    """SVG string of the board, with optional last-move highlight arrow."""
    arrows: list[chess.svg.Arrow] = []
    if last_move is not None:
        arrows = [chess.svg.Arrow(last_move.from_square, last_move.to_square, color="#cc0000bb")]
    return chess.svg.board(board=board, arrows=arrows, size=400)


def render_png(board: chess.Board, last_move: chess.Move | None = None) -> bytes | None:
    """
    Render the board to PNG bytes, or None if no renderer is available.

    Tries svglib first (pure Python, always works after `uv add svglib reportlab`),
    then falls back to cairosvg if installed.
    """
    svg_str = render_svg(board, last_move)

    if _SVGLIB_AVAILABLE and _svg2rlg is not None and _renderPM is not None:
        try:
            drawing = _svg2rlg(BytesIO(svg_str.encode("utf-8")))
            if drawing is not None:
                return _renderPM.drawToString(drawing, fmt="PNG")
        except Exception:
            pass  # fall through to cairosvg

    if _CAIROSVG_AVAILABLE and _cairosvg is not None:
        try:
            return _cairosvg.svg2png(bytestring=svg_str.encode("utf-8"))
        except Exception:
            pass

    return None


def is_png_available() -> bool:
    """True if at least one PNG renderer is available."""
    return _PNG_AVAILABLE
