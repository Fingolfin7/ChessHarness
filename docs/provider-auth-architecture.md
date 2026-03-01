# Provider Auth Architecture

This document explains how ChessHarness authenticates and calls each provider family:

1. `openai` (standard OpenAI API key/bearer token)
2. `openai_chatgpt` (Codex/ChatGPT session-style auth via local Codex login)
3. `copilot_chat` (GitHub device flow + Copilot token exchange)

---

## 1) `openai` (standard API-key path)

### Auth source
- User pastes token in UI or config defines `providers.openai.api_key` / `bearer_token`.
- Stored in `.chessharness_auth.json` as `openai`.

### Runtime call path
```text
ModelPicker (Connect openai)
  -> POST /api/auth/connect { provider: "openai", token }
  -> _verify_token_detailed("openai")
      -> AsyncOpenAI(...).models.list()
  -> auth_tokens["openai"] = token

Game/Tournament start
  -> create_provider("openai", model_id, ...)
  -> OpenAIProvider
  -> chat.completions.create(stream=True)
```

### Protocol shape
- OpenAI Chat Completions API
- Message content supports text/image conversion in `OpenAIProvider._build_api_messages`.

---

## 2) `openai_chatgpt` (Codex/ChatGPT path)

### Auth source
- User clicks **Use Codex Login**.
- Backend imports token from `~/.codex/auth.json` and stores as `openai_chatgpt`.

### Runtime call path
```text
ModelPicker (Use Codex Login)
  -> POST /api/auth/openai_chatgpt/codex/connect
  -> _load_codex_auth_payload() from ~/.codex/auth.json
  -> _extract_codex_openai_token(...)
  -> auth_tokens["openai_chatgpt"] = token
  -> auth_tokens["openai_chatgpt__source"] = "codex_auth"

Game/Tournament start
  -> create_provider("openai_chatgpt", model_id, ...)
  -> OpenAIChatGPTProvider
  -> responses.create(stream=True) to https://chatgpt.com/backend-api/codex
```

### Protocol shape and endpoint constraints
- Uses Responses-style payload:
  - `system` message -> top-level `instructions`
  - conversation turns -> `input`
- Role-specific content part types:
  - `user` -> `input_text`
  - `assistant` -> `output_text`
- Endpoint requires streaming (`stream=true`).
- Provider includes compatibility fallbacks:
  - retries without `max_output_tokens` if unsupported
  - retries without `reasoning` if unsupported

### Token refresh behavior
- If source is `codex_auth`, refresher reloads latest token from `~/.codex/auth.json` before requests.

---

## 3) `copilot_chat` (GitHub device-flow path)

### Auth source
- User clicks **Sign in with GitHub** for Copilot.
- Backend runs GitHub device flow + exchange to Copilot access token.

### Runtime call path
```text
ModelPicker (Copilot Sign In)
  -> POST /api/auth/copilot_chat/device/start
      -> github.com/login/device/code
  -> POST /api/auth/copilot_chat/device/poll
      -> github.com/login/oauth/access_token
      -> api.github.com/copilot_internal/v2/token
  -> auth_tokens["copilot_chat"] = short-lived Copilot token
  -> auth_tokens["copilot_chat__github_token"] = GitHub token

Game/Tournament start
  -> create_provider("copilot_chat", model_id, ...)
  -> OpenAIProvider(base_url=https://api.githubcopilot.com, default_headers=...)
  -> chat.completions.create(stream=True)
```

### Token refresh behavior
- Copilot refresher uses stored GitHub token to mint new short-lived Copilot token when needed.

---

## Why there are separate providers

`openai`, `openai_chatgpt`, and `copilot_chat` are intentionally separate because they differ in:

- Token provenance and scopes
- Endpoint base URL
- Request protocol expectations
- Refresh mechanics

Trying to collapse them into one path causes brittle auth/protocol failures (scope mismatch, unsupported params, required stream mode, content-part type errors).

---

## File map

- Provider factory:
  - `chessharness/providers/__init__.py`
- Standard OpenAI provider:
  - `chessharness/providers/openai.py`
- ChatGPT/Codex provider:
  - `chessharness/providers/openai_chatgpt.py`
- Web auth + token orchestration:
  - `chessharness/web/app.py`
- Frontend provider auth UI:
  - `frontend/src/components/ModelPicker.jsx`
  - `frontend/src/context/AppContext.jsx`
