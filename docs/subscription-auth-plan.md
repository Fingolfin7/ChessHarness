# Subscription/Auth Expansion Plan

This file tracks implementation work for adding non-API-key access paths and lower-cost provider routing.

## Priorities (current)

1. OpenAI (existing, keep first-class)
2. Copilot-compatible endpoints (OpenAI-compatible transport)
3. Groq (OpenAI-compatible endpoint)
4. OpenRouter (OpenAI-compatible endpoint)
5. Anthropic subscription login: paused pending current provider support/policy changes

## Phase 1 (implemented in this repo)

- Add provider aliases for OpenAI-compatible backends:
  - `copilot`
  - `groq`
  - `openrouter`
- Allow `api_key` **or** `bearer_token` in provider config.
- Allow per-provider custom `base_url` while still using shared OpenAI transport.

## Phase 2

- Introduce explicit `auth_mode` (`api_key`, `oauth_subscription`, `custom_endpoint`).
- Add token store abstraction for OS keychain-backed credentials.
- Add login/refresh flows where officially supported.

## Phase 3

- Web UI connect/disconnect states for subscription auth.
- Budget caps by provider/auth mode.
- Telemetry around token refresh failures and auth fallbacks.

## Implemented reference notes

- Copilot Chat auth flow (device login, token exchange, IDE headers, model discovery):
  - [`docs/copilot-chat-auth-flow.md`](/C:/Users/mushu/PycharmProjects/ChessHarness/docs/copilot-chat-auth-flow.md)

## Safety constraints

- Prefer official OAuth/device-code flows.
- Do not rely on scraped consumer web session cookies in production.
- Keep API-key path as fallback to preserve playability.

## User sign-in UX target

Planned in-app sign-in flow (not fully implemented yet):

1. User clicks `Connect OpenAI` or `Connect Copilot` in the web UI.
2. App starts provider OAuth/device flow (or launches external bridge login).
3. Provider returns access token (+ refresh token when available).
4. App stores tokens in OS secure storage and writes only provider reference in app config.
5. Provider adapters fetch fresh bearer token on demand and auto-refresh on expiry.

Until this exists, users sign in externally and paste `api_key`/`bearer_token` into `config.yaml`.


### Implemented now: in-app token connect flow

- Web setup screen now exposes `Sign in providers` for OpenAI/Copilot token connect/disconnect.
- Backend endpoints (`/api/auth/providers`, `/api/auth/connect`, `/api/auth/disconnect`) persist tokens to `.chessharness_auth.json`.
- Game provider construction applies stored auth token overrides at runtime.
