"""
Entry point for the ChessHarness web UI.

Development (Vite hot-reload):
    uv run python scripts/dev.py    <- backend on :8000 + Vite on :5173
    CHESSHARNESS_RELOAD=1           <- optional backend reload (not engine-safe on Windows)

Production (serve built frontend):
    cd frontend && npm run build
    uv run python web_main.py       <- everything on :8000
"""

import os

import uvicorn


def _reload_enabled() -> bool:
    """Return whether the developer explicitly requested Uvicorn reload."""
    return os.getenv("CHESSHARNESS_RELOAD", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


if __name__ == "__main__":
    uvicorn.run(
        "chessharness.web.app:app",
        host="0.0.0.0",
        port=8000,
        # Uvicorn's Windows reload worker uses a selector event loop, which
        # cannot launch the async UCI subprocess used by python-chess.  Keep
        # the subprocess-capable single process as the safe default; reload
        # remains available for UI-only development via CHESSHARNESS_RELOAD=1.
        reload=_reload_enabled(),
    )
