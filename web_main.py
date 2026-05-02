"""
Entry point for the ChessHarness web UI.

Development (hot-reload):
    uv run python scripts/dev.py    <- backend on :8000 + Vite on :5173

Production (serve built frontend):
    cd frontend && npm run build
    uv run python web_main.py       <- everything on :8000
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "chessharness.web.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
