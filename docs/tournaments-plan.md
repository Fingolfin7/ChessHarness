# Tournament Mode — Implementation Plan

This document describes the architecture and phased implementation for multi-model chess tournaments in ChessHarness.

## Goals

- Let users select N AI models to compete in a structured tournament
- Start with **Knock-out** (single-elimination) as the first format
- Architect for easy addition of **Round Robin**, **Swiss**, and **Arena** later
- Reuse the existing `game.py` loop unchanged — tournaments are a scheduling layer on top
- Follow the same event-driven, UI-agnostic design as the rest of the project

---

## Architecture Overview

```
tournament_main.py            ← new CLI entry point for tournament mode
chessharness/
├── tournaments/
│   ├── __init__.py           ← create_tournament() factory
│   ├── base.py               ← Tournament ABC + shared dataclasses
│   ├── events.py             ← frozen TournamentEvent dataclasses
│   ├── knockout.py           ← KnockoutTournament (Phase 1)
│   ├── round_robin.py        ← RoundRobinTournament (stub → Phase 2)
│   ├── swiss.py              ← SwissTournament (stub → Phase 3)
│   └── arena.py              ← ArenaTournament (stub → Phase 4)
├── cli/
│   ├── tournament_display.py ← Rich consumer for TournamentEvent stream
│   └── tournament_selector.py← Pick participants + tournament settings
```

The **key principle**: `game.py` is never modified. The tournament layer:
1. Decides *who* plays *whom* (pairings)
2. Calls `run_game()` for each match
3. Records results and advances brackets/standings
4. Yields `TournamentEvent` objects — same frozen-dataclass pattern as game events

---

## Shared Dataclasses (`tournaments/base.py`)

```python
@dataclass(frozen=True)
class TournamentParticipant:
    provider_name: str
    model: ModelEntry           # from chessharness.config

@dataclass(frozen=True)
class MatchResult:
    match_id: str               # e.g. "QF-1", "SF-2"
    white: TournamentParticipant
    black: TournamentParticipant
    game_result: GameResult     # "1-0" | "0-1" | "1/2-1/2" | "*"
    pgn: str
    winner: TournamentParticipant | None   # None on draw

@dataclass
class StandingEntry:
    participant: TournamentParticipant
    wins: int
    losses: int
    draws: int
    points: float               # wins=1, draws=0.5

class Tournament(ABC):
    @abstractmethod
    async def run(
        self,
        participants: list[TournamentParticipant],
        game_config: GameConfig,
        players: dict[TournamentParticipant, Player],
    ) -> AsyncIterator[TournamentEvent]: ...

    @abstractmethod
    def standings(self) -> list[StandingEntry]: ...
```

---

## Tournament Events (`tournaments/events.py`)

All events are `@dataclass(frozen=True)`, following `chessharness/events.py`:

| Event | Fields | When |
|---|---|---|
| `TournamentStartEvent` | tournament_type, participants, bracket | Before round 1 |
| `RoundStartEvent` | round_num, total_rounds, pairings | Start of each round |
| `MatchStartEvent` | match_id, white, black, round_num | Before each game |
| `MatchCompleteEvent` | match_id, result: MatchResult, advancing | After each game |
| `RoundCompleteEvent` | round_num, results, standings | After all matches in a round |
| `TournamentCompleteEvent` | winner, final_standings, all_results | Tournament over |

Union type: `TournamentEvent = TournamentStartEvent | RoundStartEvent | ...`

The display layer pattern-matches on these, just like `cli/display.py` does for game events.

---

## Knock-out Tournament (`tournaments/knockout.py`) — Phase 1

### Rules
- Single-elimination: lose once → out
- Each match is **1 game** — colour (white/black) is assigned randomly per match
- Winner of the game advances; on draw → sudden-death rematch with colours swapped (configurable)
- If N participants is not a power of 2: top seeds get **byes** in round 1

### Bracket Generation
```
8-player example:
Round 1 (QF): [1v8, 4v5, 2v7, 3v6]
Round 2 (SF): [QF winner 1 v QF winner 2, QF winner 3 v QF winner 4]
Round 3 (F):  [SF winner 1 v SF winner 2]
```
Seeding is determined by participant selection order (first picked = seed 1).

### Concurrency
- Matches **within a round** can run concurrently (different models, independent games)
- Use `asyncio.gather()` per-round, stream events via an `asyncio.Queue` aggregated in order
- Matches **across rounds** are sequential (bracket dependency)

### Draw Handling (configurable)
- `"rematch"` — play a sudden-death game with colours swapped until there is a winner (default)
- `"coin_flip"` — random advancement on draw (no rematch)
- `"seed"` — higher seed advances on draw (no rematch)

---

## CLI Entry Point (`tournament_main.py`)

```python
# Usage: uv run python tournament_main.py
async def main():
    config = load_config()
    participants = await select_tournament_participants(config)
    tournament_config = await select_tournament_settings()  # type, games_per_match, draw_handling
    tournament = create_tournament(tournament_config.type)

    players = {p: create_player(p, config) for p in participants}

    async for event in tournament.run(participants, config.game, players):
        await display_tournament_event(event)   # tournament_display.py
```

---

## CLI Display (`cli/tournament_display.py`)

Consumes `TournamentEvent` stream using Rich. Key visuals:

- `TournamentStartEvent` → bracket panel (ASCII bracket tree)
- `RoundStartEvent` → round header + pairing table
- `MatchStartEvent` → "▶ Match QF-1: [ModelA] vs [ModelB]" then delegates to game display
- `MatchCompleteEvent` → winner chip + score, crossed-out loser
- `RoundCompleteEvent` → updated bracket showing survivors
- `TournamentCompleteEvent` → champion banner + full results table

For each individual game inside a match the existing `cli/display.py` handlers are
**reused directly** — `tournament_display.py` calls through to them for per-move events.

---

## Participant Selection (`cli/tournament_selector.py`)

Extends the existing `selector.py` pattern:

1. Show the same numbered model table
2. User picks models one at a time (enter blank to stop, min 2)
3. Warn if count is not a power of 2 (byes will be assigned)
4. Confirm list + seeding order

---

## Config Extension (`config.example.yaml`)

A new optional top-level `tournament` block:

```yaml
tournament:
  type: knockout          # knockout | round_robin | swiss | arena
  draw_handling: rematch  # rematch | coin_flip | seed
  save_pgn: true          # save all match PGNs to pgn_dir
  concurrent_matches: true # run intra-round matches in parallel
```

The `tournament` block is optional; absent = use interactive prompts.

---

## Future Tournament Types

### Round Robin (`round_robin.py`) — Phase 2
- Every participant plays every other participant (home + away)
- No elimination; final standings by total points
- Pairings: round-robin schedule algorithm (circle method)
- Concurrency: all games in a round run in parallel

### Swiss (`swiss.py`) — Phase 3
- Fixed N rounds (typically log₂(participants))
- After each round, pair players with equal/similar scores
- No repeat matchups
- Final standings by points + tiebreaks (Buchholz, Sonneborn-Berger)

### Arena (`arena.py`) — Phase 4
- Time-limited (e.g. 30 minutes wall clock)
- Finished a game → immediately paired again
- Score = sum of wins (bonus point for consecutive wins)
- All games run concurrently

---

## Phased Implementation Plan

### Phase 1 — Knock-out (this branch)
- [ ] `chessharness/tournaments/__init__.py` — `create_tournament()` factory
- [ ] `chessharness/tournaments/base.py` — `Tournament` ABC, `TournamentParticipant`, `MatchResult`, `StandingEntry`
- [ ] `chessharness/tournaments/events.py` — all `TournamentEvent` frozen dataclasses
- [ ] `chessharness/tournaments/knockout.py` — `KnockoutTournament` implementation
- [ ] `chessharness/tournaments/round_robin.py` — stub (raises `NotImplementedError`)
- [ ] `chessharness/tournaments/swiss.py` — stub
- [ ] `chessharness/tournaments/arena.py` — stub
- [ ] `chessharness/cli/tournament_display.py` — Rich consumer
- [ ] `chessharness/cli/tournament_selector.py` — participant picker
- [ ] `tournament_main.py` — CLI entry point
- [ ] `tests/test_knockout_tournament.py` — bracket generation + result recording
- [ ] Update `config.example.yaml` with `tournament:` block
- [ ] Update `README.md` with tournament usage

### Phase 2 — Round Robin
- [ ] Implement `round_robin.py`
- [ ] Add standings table to display
- [ ] Tests

### Phase 3 — Swiss
- [ ] Implement `swiss.py` with tiebreak scoring
- [ ] Tests

### Phase 4 — Arena
- [ ] Implement `arena.py` with wall-clock timer
- [ ] Concurrency stress tests

---

## Testing Strategy

- Unit-test bracket generation with mock participants and results (no LLM calls)
- Use a `MockPlayer` that returns the first legal move instantly — same pattern as existing tests
- Assert correct bracket advancement, bye logic, and draw-handling variants
- Integration test: 2-participant knockout (1 match, 1 game) with mock players end-to-end

---

## Web UI — Tournament Broadcast View (Phase 1)

Inspired by Lichess tournament broadcasts: a multi-board overview where the user can click
into any live game to watch it in detail.

### Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  Knockout — Round 2 (Semi-finals)          [bracket button]     │
├──────────────┬──────────────┬──────────────┬────────────────────┤
│  QF-1 ●LIVE │  QF-2 ●LIVE │  QF-3 ●DONE │  QF-4 ●DONE        │
│  [mini board]│  [mini board]│  [mini board]│  [mini board]      │
│  GPT-4o      │  Gemini Pro  │  Claude ✓   │  Llama ✓           │
│  vs Llama    │  vs Claude   │  1-0 (24)   │  0-1 (31)          │
└──────────────┴──────────────┴──────────────┴────────────────────┘
        ↓ click any live/finished card
┌─────────────────────────────────────────────────────────────────┐
│  ← Back to overview                                             │
│  [full board]          │  GPT-4o (White)                        │
│                        │  vs Llama (Black)                      │
│                        │  Move 14 — White to move               │
│                        │  ┌──────────────────────────────┐      │
│                        │  │ 1.e4 e5  2.Nf3 Nc6  3.Bb5…  │      │
│                        │  └──────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────┘
```

### Backend — WebSocket API

Each concurrent game in a round gets its own WebSocket channel:

```
GET  /tournament/status          → full bracket JSON (snapshot)
WS   /tournament/events          → TournamentEvent stream (all games)
WS   /tournament/game/{match_id} → GameEvent stream for one game
```

`TournamentEvent` objects are serialised with `dataclasses.asdict()` — same pattern as
the existing single-game WebSocket in `chessharness/web/app.py`.

The frontend subscribes to `/tournament/events` for the overview (board thumbnails + status
chips) and to `/tournament/game/{match_id}` when the user clicks into a game.

### Frontend Components (React)

| Component | Responsibility |
|---|---|
| `TournamentPage` | top-level: overview vs detail routing |
| `BracketPanel` | collapsible bracket tree (slide-in from right) |
| `GameGrid` | CSS grid of `GameCard` tiles |
| `GameCard` | mini `react-chessboard` + player names + status chip (LIVE/DONE/BYE) + move count; pulses on new move |
| `GameDetail` | full board + scrollable move list (SAN pairs) + player headers; mirrors the existing single-game view |
| `MoveList` | move-number rows, current move highlighted, click to jump to position |

### State Management

- `useTournamentSocket()` hook: subscribes to `/tournament/events`, maintains
  `{ matches: Map<match_id, MatchState> }` in React state
- `useGameSocket(matchId)` hook: subscribes to `/tournament/game/{matchId}` only while
  the detail view is open — disconnects on navigate back
- `MatchState`: `{ status, fen, lastMove, moveSan[], white, black }`

### Key UX Behaviours

- Clicking a `GameCard` navigates to `GameDetail` without interrupting other live games
- `GameCard` mini boards update live (FEN pushed on every `MoveAppliedEvent`)
- Status chip: green "LIVE" pulse while game running, grey "DONE" with result badge when over
- Bracket panel overlay shows the bracket tree at any time; advancing models are highlighted

---

## Non-Goals (out of scope for Phase 1)

- Persistent tournament state / resume after crash
- ELO rating updates
