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
import os
import uuid
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from chessharness.auth_store import load_auth_tokens, save_auth_tokens
from chessharness.config import ProviderConfig, load_config
from chessharness.conv_logger import ConversationLogger
from chessharness.game import run_game
from chessharness.players import create_player
from chessharness.players.llm import LLMPlayer
from chessharness.providers import create_provider

app = FastAPI(title="ChessHarness")


config = load_config()
auth_tokens = load_auth_tokens()
oauth_flows: dict[str, dict] = {}


def _providers_with_auth_overrides() -> dict[str, ProviderConfig]:
    providers = dict(config.providers)
    for provider_name, token in auth_tokens.items():
        if provider_name in providers and token:
            providers[provider_name] = replace(providers[provider_name], bearer_token=token)
    return providers


def _provider_connected(provider_name: str) -> bool:
    prov = config.providers.get(provider_name)
    return bool((prov and prov.auth_token) or auth_tokens.get(provider_name))




def _github_post_form(url: str, data: dict[str, str]) -> dict:
    body = urlencode(data).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))

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
# Auth                                                                         #
# --------------------------------------------------------------------------- #

@app.get("/api/auth/providers")
def get_auth_providers():
    provider_names = sorted({provider for provider, _ in config.all_models()})
    return [
        {"provider": name, "connected": _provider_connected(name)}
        for name in provider_names
    ]


@app.post("/api/auth/connect")
def connect_auth(payload: dict):
    provider = str(payload.get("provider", "")).strip()
    token = str(payload.get("token", "")).strip()
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")
    if provider not in config.providers:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    if not token:
        raise HTTPException(status_code=400, detail="token is required")

    auth_tokens[provider] = token
    save_auth_tokens(auth_tokens)
    return {"provider": provider, "connected": True}


@app.post("/api/auth/disconnect")
def disconnect_auth(payload: dict):
    provider = str(payload.get("provider", "")).strip()
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")

    if provider in auth_tokens:
        del auth_tokens[provider]
        save_auth_tokens(auth_tokens)
    return {"provider": provider, "connected": _provider_connected(provider)}


@app.post("/api/auth/oauth/start")
def oauth_start(payload: dict):
    provider = str(payload.get("provider", "")).strip()
    if provider != "copilot":
        raise HTTPException(status_code=400, detail="OAuth is currently supported only for provider 'copilot'")

    client_id = os.getenv("CHESSHARNESS_GITHUB_CLIENT_ID", "").strip()
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail="Set CHESSHARNESS_GITHUB_CLIENT_ID to enable Copilot OAuth device login",
        )

    result = _github_post_form(
        "https://github.com/login/device/code",
        {"client_id": client_id, "scope": "read:user"},
    )

    if "device_code" not in result:
        raise HTTPException(status_code=502, detail=f"OAuth start failed: {result}")

    flow_id = str(uuid.uuid4())
    oauth_flows[flow_id] = {
        "provider": provider,
        "device_code": result["device_code"],
        "interval": int(result.get("interval", 5)),
        "client_id": client_id,
    }

    return {
        "flow_id": flow_id,
        "provider": provider,
        "user_code": result.get("user_code"),
        "verification_uri": result.get("verification_uri") or result.get("verification_uri_complete"),
        "verification_uri_complete": result.get("verification_uri_complete"),
        "expires_in": int(result.get("expires_in", 900)),
        "interval": int(result.get("interval", 5)),
    }


@app.post("/api/auth/oauth/poll")
def oauth_poll(payload: dict):
    flow_id = str(payload.get("flow_id", "")).strip()
    flow = oauth_flows.get(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Unknown or expired OAuth flow")

    token_result = _github_post_form(
        "https://github.com/login/oauth/access_token",
        {
            "client_id": flow["client_id"],
            "device_code": flow["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
    )

    if "access_token" in token_result:
        provider = flow["provider"]
        auth_tokens[provider] = token_result["access_token"]
        save_auth_tokens(auth_tokens)
        del oauth_flows[flow_id]
        return {"status": "connected", "provider": provider}

    error = token_result.get("error")
    if error in {"authorization_pending", "slow_down"}:
        return {"status": "pending", "error": error}

    if flow_id in oauth_flows:
        del oauth_flows[flow_id]
    return {"status": "failed", "error": error or "unknown_error"}


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

        providers_cfg = _providers_with_auth_overrides()
        white_provider = create_provider(w["provider"], w["model_id"], providers_cfg)
        black_provider = create_provider(b["provider"], b["model_id"], providers_cfg)

        white_player = create_player(
            w["provider"], w["name"], white_provider, config.game.show_legal_moves, config.game.move_timeout
        )
        black_player = create_player(
            b["provider"], b["name"], black_provider, config.game.show_legal_moves, config.game.move_timeout
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
