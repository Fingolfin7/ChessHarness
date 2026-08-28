import asyncio
import time
import unittest

import chess
import chess.engine

from chessharness.players.base import GameState
from chessharness.players.engine import EnginePlayer
from chessharness.game import _cancel_and_drain_move_task


def _option(name: str, option_type: str, minimum=None, maximum=None):
    return chess.engine.Option(name, option_type, None, minimum, maximum, None)


def _options(*, elo_min: int = 1320, elo_max: int = 3190):
    return {
        "UCI_LimitStrength": _option("UCI_LimitStrength", "check"),
        "UCI_Elo": _option("UCI_Elo", "spin", elo_min, elo_max),
        "Threads": _option("Threads", "spin", 1, 128),
        "Hash": _option("Hash", "spin", 1, 131072),
    }


def _state(fen: str = chess.STARTING_FEN) -> GameState:
    return GameState(
        fen=fen,
        board_ascii="",
        legal_moves_uci=[],
        legal_moves_san=[],
        move_history_san=[],
        color="white",
        move_number=1,
    )


class _FakeTransport:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _FakeProtocol:
    def __init__(self, *, options=None, quit_error: Exception | None = None) -> None:
        self.options = options if options is not None else _options()
        self.id = {"name": "Stockfish Test", "author": "Stockfish developers"}
        self.quit_error = quit_error
        self.configure_calls: list[dict] = []
        self.play_calls: list[tuple[chess.Board, chess.engine.Limit, object]] = []
        self.quit_calls = 0

    async def configure(self, options):
        self.configure_calls.append(dict(options))

    async def play(self, board, limit, *, info):
        self.play_calls.append((board, limit, info))
        return chess.engine.PlayResult(
            chess.Move.from_uci("e2e4"),
            None,
            {
                "nodes": 1234,
                "depth": 7,
                "score": chess.engine.PovScore(chess.engine.Cp(15), chess.WHITE),
            },
        )

    async def quit(self):
        self.quit_calls += 1
        if self.quit_error is not None:
            raise self.quit_error


class _CancellationResistantProtocol(_FakeProtocol):
    def __init__(self) -> None:
        super().__init__()
        self.play_started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def play(self, board, limit, *, info):
        self.play_started.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
        return await super().play(board, limit, info=info)


class _FakeFactory:
    def __init__(self, protocol: _FakeProtocol) -> None:
        self.protocol = protocol
        self.transport = _FakeTransport()
        self.calls: list[str] = []

    async def __call__(self, command: str):
        self.calls.append(command)
        return self.transport, self.protocol


class EnginePlayerTests(unittest.IsolatedAsyncioTestCase):
    async def test_lazy_start_configures_and_plays_fixed_node_search(self) -> None:
        protocol = _FakeProtocol()
        factory = _FakeFactory(protocol)
        player = EnginePlayer(
            "Stockfish 1600",
            engine_path="stockfish-test",
            uci_elo=1600,
            node_limit=42_000,
            threads=2,
            hash_mb=128,
            competitor_id="engine:stockfish:test-profile",
            engine_factory=factory,
        )

        self.assertEqual(factory.calls, [])
        self.assertEqual(player.competitor_id, "engine:stockfish:test-profile")
        response = await player.get_move(_state())

        self.assertEqual(factory.calls, ["stockfish-test"])
        self.assertEqual(
            protocol.configure_calls,
            [{"UCI_LimitStrength": True, "UCI_Elo": 1600, "Threads": 2, "Hash": 128}],
        )
        board, limit, info = protocol.play_calls[0]
        self.assertEqual(board.fen(), chess.STARTING_FEN)
        self.assertEqual(limit.nodes, 42_000)
        self.assertEqual(info, chess.engine.INFO_BASIC)
        self.assertEqual(response.raw, "e2e4")
        self.assertEqual(response.move, "e2e4")
        self.assertEqual(response.provider_metadata["engine_name"], "Stockfish Test")
        self.assertEqual(response.provider_metadata["nodes"], 1234)
        self.assertNotIn("score", response.provider_metadata)

    async def test_reuses_one_process_across_moves(self) -> None:
        protocol = _FakeProtocol()
        factory = _FakeFactory(protocol)
        player = EnginePlayer("SF", engine_factory=factory)

        await player.get_move(_state())
        await player.get_move(_state())

        self.assertEqual(len(factory.calls), 1)
        self.assertEqual(len(protocol.configure_calls), 1)
        self.assertEqual(len(protocol.play_calls), 2)

    async def test_rejects_elo_outside_engine_advertised_range_and_cleans_up(self) -> None:
        protocol = _FakeProtocol(options=_options(elo_min=1400, elo_max=2000))
        factory = _FakeFactory(protocol)
        player = EnginePlayer("SF", uci_elo=2100, engine_factory=factory)

        with self.assertRaisesRegex(ValueError, "above the engine maximum 2000"):
            await player.get_move(_state())

        self.assertEqual(protocol.configure_calls, [])
        self.assertEqual(protocol.quit_calls, 1)

    async def test_missing_required_option_fails_and_cleans_up(self) -> None:
        options = _options()
        del options["UCI_LimitStrength"]
        protocol = _FakeProtocol(options=options)
        factory = _FakeFactory(protocol)
        player = EnginePlayer("SF", engine_factory=factory)

        with self.assertRaisesRegex(chess.engine.EngineError, "UCI_LimitStrength"):
            await player.get_move(_state())

        self.assertEqual(protocol.quit_calls, 1)

    async def test_close_is_idempotent(self) -> None:
        protocol = _FakeProtocol()
        factory = _FakeFactory(protocol)
        player = EnginePlayer("SF", engine_factory=factory)
        await player.get_move(_state())

        await player.close()
        await player.close()

        self.assertEqual(protocol.quit_calls, 1)
        self.assertEqual(factory.transport.close_calls, 0)

    async def test_close_falls_back_to_transport_when_quit_fails(self) -> None:
        protocol = _FakeProtocol(quit_error=RuntimeError("broken pipe"))
        factory = _FakeFactory(protocol)
        player = EnginePlayer("SF", engine_factory=factory)
        await player.get_move(_state())

        await player.close()
        await player.close()

        self.assertEqual(protocol.quit_calls, 1)
        self.assertEqual(factory.transport.close_calls, 1)

    async def test_bounded_cleanup_force_closes_a_stuck_engine_play(self) -> None:
        protocol = _CancellationResistantProtocol()
        factory = _FakeFactory(protocol)
        player = EnginePlayer("SF", engine_factory=factory)
        move_task = asyncio.create_task(player.get_move(_state()))

        await asyncio.wait_for(protocol.play_started.wait(), timeout=1)
        move_task.cancel()
        await asyncio.wait_for(protocol.cancelled.wait(), timeout=1)

        started = time.monotonic()
        await asyncio.wait_for(
            _cancel_and_drain_move_task(move_task, player),
            timeout=2,
        )
        self.assertLess(time.monotonic() - started, 1.75)
        self.assertEqual(factory.transport.close_calls, 1)
        self.assertIsNone(player._transport)
        self.assertIsNone(player._protocol)

        protocol.release.set()
        await asyncio.wait_for(move_task, timeout=1)
        await player.close()
        self.assertEqual(protocol.quit_calls, 0)

    async def test_startup_is_safe_under_concurrent_first_moves(self) -> None:
        protocol = _FakeProtocol()
        factory = _FakeFactory(protocol)
        player = EnginePlayer("SF", engine_factory=factory)

        await asyncio.gather(player.get_move(_state()), player.get_move(_state()))

        self.assertEqual(len(factory.calls), 1)

    def test_rejects_non_positive_resource_limits(self) -> None:
        for kwargs in ({"node_limit": 0}, {"threads": 0}, {"hash_mb": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                EnginePlayer("SF", **kwargs)


if __name__ == "__main__":
    unittest.main()
