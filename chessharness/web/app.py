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
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from chessharness.auth_store import load_auth_tokens, save_auth_tokens
from chessharness.config import ModelEntry, ProviderConfig, load_config
from chessharness.conv_logger import ConversationLogger
from chessharness.game import run_game
from chessharness.players import create_player
from chessharness.players.llm import LLMPlayer
from chessharness.providers import create_provider

config = load_config()
auth_tokens = load_auth_tokens()


async def _refresh_copilot_token() -> bool:
    """Exchange the stored GitHub OAuth token for a fresh Copilot API token.

    The GitHub OAuth token obtained via the device flow is long-lived (doesn't
    expire unless revoked).  The Copilot internal token it can be exchanged for
    is short-lived (~30 min).  Calling this on startup and on verification failure
    keeps the Copilot token fresh without asking the user to re-authenticate.

    Returns True if auth_tokens was updated.
    """
    github_token = auth_tokens.get("copilot__github_token")
    if not github_token:
        return False
    try:
        async with asyncio.timeout(8):
            result = await _github_http(
                "GET",
                "https://api.github.com/copilot_internal/v2/token",
                token=github_token,
            )
        new_token = result.get("token")
    except Exception:
        new_token = None
    # If exchange failed, fall back to the GitHub OAuth token itself
    auth_tokens["copilot"] = new_token or github_token
    save_auth_tokens(auth_tokens)
    return True


app = FastAPI(title="ChessHarness")


@app.on_event("startup")
async def _startup() -> None:
    """On server start, refresh the Copilot API token so it never silently expires."""
    if auth_tokens.get("copilot__github_token"):
        await _refresh_copilot_token()


# GitHub OAuth App client_id for Copilot device flow (same one used by copilot.vim / copilot.lua)
_COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
_COPILOT_API_BASE = "https://api.githubcopilot.com"


def _copilot_dynamic_config() -> ProviderConfig | None:
    """
    Build a ProviderConfig for copilot from device-flow data stored in auth_store.
    Used when copilot is not present in config.yaml.
    Returns None if no copilot token is stored.
    """
    token = auth_tokens.get("copilot")
    if not token:
        return None
    try:
        models_data: list[dict] = json.loads(auth_tokens.get("copilot__models", "[]"))
    except Exception:
        models_data = []
    if not models_data:
        # Sensible fallback if the models fetch failed
        models_data = [
            {"id": "gpt-4o", "name": "Copilot GPT-4o"},
            {"id": "gpt-4.1", "name": "Copilot GPT-4.1"},
        ]
    return ProviderConfig(
        bearer_token=token,
        base_url=auth_tokens.get("copilot__base_url", _COPILOT_API_BASE),
        models=[ModelEntry(id=m["id"], name=m["name"]) for m in models_data],
    )


def _providers_with_auth_overrides() -> dict[str, ProviderConfig]:
    providers = dict(config.providers)
    for key, value in auth_tokens.items():
        if "__" in key or not value:
            continue  # skip metadata keys (e.g. copilot__base_url)
        if key not in providers:
            continue
        provider = replace(providers[key], bearer_token=value)
        base_url_override = auth_tokens.get(f"{key}__base_url")
        if base_url_override:
            provider = replace(provider, base_url=base_url_override)
        providers[key] = provider
    # Inject dynamic copilot config if not already in config.yaml
    if "copilot" not in providers:
        dyn = _copilot_dynamic_config()
        if dyn:
            providers["copilot"] = dyn
    return providers


def _provider_connected(provider_name: str) -> bool:
    prov = config.providers.get(provider_name)
    return bool((prov and prov.auth_token) or auth_tokens.get(provider_name))


async def _verify_token(provider_name: str, providers_cfg: dict[str, ProviderConfig]) -> bool:
    """Verify a provider token is valid via a lightweight API call."""
    prov = providers_cfg.get(provider_name)
    if not prov or not prov.auth_token:
        return False
    token = prov.auth_token
    try:
        async with asyncio.timeout(8):
            if provider_name == "anthropic":
                import anthropic as _anthropic
                client = _anthropic.AsyncAnthropic(api_key=token)
                await client.models.list()
            elif provider_name == "google":
                from google import genai as _genai
                client = _genai.Client(api_key=token)
                async for _ in client.aio.models.list():
                    break
            elif provider_name == "copilot" and auth_tokens.get("copilot__github_token"):
                # Verify the long-lived GitHub OAuth token via the user API — this is
                # more reliable than hitting the Copilot API (no special headers needed).
                # Refresh the short-lived Copilot API token as a side-effect.
                github_token = auth_tokens["copilot__github_token"]
                result = await _github_http("GET", "https://api.github.com/user", token=github_token)
                if "login" not in result:
                    return False
                await _refresh_copilot_token()
            else:
                # openai, copilot (manual token), kimi, groq, openrouter, …
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=token, base_url=prov.base_url)
                await client.models.list()
        return True
    except Exception:
        return False


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
    providers_cfg = _providers_with_auth_overrides()
    return [
        {"provider": provider, "id": m.id, "name": m.name}
        for provider, prov in providers_cfg.items()
        for m in prov.models
    ]


@app.get("/api/config")
def get_config():
    return {
        "max_retries": config.game.max_retries,
        "show_legal_moves": config.game.show_legal_moves,
    }


# --------------------------------------------------------------------------- #
# GitHub helpers                                                               #
# --------------------------------------------------------------------------- #

async def _github_http(
    method: str,
    url: str,
    *,
    data: dict | None = None,
    token: str | None = None,
) -> dict:
    """Async GitHub API call using asyncio.to_thread + stdlib urllib (no extra deps)."""

    def _do() -> dict:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "ChessHarness/1.0",
        }
        if token:
            headers["Authorization"] = f"token {token}"
        encoded: bytes | None = None
        if data is not None:
            encoded = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=encoded, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                return json.loads(body)
            except Exception:
                raise RuntimeError(f"GitHub HTTP {exc.code}: {body[:200]}") from exc

    return await asyncio.to_thread(_do)


# --------------------------------------------------------------------------- #
# Auth                                                                         #
# --------------------------------------------------------------------------- #

@app.get("/api/auth/providers")
async def get_auth_providers():
    providers_cfg = _providers_with_auth_overrides()
    # Always include 'copilot' so the device-flow UI appears even without a config.yaml entry
    provider_names = sorted({provider for provider, _ in config.all_models()} | {"copilot"})

    async def check(name: str) -> dict:
        ok = await _verify_token(name, providers_cfg)
        return {"provider": name, "connected": ok}

    return await asyncio.gather(*[check(name) for name in provider_names])


@app.post("/api/auth/connect")
async def connect_auth(payload: dict):
    provider = str(payload.get("provider", "")).strip()
    token = str(payload.get("token", "")).strip()
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")
    if provider not in config.providers:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    if not token:
        raise HTTPException(status_code=400, detail="token is required")

    # Build a temporary config with the candidate token and verify it works
    test_cfg = dict(config.providers)
    test_cfg[provider] = replace(test_cfg[provider], bearer_token=token)
    if not await _verify_token(provider, test_cfg):
        raise HTTPException(status_code=401, detail=f"Token verification failed for {provider}")

    auth_tokens[provider] = token
    save_auth_tokens(auth_tokens)
    return {"provider": provider, "connected": True}


@app.post("/api/auth/disconnect")
def disconnect_auth(payload: dict):
    provider = str(payload.get("provider", "")).strip()
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")

    # Remove token + any device-flow metadata keys (e.g. copilot__base_url)
    keys_to_remove = [k for k in auth_tokens if k == provider or k.startswith(f"{provider}__")]
    for k in keys_to_remove:
        del auth_tokens[k]
    if keys_to_remove:
        save_auth_tokens(auth_tokens)
    return {"provider": provider, "connected": _provider_connected(provider)}


@app.post("/api/auth/copilot/device/start")
async def copilot_device_start():
    """Begin GitHub device-authorization flow for Copilot."""

    try:
        result = await _github_http(
            "POST",
            "https://github.com/login/device/code",
            data={"client_id": _COPILOT_CLIENT_ID, "scope": "read:user"},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub error: {exc}") from exc

    if "error" in result:
        raise HTTPException(
            status_code=400,
            detail=result.get("error_description", result["error"]),
        )

    return {
        "device_code": result["device_code"],
        "user_code": result["user_code"],
        "verification_uri": result.get("verification_uri", "https://github.com/login/device"),
        "expires_in": result["expires_in"],
        "interval": result.get("interval", 5),
    }


@app.post("/api/auth/copilot/device/poll")
async def copilot_device_poll(payload: dict):
    """Poll GitHub to check if the user has authorized the device code."""
    device_code = str(payload.get("device_code", "")).strip()
    if not device_code:
        raise HTTPException(status_code=400, detail="device_code is required")

    try:
        result = await _github_http(
            "POST",
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": _COPILOT_CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub error: {exc}") from exc

    error = result.get("error")
    if error in ("authorization_pending", "slow_down"):
        return {"status": "pending"}
    if error == "expired_token":
        return {"status": "expired"}
    if error:
        return {"status": "error", "error": result.get("error_description", error)}

    github_token = result.get("access_token")
    if not github_token:
        return {"status": "error", "error": "No access_token in GitHub response"}

    # Try to exchange the GitHub OAuth token for a short-lived Copilot API token.
    # If the exchange fails (e.g. the internal endpoint is unavailable or returns no token),
    # fall back to using the GitHub OAuth token directly — the Copilot API accepts it.
    copilot_token: str | None = None
    try:
        copilot_result = await _github_http(
            "GET",
            "https://api.github.com/copilot_internal/v2/token",
            token=github_token,
        )
        copilot_token = copilot_result.get("token")
    except Exception:
        pass  # exchange failed; fall through to github_token fallback

    if not copilot_token:
        copilot_token = github_token  # use GitHub OAuth token directly

    # Persist: bearer token, base_url, and GitHub token (for future refresh)
    auth_tokens["copilot"] = copilot_token
    auth_tokens["copilot__base_url"] = _COPILOT_API_BASE
    auth_tokens["copilot__github_token"] = github_token

    # Fetch available models from the Copilot API and store them so games work
    # without needing a copilot entry in config.yaml
    try:
        models_result = await _github_http("GET", f"{_COPILOT_API_BASE}/models", token=copilot_token)
        models_data = [
            {"id": m["id"], "name": m.get("name", m["id"])}
            for m in models_result.get("data", [])
            if m.get("id")
        ]
        if models_data:
            auth_tokens["copilot__models"] = json.dumps(models_data)
    except Exception:
        pass  # models fetch is best-effort; _copilot_dynamic_config() has a fallback

    save_auth_tokens(auth_tokens)

    return {"status": "connected"}


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
