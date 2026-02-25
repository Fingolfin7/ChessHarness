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
  anthropic:
    api_key: "sk-ant-..."
    models:
      - id: claude-opus-4-6
        name: "Claude Opus"
```

## Output

| Path | Contents |
|---|---|
| `./games/` | PGN file per game |
| `./logs/` | Full conversation log (prompts + raw responses) |

Press **Ctrl+C** to stop a game early — the partial PGN is saved automatically.
