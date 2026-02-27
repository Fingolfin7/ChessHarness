"""
FastAPI application â€” the web UI backend.

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
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
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

def _configure_logging() -> None:
    """Set practical defaults: concise app logs, noisy deps at warning+."""
    app_level_name = os.getenv("CHESSHARNESS_LOG_LEVEL", "INFO").upper()
    app_level = getattr(logging, app_level_name, logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    )
    stream_handler = logging.StreamHandler()
    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE,
        maxBytes=2 * 1024 * 1024,  # 2 MB
        backupCount=3,
        encoding="utf-8",
    )
    stream_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    chess_logger = logging.getLogger("chessharness")
    chess_logger.setLevel(app_level)
    chess_logger.handlers = [stream_handler, file_handler]
    chess_logger.propagate = False

    for noisy_name in (
        "svglib",
        "reportlab",
        "urllib3",
        "httpx",
        "httpcore",
        "openai",
        "anthropic",
        "google",
        "uvicorn.access",
    ):
        logging.getLogger(noisy_name).setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger("chessharness")


app = FastAPI(title="ChessHarness")



_COPILOT_CHAT_PROVIDER = "copilot_chat"

# GitHub OAuth App client_id for Copilot device flow (same one used by copilot.vim / copilot.lua)
_COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
_COPILOT_CHAT_API_BASE = "https://api.githubcopilot.com"


@app.on_event("startup")
async def _startup() -> None:
    """On server start, migrate any stale auth data from older installs."""
    changed = False

    # Migrate old provider key names to copilot_chat.
    legacy_keys = [k for k in list(auth_tokens) if k == "copilot" or k.startswith("copilot__")]
    for old_key in legacy_keys:
        suffix = old_key[len("copilot"):]
        new_key = f"{_COPILOT_CHAT_PROVIDER}{suffix}"
        if new_key not in auth_tokens:
            auth_tokens[new_key] = auth_tokens[old_key]
            changed = True
        if old_key in auth_tokens:
            del auth_tokens[old_key]
            changed = True

    # Remove stale base_url metadata from previous experiments.
    legacy_base_url_keys = [
        f"{_COPILOT_CHAT_PROVIDER}__base_url",
        "copilot__base_url",
    ]
    for key in legacy_base_url_keys:
        if auth_tokens.get(key) in {
            "https://api.githubcopilot.com",
            "https://models.inference.ai.azure.com",
        }:
            del auth_tokens[key]
            changed = True

    if changed:
        save_auth_tokens(auth_tokens)

# All providers the app knows about, independent of config.yaml
_KNOWN_PROVIDERS: dict[str, dict] = {
    "openai":    {"base_url": None},
    "anthropic": {"base_url": None},
    "google":    {"base_url": None},
    _COPILOT_CHAT_PROVIDER: {"base_url": _COPILOT_CHAT_API_BASE},
}


def _canonical_provider_name(name: str) -> str:
    """Map legacy provider ids to the current canonical name."""
    return _COPILOT_CHAT_PROVIDER if name == "copilot" else name


def _providers_from_config_with_migrations() -> dict[str, ProviderConfig]:
    """Return config providers with legacy copilot renamed to copilot_chat."""
    providers = dict(config.providers)
    if "copilot" in providers and _COPILOT_CHAT_PROVIDER not in providers:
        legacy = providers.pop("copilot")
        providers[_COPILOT_CHAT_PROVIDER] = replace(legacy, base_url=_COPILOT_CHAT_API_BASE)
    elif _COPILOT_CHAT_PROVIDER in providers:
        providers[_COPILOT_CHAT_PROVIDER] = replace(
            providers[_COPILOT_CHAT_PROVIDER],
            base_url=_COPILOT_CHAT_API_BASE,
        )
        providers.pop("copilot", None)
    return providers


def _copilot_chat_openai_headers() -> dict[str, str]:
    return {
        "Editor-Version": "vscode/1.95.3",
        "Editor-Plugin-Version": "copilot-chat/0.22.1",
        "Copilot-Integration-Id": "vscode-chat",
    }


def _providers_with_auth_overrides() -> dict[str, ProviderConfig]:
    """Build the runtime provider map: models from config.yaml, tokens from auth_store."""
    providers = _providers_from_config_with_migrations()
    for provider_name, info in _KNOWN_PROVIDERS.items():
        token = auth_tokens.get(provider_name)
        if not token:
            continue
        base_url = auth_tokens.get(f"{provider_name}__base_url") or info.get("base_url")
        if provider_name in providers:
            # Provider defined in config.yaml: inject the stored token (and base_url if set).
            p = replace(providers[provider_name], bearer_token=token)
            if base_url:
                p = replace(p, base_url=base_url)
            providers[provider_name] = p
        # Providers authenticated via UI but absent from config.yaml have no model list,
        # so they won't appear in the dropdown â€” the user must add them to config.yaml.
    return providers


def _find_model_entry(
    providers_cfg: dict[str, ProviderConfig],
    provider_name: str,
    model_id: str,
) -> ModelEntry | None:
    prov = providers_cfg.get(provider_name)
    if prov is None:
        return None
    for model in prov.models:
        if model.id == model_id:
            return model
    return None


def _provider_connected(provider_name: str) -> bool:
    provider_name = _canonical_provider_name(provider_name)
    providers_cfg = _providers_from_config_with_migrations()
    prov = providers_cfg.get(provider_name)
    return bool((prov and prov.auth_token) or auth_tokens.get(provider_name))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp_utc(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Handle both seconds and milliseconds epoch values.
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000.0
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


async def _copilot_chat_exchange_token(github_token: str) -> dict:
    """Exchange a GitHub token for a short-lived Copilot Chat access token."""
    result = await _github_http(
        "GET",
        "https://api.github.com/copilot_internal/v2/token",
        token=github_token,
    )
    if "token" not in result:
        raise RuntimeError(result.get("message", "No token in Copilot exchange response"))
    return result


async def _ensure_copilot_chat_access_token(*, force_refresh: bool = False) -> None:
    """Refresh cached Copilot Chat token from the stored GitHub token when needed."""
    provider = _COPILOT_CHAT_PROVIDER
    github_token = auth_tokens.get(f"{provider}__github_token")
    if not github_token:
        return

    expires_at = _parse_timestamp_utc(auth_tokens.get(f"{provider}__expires_at"))
    cached_token = auth_tokens.get(provider)
    if (
        not force_refresh
        and cached_token
        and expires_at is not None
        and expires_at > (_utc_now() + timedelta(minutes=2))
    ):
        return

    exchange = await _copilot_chat_exchange_token(github_token)
    access_token = str(exchange["token"]).strip()
    if not access_token:
        raise RuntimeError("Copilot exchange returned an empty token")

    parsed_expiry = (
        _parse_timestamp_utc(exchange.get("expires_at"))
        or (_utc_now() + timedelta(seconds=int(exchange.get("expires_in", 1800))))
    )
    auth_tokens[provider] = access_token
    auth_tokens[f"{provider}__github_token"] = github_token
    auth_tokens[f"{provider}__expires_at"] = parsed_expiry.isoformat()
    save_auth_tokens(auth_tokens)


async def _providers_with_auth_overrides_async() -> dict[str, ProviderConfig]:
    try:
        await _ensure_copilot_chat_access_token()
    except Exception as exc:
        logger.warning("Copilot Chat token refresh failed: %s", exc)
    return _providers_with_auth_overrides()


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
                url = (
                    "https://generativelanguage.googleapis.com/v1beta/models?"
                    + urllib.parse.urlencode({"key": token})
                )
                data = await _http_get(url)
                if not isinstance(data, dict) or "models" not in data:
                    return False
            elif provider_name == _COPILOT_CHAT_PROVIDER:
                # Support either a GitHub token (exchange first) or a direct Copilot token.
                try:
                    exchange = await _copilot_chat_exchange_token(token)
                    if "token" in exchange:
                        return True
                except Exception:
                    from openai import AsyncOpenAI
                    client = AsyncOpenAI(
                        api_key=token,
                        base_url=prov.base_url,
                        default_headers=_copilot_chat_openai_headers(),
                    )
                    await client.models.list()
            else:
                # openai, kimi, groq, openrouter, â€¦
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=token, base_url=prov.base_url)
                await client.models.list()
        return True
    except Exception:
        return False


def _to_json(data: dict) -> str:
    """json.dumps with datetime â†’ ISO-string support."""
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
        {
            "provider": provider,
            "id": m.id,
            "name": m.name,
            "supports_vision": m.supports_vision,
        }
        for provider, prov in providers_cfg.items()
        for m in prov.models
    ]


@app.get("/api/config")
def get_config():
    return {
        "max_retries": config.game.max_retries,
        "show_legal_moves": config.game.show_legal_moves,
        "board_input": config.game.board_input,
        "annotate_pgn": config.game.annotate_pgn,
        "max_output_tokens": config.game.max_output_tokens,
        "reasoning_effort": config.game.reasoning_effort,
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
    providers_cfg = await _providers_with_auth_overrides_async()
    # Always show all known providers plus any extras defined in config.yaml
    provider_names = sorted(
        {_canonical_provider_name(name) for name in (set(_KNOWN_PROVIDERS) | set(config.providers))}
    )

    async def check(name: str) -> dict:
        ok = await _verify_token(name, providers_cfg)
        return {"provider": name, "connected": ok}

    return await asyncio.gather(*[check(name) for name in provider_names])


@app.post("/api/auth/connect")
async def connect_auth(payload: dict):
    provider = _canonical_provider_name(str(payload.get("provider", "")).strip())
    token = str(payload.get("token", "")).strip()
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")
    if provider not in _KNOWN_PROVIDERS and provider not in config.providers:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    if not token:
        raise HTTPException(status_code=400, detail="token is required")

    # Build a temporary ProviderConfig with the candidate token and verify it
    providers_from_cfg = _providers_from_config_with_migrations()
    if provider in providers_from_cfg:
        test_cfg = {**providers_from_cfg, provider: replace(providers_from_cfg[provider], bearer_token=token)}
    else:
        test_cfg = {provider: ProviderConfig(
            bearer_token=token,
            base_url=_KNOWN_PROVIDERS[provider].get("base_url"),
        )}
    if not await _verify_token(provider, test_cfg):
        raise HTTPException(status_code=401, detail=f"Token verification failed for {provider}")

    if provider == _COPILOT_CHAT_PROVIDER:
        # Prefer GitHub token -> Copilot token exchange; accept direct Copilot token as fallback.
        exchanged = False
        try:
            me = await _github_http("GET", "https://api.github.com/user", token=token)
            if "login" in me:
                exchange = await _copilot_chat_exchange_token(token)
                access_token = str(exchange["token"]).strip()
                expires_at = (
                    _parse_timestamp_utc(exchange.get("expires_at"))
                    or (_utc_now() + timedelta(seconds=int(exchange.get("expires_in", 1800))))
                )
                auth_tokens[provider] = access_token
                auth_tokens[f"{provider}__github_token"] = token
                auth_tokens[f"{provider}__expires_at"] = expires_at.isoformat()
                exchanged = True
        except Exception:
            exchanged = False
        if not exchanged:
            auth_tokens[provider] = token
            auth_tokens.pop(f"{provider}__github_token", None)
            auth_tokens.pop(f"{provider}__expires_at", None)
    else:
        auth_tokens[provider] = token
    save_auth_tokens(auth_tokens)
    return {"provider": provider, "connected": True}


@app.post("/api/auth/disconnect")
def disconnect_auth(payload: dict):
    provider = _canonical_provider_name(str(payload.get("provider", "")).strip())
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")

    # Remove token + any device-flow metadata keys (e.g. copilot__base_url)
    keys_to_remove = [k for k in auth_tokens if k == provider or k.startswith(f"{provider}__")]
    for k in keys_to_remove:
        del auth_tokens[k]
    if keys_to_remove:
        save_auth_tokens(auth_tokens)
    return {"provider": provider, "connected": _provider_connected(provider)}


@app.post("/api/auth/copilot_chat/device/start")
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


@app.post("/api/auth/copilot_chat/device/poll")
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

    try:
        exchange = await _copilot_chat_exchange_token(github_token)
        access_token = str(exchange["token"]).strip()
        expires_at = (
            _parse_timestamp_utc(exchange.get("expires_at"))
            or (_utc_now() + timedelta(seconds=int(exchange.get("expires_in", 1800))))
        )
    except Exception as exc:
        return {"status": "error", "error": f"Copilot token exchange failed: {exc}"}

    auth_tokens[_COPILOT_CHAT_PROVIDER] = access_token
    auth_tokens[f"{_COPILOT_CHAT_PROVIDER}__github_token"] = github_token
    auth_tokens[f"{_COPILOT_CHAT_PROVIDER}__expires_at"] = expires_at.isoformat()
    save_auth_tokens(auth_tokens)

    return {"status": "connected"}


# --------------------------------------------------------------------------- #
# Tournament broadcast                                                         #
# --------------------------------------------------------------------------- #

import dataclasses as _dc

from chessharness.tournaments import (
    KnockoutTournament,
    TournamentParticipant,
    create_tournament,
)
from chessharness.tournaments.events import (
    MatchCompleteEvent,
    MatchGameEvent,
    TournamentCompleteEvent,
    TournamentEvent,
)


class _TournamentBroadcaster:
    """
    Singleton that manages the active tournament and its WebSocket subscribers.

    Two subscription types:
      - all_subscribers  : receive every TournamentEvent (for the overview page)
      - game_subscribers : receive per-match GameEvents only (for the detail view)

    A replay log is kept per match so late-connecting clients catch up instantly.
    """

    def __init__(self) -> None:
        self._all_subs: list[asyncio.Queue] = []
        self._game_subs: dict[str, list[asyncio.Queue]] = {}
        self._game_log: dict[str, list[dict]] = {}   # match_id → serialised events
        self._tournament_log: list[dict] = []         # all tournament events, serialised
        self._pgns: list[str] = []                    # PGN for each completed game
        self.status: dict = {"state": "idle"}
        self._task: asyncio.Task | None = None

    # ── Subscriptions ────────────────────────────────────────────────── #

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._all_subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._all_subs.remove(q)
        except ValueError:
            pass

    def subscribe_game(self, match_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._game_subs.setdefault(match_id, []).append(q)
        return q

    def unsubscribe_game(self, match_id: str, q: asyncio.Queue) -> None:
        subs = self._game_subs.get(match_id, [])
        try:
            subs.remove(q)
        except ValueError:
            pass

    def replay_log(self) -> list[dict]:
        return list(self._tournament_log)

    def game_replay_log(self, match_id: str) -> list[dict]:
        return list(self._game_log.get(match_id, []))

    # ── Broadcasting ─────────────────────────────────────────────────── #

    async def _broadcast_all(self, payload: dict) -> None:
        self._tournament_log.append(payload)
        for q in list(self._all_subs):
            await q.put(payload)

    async def _broadcast_game(self, match_id: str, payload: dict) -> None:
        self._game_log.setdefault(match_id, []).append(payload)
        for q in list(self._game_subs.get(match_id, [])):
            await q.put(payload)

    # ── Tournament runner ─────────────────────────────────────────────── #

    def start(
        self,
        participants: list[TournamentParticipant],
        config,
        player_factory,
        tournament,
    ) -> None:
        if self._task and not self._task.done():
            raise RuntimeError("A tournament is already running.")
        self._tournament_log.clear()
        self._game_log.clear()
        self._pgns.clear()
        self.status = {"state": "running", "participants": [p.display_name for p in participants]}
        self._task = asyncio.create_task(
            self._run(participants, config, player_factory, tournament)
        )

    def collected_pgns(self) -> list[str]:
        return list(self._pgns)

    async def _run(self, participants, config, player_factory, tournament) -> None:
        try:
            async for event in tournament.run(participants, config, player_factory):
                t_payload = _to_json_dict(event)
                await self._broadcast_all(t_payload)

                if isinstance(event, MatchGameEvent):
                    g_payload = _to_json_dict(event.game_event)
                    await self._broadcast_game(event.match_id, g_payload)

                if isinstance(event, MatchCompleteEvent) and event.result.pgn:
                    self._pgns.append(event.result.pgn)

                if isinstance(event, TournamentCompleteEvent):
                    self.status = {
                        "state": "complete",
                        "winner": event.winner_name,
                    }
        except Exception as exc:
            logger.error("Tournament error: %s", exc, exc_info=True)
            self.status = {"state": "error", "detail": str(exc)}
            err = {"type": "error", "message": str(exc)}
            await self._broadcast_all(err)


_tournament_broadcaster = _TournamentBroadcaster()


def _to_json_dict(obj):
    """Recursively convert a dataclass to a JSON-safe structure.

    Unlike dataclasses.asdict(), this injects a "type" key (the class name) at
    *every* level of nesting, not just the top.  That lets the frontend reducer
    dispatch on the type of nested events (e.g. the game_event inside a
    MatchGameEvent) without extra bookkeeping.
    """
    if _dc.is_dataclass(obj) and not isinstance(obj, type):
        d: dict = {"type": type(obj).__name__}
        for f in _dc.fields(obj):
            d[f.name] = _to_json_dict(getattr(obj, f.name))
        return d
    if isinstance(obj, (list, tuple)):
        return [_to_json_dict(item) for item in obj]
    return obj


@app.get("/api/tournament/status")
def tournament_status():
    return _tournament_broadcaster.status


@app.get("/api/tournament/pgn")
def tournament_pgn():
    from fastapi.responses import PlainTextResponse
    pgns = _tournament_broadcaster.collected_pgns()
    if not pgns:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="No completed games yet.")
    content = "\n\n".join(pgns)
    return PlainTextResponse(
        content,
        headers={"Content-Disposition": 'attachment; filename="tournament.pgn"'},
    )


@app.post("/api/tournament/start")
async def tournament_start(payload: dict):
    """
    Start a new tournament.

    Expected body:
    {
      "tournament_type": "knockout",
      "draw_handling": "rematch",
      "participants": [
        {"provider": "anthropic", "model_id": "claude-sonnet-4-6", "name": "Claude Sonnet"}
      ]
    }
    """
    if _tournament_broadcaster.status.get("state") == "running":
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="A tournament is already running.")

    tournament_type = payload.get("tournament_type", "knockout")
    draw_handling = payload.get("draw_handling", "rematch")
    raw_participants = payload.get("participants", [])

    if len(raw_participants) < 2:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Need at least 2 participants.")

    providers_cfg = await _providers_with_auth_overrides_async()

    participants = []
    for i, rp in enumerate(raw_participants, 1):
        provider_name = rp["provider"]
        model_id = rp["model_id"]
        display_name = rp.get("name", model_id)
        model_entry = _find_model_entry(providers_cfg, provider_name, model_id)
        if model_entry is None:
            from chessharness.config import ModelEntry as _ME
            model_entry = _ME(id=model_id, name=display_name)
        participants.append(
            TournamentParticipant(
                provider_name=provider_name,
                model=model_entry,
                seed=i,
            )
        )

    game_cfg = config.game
    ui_settings = payload.get("settings") or {}
    if ui_settings:
        overrides: dict = {}
        if "max_retries" in ui_settings:
            overrides["max_retries"] = max(1, int(ui_settings["max_retries"]))
        if "show_legal_moves" in ui_settings:
            overrides["show_legal_moves"] = bool(ui_settings["show_legal_moves"])
        if ui_settings.get("board_input") in ("text", "image"):
            overrides["board_input"] = ui_settings["board_input"]
        if "annotate_pgn" in ui_settings:
            overrides["annotate_pgn"] = bool(ui_settings["annotate_pgn"])
        if "max_output_tokens" in ui_settings:
            overrides["max_output_tokens"] = max(1, int(ui_settings["max_output_tokens"]))
        if "reasoning_effort" in ui_settings:
            effort = ui_settings["reasoning_effort"]
            if effort in ("low", "medium", "high", None, "", "default", "auto", "none"):
                overrides["reasoning_effort"] = (
                    effort if effort in ("low", "medium", "high") else None
                )
        if overrides:
            game_cfg = replace(game_cfg, **overrides)
    tournament_config = replace(config, game=game_cfg)

    def player_factory(participant: TournamentParticipant):
        provider = create_provider(
            participant.provider_name,
            participant.model.id,
            providers_cfg,
            supports_vision_override=participant.model.supports_vision,
        )
        return create_player(
            participant.provider_name,
            participant.display_name,
            provider,
            game_cfg.show_legal_moves,
            game_cfg.move_timeout,
            game_cfg.max_output_tokens,
            game_cfg.reasoning_effort,
        )

    tournament = create_tournament(tournament_type, draw_handling=draw_handling)
    _tournament_broadcaster.start(participants, tournament_config, player_factory, tournament)

    return {"started": True, "participants": [p.display_name for p in participants]}


@app.websocket("/ws/tournament")
async def tournament_ws(ws: WebSocket) -> None:
    """
    Subscribe to all tournament events (bracket updates + per-game board updates).
    Replays the full event log on connect so late joiners are caught up.
    """
    await ws.accept()
    q = _tournament_broadcaster.subscribe()
    try:
        # Send replay log first so the client can reconstruct current state
        for past_event in _tournament_broadcaster.replay_log():
            await ws.send_text(json.dumps(past_event, default=str))

        # Then stream live events
        while True:
            payload = await q.get()
            await ws.send_text(json.dumps(payload, default=str))
    except WebSocketDisconnect:
        pass
    finally:
        _tournament_broadcaster.unsubscribe(q)


@app.websocket("/ws/tournament/game/{match_id}")
async def tournament_game_ws(ws: WebSocket, match_id: str) -> None:
    """
    Subscribe to GameEvents for a specific tournament match.
    Replays past events on connect (for games already in progress or finished).
    """
    await ws.accept()
    q = _tournament_broadcaster.subscribe_game(match_id)
    try:
        for past_event in _tournament_broadcaster.game_replay_log(match_id):
            await ws.send_text(json.dumps(past_event, default=str))

        while True:
            payload = await q.get()
            await ws.send_text(json.dumps(payload, default=str))
    except WebSocketDisconnect:
        pass
    finally:
        _tournament_broadcaster.unsubscribe_game(match_id, q)


# --------------------------------------------------------------------------- #
# WebSocket game                                                                #
# --------------------------------------------------------------------------- #

@app.websocket("/ws/game")
async def game_ws(ws: WebSocket) -> None:
    await ws.accept()

    try:
        # â”€â”€ First message: { type: "start", white: {...}, black: {...}, settings: {...} } â”€â”€ #
        start = await ws.receive_json()

        w = start["white"]
        b = start["black"]

        # Apply per-game settings overrides sent from the UI
        ui_settings = start.get("settings") or {}
        game_cfg = config.game
        if ui_settings:
            overrides: dict = {}
            if "max_retries" in ui_settings:
                overrides["max_retries"] = max(1, int(ui_settings["max_retries"]))
            if "show_legal_moves" in ui_settings:
                overrides["show_legal_moves"] = bool(ui_settings["show_legal_moves"])
            if ui_settings.get("board_input") in ("text", "image"):
                overrides["board_input"] = ui_settings["board_input"]
            if "annotate_pgn" in ui_settings:
                overrides["annotate_pgn"] = bool(ui_settings["annotate_pgn"])
            if "max_output_tokens" in ui_settings:
                overrides["max_output_tokens"] = max(1, int(ui_settings["max_output_tokens"]))
            if "reasoning_effort" in ui_settings:
                effort = ui_settings["reasoning_effort"]
                if effort in ("low", "medium", "high", None, "", "default", "auto", "none"):
                    overrides["reasoning_effort"] = (
                        effort if effort in ("low", "medium", "high") else None
                    )
            if overrides:
                game_cfg = replace(game_cfg, **overrides)
        session_config = replace(config, game=game_cfg)

        providers_cfg = await _providers_with_auth_overrides_async()
        white_model = _find_model_entry(providers_cfg, w["provider"], w["model_id"])
        black_model = _find_model_entry(providers_cfg, b["provider"], b["model_id"])
        white_provider = create_provider(
            w["provider"],
            w["model_id"],
            providers_cfg,
            supports_vision_override=(white_model.supports_vision if white_model else None),
        )
        black_provider = create_provider(
            b["provider"],
            b["model_id"],
            providers_cfg,
            supports_vision_override=(black_model.supports_vision if black_model else None),
        )

        white_player = create_player(
            w["provider"],
            w["name"],
            white_provider,
            session_config.game.show_legal_moves,
            session_config.game.move_timeout,
            session_config.game.max_output_tokens,
            session_config.game.reasoning_effort,
        )
        black_player = create_player(
            b["provider"],
            b["name"],
            black_provider,
            session_config.game.show_legal_moves,
            session_config.game.move_timeout,
            session_config.game.max_output_tokens,
            session_config.game.reasoning_effort,
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
                    session_config, white_player, black_player, stop_event
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
        # when the other finishes (e.g. game over â†’ no need to keep listening).
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

