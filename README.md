# ChessHarness

A CLI harness for pitting LLM providers against each other in chess. Configure any combination of OpenAI, Anthropic, Google Gemini, or Kimi models as White and Black, then watch them play — complete with move validation, check/checkmate detection, PGN export, and a full conversation log showing each model's reasoning and raw API responses.

## Setup

```bash
cp config.example.yaml config.yaml   # add your API keys
uv run python main.py
```

## Configuration

Edit `config.yaml` to define which models are available. At startup you'll be shown a numbered list and asked to pick a model for each colour.

```yaml
providers:
  openai:
    api_key: "sk-..."
    models:
      - id: gpt-4o
        name: "GPT-4o"
        supports_vision: true  # optional override; omit to auto-detect
  anthropic:
    api_key: "sk-ant-..."
    models:
      - id: claude-opus-4-6
        name: "Claude Opus"
        supports_vision: true
```

## Output

| Path | Contents |
|---|---|
| `./games/` | PGN file per game |
| `./logs/` | Full conversation log (prompts + raw responses) |

Press **Ctrl+C** to stop a game early — the partial PGN is saved automatically.

## Testing

Run tests locally with pytest:

```bash
uv run python -m pip install ".[test]"
uv run pytest -q
```

GitHub Actions runs the same `pytest -q` suite on every push and pull request.

## How sign-in works (OpenAI / Copilot)

ChessHarness now supports token-based auth both from config **and from the web setup screen**:

- `api_key` (standard API key)
- `bearer_token` (login/session/gateway token)

### OpenAI

Use either:

1. **OpenAI API key** (recommended for now)
2. **Bearer token from your own approved OAuth/gateway flow**

Example:

```yaml
providers:
  openai:
    api_key: "YOUR_OPENAI_KEY"
    # or: bearer_token: "YOUR_OPENAI_BEARER_TOKEN"
    models:
      - id: gpt-4o-mini
        name: "GPT-4o Mini"
```

### Copilot

Copilot usually requires a **Copilot-compatible bridge endpoint** that accepts your GitHub/Copilot bearer token and exposes an OpenAI-style API.

```yaml
providers:
  copilot:
    bearer_token: "YOUR_COPILOT_BEARER_TOKEN"
    base_url: "https://your-copilot-bridge.example/v1"
    models:
      - id: gpt-4.1
        name: "Copilot GPT-4.1"
```

Then run normally:

```bash
uv run python web_main.py
```

In the setup screen, use **Sign in providers** to paste/connect tokens for OpenAI or Copilot without editing `config.yaml` manually. Tokens are stored locally in `.chessharness_auth.json` for subsequent runs.

## Provider/auth roadmap

Implementation planning for subscription-style auth and OpenAI-compatible backends is tracked in:

- `docs/subscription-auth-plan.md`

This keeps README focused on usage while the evolving implementation plan lives in a dedicated doc.
