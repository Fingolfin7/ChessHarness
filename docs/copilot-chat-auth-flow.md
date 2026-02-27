# Copilot Chat Auth Flow (Implementation Note)

This note documents the current `copilot_chat` integration used by the web app.

## Why `copilot_chat` exists

The old `copilot` path used `https://models.inference.ai.azure.com` (GitHub Models marketplace).
That endpoint applies marketplace quotas and does not match IDE/OpenCode Copilot usage.

`copilot_chat` switches the app to `https://api.githubcopilot.com` with a short-lived Copilot token, which behaves more like IDE/OpenCode integrations.

## Current flow (web UI)

1. User starts device auth in the web UI (`Sign in with GitHub`).
2. Backend runs GitHub device flow and receives a GitHub OAuth token.
3. Backend exchanges that token at `GET https://api.github.com/copilot_internal/v2/token`.
4. Exchange returns a short-lived Copilot token.
5. Backend stores:
   - `copilot_chat` (Copilot access token)
   - `copilot_chat__github_token` (GitHub token, used for future exchange)
   - `copilot_chat__expires_at` (expiry timestamp)
6. Requests to the provider use `https://api.githubcopilot.com`.
7. Before auth checks and before starting a game, backend refreshes the Copilot token if expiry is near.

## IDE-style headers (required)

Copilot Chat rejects IDE-auth requests unless IDE metadata headers are present.

Current headers sent on `copilot_chat` requests:

- `Editor-Version: vscode/1.95.3`
- `Editor-Plugin-Version: copilot-chat/0.22.1`
- `Copilot-Integration-Id: vscode-chat`

These are passed as `default_headers` to the OpenAI SDK client for the `copilot_chat` provider.

What Copilot sees from this app:

- Copilot bearer token (short-lived)
- `api.githubcopilot.com` endpoint
- OpenAI-compatible chat/completions payload
- IDE-style metadata headers for compatibility

## Model IDs and discovery

GitHub Models marketplace model IDs are not the same as Copilot Chat model IDs.
After switching endpoints, marketplace IDs (for example `openai/gpt-5`) can fail with `model_not_supported`.

To fix this, use the one-off discovery script:

- [`scripts/discover_copilot_chat_models.py`](/C:/Users/mushu/PycharmProjects/ChessHarness/scripts/discover_copilot_chat_models.py)

What it does:

1. Reads `.chessharness_auth.json`
2. Reuses stored `copilot_chat__github_token`
3. Exchanges for a Copilot token
4. Calls `models.list()` on `api.githubcopilot.com`
5. Filters out embedding models
6. Rewrites the Copilot provider block in:
   - [`config.yaml`](/C:/Users/mushu/PycharmProjects/ChessHarness/config.yaml)
   - [`config.example.yaml`](/C:/Users/mushu/PycharmProjects/ChessHarness/config.example.yaml)

## Compatibility behavior

- Legacy `copilot` auth keys are migrated to `copilot_chat` on startup.
- Legacy `/api/auth/copilot/...` routes are still accepted as aliases.
- Backend still accepts provider name `copilot` as a compatibility alias in provider construction.

## Caveats

- `copilot_internal/v2/token` is an internal path and may change.
- Copilot may require additional headers or tighter client checks later.
- Dynamic `models.list()` reflects what is visible now, not necessarily what stays supported long-term.

## Relevant implementation files

- [`chessharness/web/app.py`](/C:/Users/mushu/PycharmProjects/ChessHarness/chessharness/web/app.py)
- [`chessharness/providers/openai.py`](/C:/Users/mushu/PycharmProjects/ChessHarness/chessharness/providers/openai.py)
- [`chessharness/providers/__init__.py`](/C:/Users/mushu/PycharmProjects/ChessHarness/chessharness/providers/__init__.py)
