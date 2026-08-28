import asyncio
import unittest

from chessharness.players.base import (
    GameState,
    MoveResponse,
    Player,
    close_players_bounded,
)
from chessharness.players.llm import LLMPlayer
from chessharness.providers.base import LLMProvider, Message, ProviderError


def _state() -> GameState:
    return GameState(
        fen="startpos-fen",
        board_ascii="ASCII-BOARD",
        legal_moves_uci=["e2e4"],
        legal_moves_san=["e4"],
        move_history_san=[],
        color="white",
        move_number=1,
    )


class _ControlledProvider(LLMProvider):
    def __init__(self, *, close_failures: int = 0) -> None:
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self.close_failures = close_failures
        self.close_calls = 0
        self.stream_calls = 0

    @property
    def supports_vision(self) -> bool:
        return False

    async def complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 5120,
        reasoning_effort: str | None = None,
    ) -> str:
        return "## Move\ne4"

    async def stream(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 5120,
        reasoning_effort: str | None = None,
    ):
        self.stream_calls += 1
        yield "## Reasoning\nready\n\n## Move\ne4\n"

    async def close(self) -> None:
        self.close_calls += 1
        self.close_started.set()
        await self.release_close.wait()
        if self.close_failures:
            self.close_failures -= 1
            raise RuntimeError("close failed")


class _SlowPlayer(Player):
    def __init__(self) -> None:
        super().__init__("slow", player_type="llm")
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self.closed = asyncio.Event()

    async def get_move(
        self,
        state: GameState,
        chunk_queue: asyncio.Queue | None = None,
    ) -> MoveResponse:
        return MoveResponse(raw="e4", move="e4")

    async def close(self) -> None:
        self.close_started.set()
        await self.release_close.wait()
        self.closed.set()


class PlayerCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_close_waiters_share_a_shielded_provider_close(self) -> None:
        provider = _ControlledProvider()
        player = LLMPlayer("model", provider)

        first = asyncio.create_task(player.close())
        await provider.close_started.wait()
        second = asyncio.create_task(player.close())

        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first

        self.assertFalse(player._closed)
        self.assertTrue(player._closing)
        self.assertEqual(provider.close_calls, 1)

        provider.release_close.set()
        await second

        self.assertTrue(player._closed)
        self.assertEqual(provider.close_calls, 1)

    async def test_failed_close_does_not_poison_player_and_can_be_retried(self) -> None:
        provider = _ControlledProvider(close_failures=1)
        player = LLMPlayer("model", provider)

        first = asyncio.create_task(player.close())
        await provider.close_started.wait()
        provider.release_close.set()
        with self.assertRaisesRegex(RuntimeError, "close failed"):
            await first

        self.assertFalse(player._closed)
        self.assertFalse(player._closing)

        provider.close_started.clear()
        provider.release_close.clear()
        second = asyncio.create_task(player.close())
        await provider.close_started.wait()
        provider.release_close.set()
        await second

        self.assertTrue(player._closed)
        self.assertEqual(provider.close_calls, 2)

    async def test_closing_player_rejects_new_move_without_reusing_provider(self) -> None:
        provider = _ControlledProvider()
        player = LLMPlayer("model", provider)

        close_task = asyncio.create_task(player.close())
        await provider.close_started.wait()

        with self.assertRaises(ProviderError) as caught:
            await player.get_move(_state())

        self.assertFalse(caught.exception.retryable)
        self.assertEqual(provider.stream_calls, 0)

        provider.release_close.set()
        await close_task

    async def test_bounded_cleanup_returns_and_finishes_in_background(self) -> None:
        player = _SlowPlayer()

        cleanup = asyncio.create_task(close_players_bounded([player], timeout=0.01))
        await player.close_started.wait()
        await cleanup

        self.assertFalse(player.closed.is_set())
        player.release_close.set()
        await asyncio.wait_for(player.closed.wait(), timeout=1)

    async def test_bounded_cleanup_preserves_caller_cancellation(self) -> None:
        player = _SlowPlayer()

        cleanup = asyncio.create_task(close_players_bounded([player], timeout=1))
        await player.close_started.wait()
        cleanup.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await cleanup

        # The shielded resource cleanup remains alive after the cancelled
        # caller exits and can still finish normally.
        player.release_close.set()
        await asyncio.wait_for(player.closed.wait(), timeout=1)


if __name__ == "__main__":
    unittest.main()
