"""
FastAPI application — the web UI backend.

Exposes:
  GET  /api/models         List all configured models
  GET  /api/config         Relevant game config for the UI
  WS   /ws/game            Run a game over a WebSocket

In development Vite proxies /api and /ws to this server.
In production FastAPI serves the built frontend from frontend/dist.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from chessharness.config import load_config
from chessharness.conv_logger import ConversationLogger
from chessharness.game import run_game
from chessharness.players import create_player
from chessharness.players.llm import LLMPlayer
from chessharness.providers import create_provider

app = FastAPI(title="ChessHarness")

config = load_config()


def _to_json(data: dict) -> str:
    """json.dumps with datetime → ISO-string support."""
    def _default(obj: object) -> str:
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
    return json.dumps(data, default=_default)

_DIST = Path(__file__).parent.parent.parent / "frontend" / "dist"


# --------------------------------------------------------------------------- #
# REST                                                                         #
# --------------------------------------------------------------------------- #

@app.get("/api/models")
def get_models():
    return [
        {"provider": provider, "id": m.id, "name": m.name}
        for provider, m in config.all_models()
    ]


@app.get("/api/config")
def get_config():
    return {
        "max_retries": config.game.max_retries,
        "show_legal_moves": config.game.show_legal_moves,
    }


# --------------------------------------------------------------------------- #
# WebSocket game                                                                #
# --------------------------------------------------------------------------- #

@app.websocket("/ws/game")
async def game_ws(ws: WebSocket) -> None:
    await ws.accept()

    try:
        # ── First message: { type: "start", white: {...}, black: {...} } ── #
        start = await ws.receive_json()

        w = start["white"]
        b = start["black"]

        white_provider = create_provider(w["provider"], w["model_id"], config.providers)
        black_provider = create_provider(b["provider"], b["model_id"], config.providers)

        white_player = create_player(
            w["provider"], w["name"], white_provider, config.game.show_legal_moves
        )
        black_player = create_player(
            b["provider"], b["name"], black_provider, config.game.show_legal_moves
        )

        # Attach per-player loggers
        game_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = Path("./logs")
        for player, color in ((white_player, "white"), (black_player, "black")):
            if isinstance(player, LLMPlayer):
                player._logger = ConversationLogger(
                    log_dir=log_dir,
                    game_id=game_id,
                    player_name=player.name,
                    color=color,
                )

        stop_event = asyncio.Event()

        async def _game_loop() -> None:
            try:
                async for event in run_game(
                    config, white_player, black_player, stop_event
                ):
                    await ws.send_text(
                        _to_json({"type": type(event).__name__, **dataclasses.asdict(event)})
                    )
            except WebSocketDisconnect:
                stop_event.set()

        async def _receive_loop() -> None:
            try:
                while True:
                    msg = await ws.receive_json()
                    if msg.get("type") == "stop":
                        stop_event.set()
                        break
            except (WebSocketDisconnect, RuntimeError):
                stop_event.set()

        # Run game and receive concurrently; cancel whichever is still running
        # when the other finishes (e.g. game over → no need to keep listening).
        game_task = asyncio.create_task(_game_loop())
        recv_task = asyncio.create_task(_receive_loop())

        done, pending = await asyncio.wait(
            {game_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass

        # Re-raise any exception from the game loop
        for task in done:
            if task.exception():
                raise task.exception()  # type: ignore[misc]

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await ws.send_text(_to_json({"type": "error", "message": str(exc)}))
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Serve built React app in production                                          #
# --------------------------------------------------------------------------- #

if _DIST.exists():
    app.mount(
        "/assets", StaticFiles(directory=_DIST / "assets"), name="assets"
    )

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:
        return FileResponse(_DIST / "index.html")
