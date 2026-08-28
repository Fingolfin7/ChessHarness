from __future__ import annotations

import asyncio
import time
import unittest

from chessharness.config import Config, GameConfig
from chessharness.events import GameOverEvent, InvalidMoveEvent, MoveAppliedEvent
from chessharness.game import run_game
from chessharness.players.base import GameState, MoveResponse, Player
from chessharness.providers.base import ProviderError


class _BlockingPlayer(Player):
    def __init__(self, player_type: str = "llm") -> None:
        super().__init__("Blocking", player_type, f"{player_type}:test:blocking")
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def get_move(self, state: GameState, chunk_queue=None) -> MoveResponse:
        self.started.set()
        try:
            await asyncio.Future()
        finally:
            self.cancelled.set()


class _CancellationResistantPlayer(Player):
    def __init__(self) -> None:
        super().__init__("Stubborn", "llm", "llm:test:stubborn")
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.finished = asyncio.Event()
        self.release = asyncio.Event()
        self.chunk_queue: asyncio.Queue | None = None

    async def get_move(self, state: GameState, chunk_queue=None) -> MoveResponse:
        self.chunk_queue = chunk_queue
        self.started.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
        self.finished.set()
        return MoveResponse(raw="e4", move="e4")


class _DirectTimeoutPlayer(Player):
    def __init__(self) -> None:
        super().__init__("Direct timeout", "llm", "llm:test:direct-timeout")
        self.calls = 0

    async def get_move(self, state: GameState, chunk_queue=None) -> MoveResponse:
        self.calls += 1
        raise TimeoutError("adapter timeout")


class _ScriptedPlayer(Player):
    def __init__(self, name: str, moves: list[MoveResponse]) -> None:
        super().__init__(name, "llm", f"llm:test:{name.casefold()}")
        self._moves = iter(moves)

    async def get_move(self, state: GameState, chunk_queue=None) -> MoveResponse:
        return next(self._moves)


class _RetryingProviderPlayer(Player):
    def __init__(self) -> None:
        super().__init__("Retrying", "llm", "llm:test:retrying")
        self.calls = 0

    async def get_move(self, state: GameState, chunk_queue=None) -> MoveResponse:
        self.calls += 1
        if self.calls == 1:
            raise ProviderError(
                "test",
                "temporary timeout",
                cause=TimeoutError(),
                kind="timeout",
                retryable=True,
            )
        return MoveResponse(raw="e4", move="e4")


async def _advance_to_active_request(events) -> None:
    # GameStart, TurnStart, MoveRequested are yielded before get_move starts.
    await anext(events)
    await anext(events)
    await anext(events)


class GameReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_generator_cancels_active_move_task(self) -> None:
        white = _BlockingPlayer()
        black = _ScriptedPlayer("Black", [MoveResponse(raw="e5", move="e5")])
        events = run_game(
            Config(GameConfig(move_timeout=60, save_pgn=False), {}),
            white,
            black,
        )
        await _advance_to_active_request(events)

        pending = asyncio.create_task(anext(events))
        await asyncio.wait_for(white.started.wait(), timeout=1)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending

        await asyncio.wait_for(white.cancelled.wait(), timeout=1)
        await events.aclose()

    async def test_stop_event_interrupts_and_cancels_active_move(self) -> None:
        white = _BlockingPlayer()
        black = _ScriptedPlayer("Black", [MoveResponse(raw="e5", move="e5")])
        stop = asyncio.Event()
        events = run_game(
            Config(GameConfig(move_timeout=60, save_pgn=False), {}),
            white,
            black,
            stop_event=stop,
        )
        await _advance_to_active_request(events)

        pending = asyncio.create_task(anext(events))
        await asyncio.wait_for(white.started.wait(), timeout=1)
        stop.set()
        terminal = await asyncio.wait_for(pending, timeout=1)

        self.assertIsInstance(terminal, GameOverEvent)
        self.assertEqual(terminal.reason, "interrupted")
        await asyncio.wait_for(white.cancelled.wait(), timeout=1)
        await events.aclose()

    async def test_shared_turn_deadline_cancels_a_stalled_provider(self) -> None:
        white = _BlockingPlayer()
        black = _ScriptedPlayer("Black", [MoveResponse(raw="e5", move="e5")])
        config = Config(GameConfig(move_timeout=0.05, save_pgn=False), {})

        started = time.monotonic()
        events = []
        with self.assertRaises(ProviderError):
            async for event in run_game(config, white, black):
                events.append(event)
        elapsed = time.monotonic() - started

        failure = next(event for event in events if isinstance(event, InvalidMoveEvent))
        self.assertEqual(failure.failure_kind, "provider_timeout")
        self.assertFalse(any(isinstance(event, GameOverEvent) for event in events))
        self.assertLess(elapsed, 0.5)
        self.assertTrue(white.cancelled.is_set())

    async def test_engine_turn_uses_the_shared_deadline(self) -> None:
        white = _BlockingPlayer(player_type="engine")
        black = _ScriptedPlayer("Black", [MoveResponse(raw="e5", move="e5")])
        events = []

        with self.assertRaises(ProviderError):
            async for event in run_game(
                Config(GameConfig(move_timeout=0.05, save_pgn=False), {}),
                white,
                black,
            ):
                events.append(event)

        failure = next(event for event in events if isinstance(event, InvalidMoveEvent))
        self.assertEqual(failure.failure_kind, "engine_error")
        self.assertTrue(white.cancelled.is_set())

    async def test_cancellation_resistant_player_cannot_hang_deadline(self) -> None:
        white = _CancellationResistantPlayer()
        black = _ScriptedPlayer("Black", [MoveResponse(raw="e5", move="e5")])
        started = time.monotonic()

        try:
            with self.assertRaises(ProviderError):
                async for _ in run_game(
                    Config(GameConfig(move_timeout=0.05, save_pgn=False), {}),
                    white,
                    black,
                ):
                    pass
        finally:
            white.release.set()
            await asyncio.sleep(0)

        self.assertTrue(white.cancelled.is_set())
        self.assertLess(time.monotonic() - started, 1.5)

    async def test_cancelling_generator_cleans_chunk_queue_waiter(self) -> None:
        white = _CancellationResistantPlayer()
        black = _ScriptedPlayer("Black", [MoveResponse(raw="e5", move="e5")])
        events = run_game(
            Config(GameConfig(move_timeout=60, save_pgn=False), {}),
            white,
            black,
        )
        await _advance_to_active_request(events)

        pending = asyncio.create_task(anext(events))
        await asyncio.wait_for(white.started.wait(), timeout=1)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(pending, timeout=2)

        await asyncio.wait_for(white.cancelled.wait(), timeout=1)
        self.assertIsNotNone(white.chunk_queue)
        self.assertEqual(len(white.chunk_queue._getters), 0)

        white.release.set()
        await asyncio.wait_for(white.finished.wait(), timeout=1)
        await events.aclose()

    async def test_direct_timeout_error_is_classified_and_retried_once(self) -> None:
        white = _DirectTimeoutPlayer()
        black = _ScriptedPlayer("Black", [MoveResponse(raw="e5", move="e5")])
        events = []

        with self.assertRaises(ProviderError):
            async for event in run_game(
                Config(GameConfig(move_timeout=5, save_pgn=False), {}),
                white,
                black,
            ):
                events.append(event)

        self.assertEqual(white.calls, 2)
        self.assertTrue(
            all(
                event.failure_kind == "provider_timeout"
                for event in events
                if isinstance(event, InvalidMoveEvent)
            )
        )

    async def test_one_provider_retry_does_not_consume_model_move_retry(self) -> None:
        white = _RetryingProviderPlayer()
        black = _ScriptedPlayer("Black", [MoveResponse(raw="e5", move="e5")])
        stop = asyncio.Event()
        events = run_game(
            Config(GameConfig(move_timeout=5, save_pgn=False), {}),
            white,
            black,
            stop_event=stop,
        )

        collected = []
        async for event in events:
            collected.append(event)
            if isinstance(event, MoveAppliedEvent):
                stop.set()

        failures = [e for e in collected if isinstance(e, InvalidMoveEvent)]
        applied = [e for e in collected if isinstance(e, MoveAppliedEvent)]
        self.assertEqual(white.calls, 2)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].failure_kind, "provider_timeout")
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0].move_san, "e4")

    async def test_zero_token_completions_are_provider_failures(self) -> None:
        empty = MoveResponse(
            raw="",
            move="",
            provider_metadata={
                "finish_reason": "stop",
                "usage": {"completion_tokens": 0},
            },
        )
        white = _ScriptedPlayer("White", [empty, empty])
        black = _ScriptedPlayer("Black", [MoveResponse(raw="e5", move="e5")])

        events = []
        with self.assertRaises(ProviderError):
            async for event in run_game(
                Config(GameConfig(move_timeout=5, save_pgn=False), {}),
                white,
                black,
            ):
                events.append(event)

        failures = [e for e in events if isinstance(e, InvalidMoveEvent)]
        self.assertEqual(len(failures), 2)
        self.assertTrue(all(e.failure_kind == "provider_empty_response" for e in failures))


if __name__ == "__main__":
    unittest.main()
