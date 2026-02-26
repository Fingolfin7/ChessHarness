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
import logging
import logging.handlers
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

# --------------------------------------------------------------------------- #
# Logging                                                                      #
# --------------------------------------------------------------------------- #

_LOG_FILE = Path("./logs/chessharness.log")
_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.StreamHandler(),                                   # server console
        logging.handlers.RotatingFileHandler(
            _LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3,  # 2 MB × 3 files
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("chessharness")


app = FastAPI(title="ChessHarness")


async def _fetch_copilot_models() -> list[dict]:
    """Fetch available models from the public GitHub Models catalog.

    The catalog returns IDs in "publisher/model-name" format (e.g. "openai/gpt-4.1").
    The inference endpoint at models.inference.ai.azure.com expects only the
    model-name part (e.g. "gpt-4.1"), so we strip the publisher prefix.
    """
    data = await _http_get("https://models.github.ai/catalog/models")
    if not isinstance(data, list):
        return []
    result = []
    for m in data:
        if not isinstance(m, dict):
            continue
        raw_id = m.get("id", "")
        if not raw_id:
            continue
        # Strip "publisher/" prefix — inference API uses the bare model name
        model_id = raw_id.split("/", 1)[-1]
        name = m.get("name", model_id)
        result.append({"id": model_id, "name": name})
    return result


_OPENAI_CHAT_PREFIXES = ("gpt-", "chatgpt-", "o1", "o3", "o4")


async def _fetch_provider_models(provider_name: str) -> list[dict]:
    """Fetch the model list for a known provider. Returns [] on failure."""
    token = auth_tokens.get(provider_name)
    if not token:
        return []
    try:
        async with asyncio.timeout(10):
            if provider_name == "openai":
                data = await _http_get(
                    "https://api.openai.com/v1/models",
                    bearer_token=token,
                )
                items = data.get("data", []) if isinstance(data, dict) else []
                return sorted(
                    [
                        {"id": m["id"], "name": m["id"]}
                        for m in items
                        if isinstance(m, dict)
                        and any(m.get("id", "").startswith(p) for p in _OPENAI_CHAT_PREFIXES)
                    ],
                    key=lambda m: m["id"],
                )

            elif provider_name == "anthropic":
                data = await _http_get(
                    "https://api.anthropic.com/v1/models",
                    api_key_header="x-api-key",
                    api_key=token,
                    extra_headers={"anthropic-version": "2023-06-01"},
                )
                items = data.get("data", []) if isinstance(data, dict) else []
                return [
                    {"id": m["id"], "name": m.get("display_name", m["id"])}
                    for m in items
                    if isinstance(m, dict) and "id" in m
                ]

            elif provider_name == "google":
                url = (
                    "https://generativelanguage.googleapis.com/v1beta/models?"
                    + urllib.parse.urlencode({"key": token})
                )
                data = await _http_get(url)
                items = data.get("models", []) if isinstance(data, dict) else []
                result = []
                for m in items:
                    if not isinstance(m, dict):
                        continue
                    name = m.get("name", "")
                    if not name.startswith("models/gemini"):
                        continue
                    if "generateContent" not in m.get("supportedGenerationMethods", []):
                        continue
                    mid = name.removeprefix("models/")
                    result.append({"id": mid, "name": m.get("displayName", mid)})
                return result

            elif provider_name == "copilot":
                return await _fetch_copilot_models()

    except Exception:
        pass
    return []


async def _refresh_provider_models(provider_name: str) -> None:
    """Fetch the model list for a provider and persist it to auth_store."""
    models = await _fetch_provider_models(provider_name)
    if models:
        auth_tokens[f"{provider_name}__models"] = json.dumps(models)
        save_auth_tokens(auth_tokens)


@app.on_event("startup")
async def _startup() -> None:
    """On server start, migrate stale auth data and refresh model lists."""
    changed = False
    # Migrate: old installs stored the Copilot-internal base URL; clear it so the
    # correct models.inference.ai.azure.com URL is picked up from _KNOWN_PROVIDERS.
    if auth_tokens.get("copilot__base_url") == "https://api.githubcopilot.com":
        del auth_tokens["copilot__base_url"]
        changed = True
    # Migrate: ensure the copilot bearer token is the long-lived GitHub OAuth token,
    # not a short-lived Copilot-internal token from an older session.
    github_token = auth_tokens.get("copilot__github_token")
    if github_token and auth_tokens.get("copilot") != github_token:
        auth_tokens["copilot"] = github_token
        changed = True
    if changed:
        save_auth_tokens(auth_tokens)

    tasks = [
        _refresh_provider_models(name)
        for name in _KNOWN_PROVIDERS
        if auth_tokens.get(name)
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# GitHub OAuth App client_id for Copilot device flow (same one used by copilot.vim / copilot.lua)
_COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
_COPILOT_API_BASE = "https://models.inference.ai.azure.com"

# All providers the app knows about, independent of config.yaml
_KNOWN_PROVIDERS: dict[str, dict] = {
    "openai":    {"base_url": None},
    "anthropic": {"base_url": None},
    "google":    {"base_url": None},
    "copilot":   {"base_url": _COPILOT_API_BASE},
}


def _providers_with_auth_overrides() -> dict[str, ProviderConfig]:
    """Build the runtime provider map by merging config.yaml entries with auth_store data."""
    providers = dict(config.providers)
    for provider_name, info in _KNOWN_PROVIDERS.items():
        token = auth_tokens.get(provider_name)
        if not token:
            continue
        try:
            stored_models: list[dict] = json.loads(
                auth_tokens.get(f"{provider_name}__models", "[]")
            )
        except Exception:
            stored_models = []
        base_url = auth_tokens.get(f"{provider_name}__base_url") or info.get("base_url")
        if provider_name in providers:
            # Existing config entry: apply stored token (and base_url/models if available)
            p = replace(providers[provider_name], bearer_token=token)
            if base_url:
                p = replace(p, base_url=base_url)
            if stored_models and not p.models:
                p = replace(p, models=[ModelEntry(id=m["id"], name=m["name"]) for m in stored_models])
            providers[provider_name] = p
        elif stored_models:
            # No config entry: build a fully dynamic ProviderConfig from stored data
            providers[provider_name] = ProviderConfig(
                bearer_token=token,
                base_url=base_url,
                models=[ModelEntry(id=m["id"], name=m["name"]) for m in stored_models],
            )
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
            elif provider_name == "copilot":
                # Verify any GitHub token (device-flow OAuth or manual PAT) via the user API.
                # The GitHub user endpoint accepts any valid token regardless of scope,
                # and avoids OpenAI SDK schema-parsing issues with the models endpoint.
                result = await _github_http("GET", "https://api.github.com/user", token=token)
                if "login" not in result:
                    return False
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


async def _http_get(
    url: str,
    *,
    bearer_token: str | None = None,
    api_key_header: str | None = None,
    api_key: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict | list:
    """Generic async HTTP GET via stdlib urllib (no extra deps)."""

    def _do() -> dict | list:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "ChessHarness/1.0",
        }
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        if api_key_header and api_key:
            headers[api_key_header] = api_key
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                return json.loads(body)
            except Exception:
                raise RuntimeError(f"HTTP {exc.code}: {body[:200]}") from exc

    return await asyncio.to_thread(_do)


# --------------------------------------------------------------------------- #
# Auth                                                                         #
# --------------------------------------------------------------------------- #

@app.get("/api/auth/providers")
async def get_auth_providers():
    providers_cfg = _providers_with_auth_overrides()
    # Always show all known providers plus any extras defined in config.yaml
    provider_names = sorted(set(_KNOWN_PROVIDERS) | set(config.providers))

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
    if provider not in _KNOWN_PROVIDERS and provider not in config.providers:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    if not token:
        raise HTTPException(status_code=400, detail="token is required")

    # Build a temporary ProviderConfig with the candidate token and verify it
    if provider in config.providers:
        test_cfg = {**config.providers, provider: replace(config.providers[provider], bearer_token=token)}
    else:
        test_cfg = {provider: ProviderConfig(
            bearer_token=token,
            base_url=_KNOWN_PROVIDERS[provider].get("base_url"),
        )}
    if not await _verify_token(provider, test_cfg):
        raise HTTPException(status_code=401, detail=f"Token verification failed for {provider}")

    auth_tokens[provider] = token
    # For GitHub Models, also store as the canonical github_token key so that
    # startup migration and verification both find it in the same place.
    if provider == "copilot":
        auth_tokens["copilot__github_token"] = token
    save_auth_tokens(auth_tokens)
    # Fetch and cache the model list so the dropdown is populated immediately
    await _refresh_provider_models(provider)
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

    # The GitHub OAuth token is used directly with models.inference.ai.azure.com —
    # no Copilot-internal token exchange needed or wanted.
    auth_tokens["copilot"] = github_token
    auth_tokens["copilot__github_token"] = github_token
    save_auth_tokens(auth_tokens)

    # Fetch models in the background so the auth response is not delayed.
    # The frontend re-fetches /api/models after this endpoint returns.
    asyncio.create_task(_refresh_provider_models("copilot"))

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
