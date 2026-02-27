# ChessHarness

Pit LLM providers against each other in chess. Configure any combination of OpenAI, Google Gemini, Anthropic, Kimi, or GitHub Copilot Chat models as White and Black — or run a full knockout tournament — then watch them play with move validation, check/checkmate detection, PGN export, and a live reasoning feed showing each model's thinking.

![ChessHarness demo](docs/screenshots/game-demo.gif)

---

## Features

- **Multi-provider** — OpenAI, Google Gemini, Anthropic, Kimi, GitHub Copilot Chat, OpenRouter
- **Live reasoning panel** — see each model's chain-of-thought as it streams in
- **Move history** — click any move to replay the game from that position
- **Knockout tournaments** — bracket view, byes, configurable draw handling
- **PGN export** — optionally annotated with model reasoning
- **Custom starting position** — pass any FEN to start mid-game
- **Reconnecting WebSocket** — survives network blips

---

## Screenshots

### Game Setup

Pick your models, set board input mode, token limits, and reasoning effort before starting.

![Game setup screen](docs/screenshots/01-game-setup.png)

---

### Live Game

Board, move history, player panels, and real-time reasoning — all in one view.

![Game in progress — Open Sicilian](docs/screenshots/04-game-moves.png)

The reasoning panels below the board stream each model's thinking as it arrives:

![Reasoning panel](docs/screenshots/05-game-reasoning.png)

---

### Tournament Setup

Seed up to 16 models into a knockout bracket, choose draw-handling rules, and launch.

![Tournament setup](docs/screenshots/07-tournament-setup.png)

---

## Setup

```bash
cp config.example.yaml config.yaml   # add your API keys
uv run web_main.py                   # backend on :8000
cd frontend && npm run dev           # Vite dev server on :5173
```

Then open **http://localhost:5173**.

---

## Configuration

Edit `config.yaml` to define which models are available. At startup the UI loads all connected providers automatically.

```yaml
providers:
  openai:
    api_key: "sk-..."
    models:
      - id: gpt-5.2
        name: "GPT-5.2"
        supports_vision: true
  google:
    api_key: "AIza..."
    models:
      - id: gemini-3-flash-preview
        name: "Gemini 3 Flash (Preview)"
        supports_vision: true
  anthropic:
    api_key: "sk-ant-..."
    models:
      - id: claude-sonnet-4-6
        name: "Claude Sonnet 4.6"
        supports_vision: true
```

Additional providers (`kimi`, `copilot_chat`, `openrouter`) follow the same pattern — see `config.example.yaml` for full details.

---

## Output

| Path | Contents |
|---|---|
| `./games/` | PGN file per game |
| `./logs/` | Full conversation log (prompts + raw responses) |

Press **Stop Game** or **Ctrl+C** to end a game early — the partial PGN is saved automatically.

---

## Testing

```bash
uv run python -m pip install ".[test]"
uv run pytest -q
```

GitHub Actions runs the same suite on every push and pull request.

---

## Auth

Providers can be connected in two ways:

1. **`config.yaml`** — add `api_key` or `bearer_token` before starting the server
2. **Setup screen** — paste a token in the Providers panel at runtime (saved to `.chessharness_auth.json`)

GitHub Copilot Chat supports a device-flow sign-in ("Sign in with GitHub") directly from the setup screen.
