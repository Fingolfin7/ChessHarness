"""
Entry point for the ChessHarness web UI.

Development (hot-reload):
    uv run python web_main.py
    cd frontend && npm run dev      ← Vite dev server on :5173

Production (serve built frontend):
    cd frontend && npm run build
    uv run python web_main.py       ← everything on :8000
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "chessharness.web.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
