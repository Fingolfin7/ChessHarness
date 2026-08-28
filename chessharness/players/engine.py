"""UCI chess-engine player (for example, a fixed-strength Stockfish)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import chess
import chess.engine

from chessharness.players.base import GameState, MoveResponse, Player


EngineFactory = Callable[[str], Awaitable[tuple[Any, Any]]]


class EnginePlayer(Player):
    """A lazily started, reusable UCI engine player.

    ``uci_elo`` is the engine's advertised UCI strength target rather than a
    claim about a human/FIDE rating. Search work is fixed by node count so a
    benchmark does not change merely because the host is temporarily busy.

    ``engine_factory`` is injectable to allow tests and alternative UCI
    launchers. It must have the same async return shape as
    :func:`chess.engine.popen_uci`.
    """

    _REQUIRED_OPTIONS = (
        "UCI_LimitStrength",
        "UCI_Elo",
        "Threads",
        "Hash",
    )

    def __init__(
        self,
        name: str,
        engine_path: str = "stockfish",
        uci_elo: int = 1600,
        node_limit: int = 100_000,
        threads: int = 1,
        hash_mb: int = 64,
        *,
        competitor_id: str | None = None,
        engine_factory: EngineFactory | None = None,
    ) -> None:
        super().__init__(name, player_type="engine", competitor_id=competitor_id)
        if not engine_path:
            raise ValueError("engine_path must not be empty")
        if node_limit <= 0:
            raise ValueError("node_limit must be greater than zero")
        if threads <= 0:
            raise ValueError("threads must be greater than zero")
        if hash_mb <= 0:
            raise ValueError("hash_mb must be greater than zero")

        self._engine_path = engine_path
        self._uci_elo = uci_elo
        self._node_limit = node_limit
        self._threads = threads
        self._hash_mb = hash_mb
        self._engine_factory = engine_factory or chess.engine.popen_uci
        self.is_rating_anchor = True
        self.anchor_rating = float(uci_elo)

        self._transport: Any | None = None
        self._protocol: Any | None = None
        # UCI protocols are stateful. This lock both makes lazy startup safe
        # and prevents concurrent play/close operations on one process.
        self._lifecycle_lock = asyncio.Lock()

    async def get_move(
        self,
        state: GameState,
        chunk_queue: asyncio.Queue | None = None,
    ) -> MoveResponse:
        del chunk_queue  # Engines produce a completed move, not text chunks.
        board = chess.Board(state.fen)

        async with self._lifecycle_lock:
            protocol = await self._ensure_engine()
            result = await protocol.play(
                board,
                chess.engine.Limit(nodes=self._node_limit),
                info=chess.engine.INFO_BASIC,
            )

            if result.move is None:
                raise chess.engine.EngineError(
                    f"UCI engine {self.name!r} did not return a move"
                )

            move = result.move.uci()
            metadata = self._metadata(protocol, result.info)

        return MoveResponse(
            raw=move,
            move=move,
            reasoning="",
            provider_metadata=metadata,
        )

    async def _ensure_engine(self) -> Any:
        """Start and configure the engine. Caller must hold the lifecycle lock."""
        if self._protocol is not None:
            return self._protocol

        transport, protocol = await self._engine_factory(self._engine_path)
        self._transport = transport
        self._protocol = protocol
        try:
            options = self._canonical_options(protocol.options)
            missing = [name for name in self._REQUIRED_OPTIONS if name not in options]
            if missing:
                raise chess.engine.EngineError(
                    "UCI engine is missing required option(s): " + ", ".join(missing)
                )

            elo_option = options["UCI_Elo"]
            elo_min = elo_option.min
            elo_max = elo_option.max
            if elo_min is not None and self._uci_elo < elo_min:
                raise ValueError(
                    f"uci_elo {self._uci_elo} is below the engine minimum {elo_min}"
                )
            if elo_max is not None and self._uci_elo > elo_max:
                raise ValueError(
                    f"uci_elo {self._uci_elo} is above the engine maximum {elo_max}"
                )

            # Let python-chess validate the remaining spin-option ranges using
            # the exact option definitions advertised by this engine.
            options["Threads"].parse(self._threads)
            options["Hash"].parse(self._hash_mb)
            await protocol.configure(
                {
                    "UCI_LimitStrength": True,
                    "UCI_Elo": self._uci_elo,
                    "Threads": self._threads,
                    "Hash": self._hash_mb,
                }
            )
        except BaseException:
            await self._discard_engine(transport, protocol)
            self._transport = None
            self._protocol = None
            raise

        return protocol

    @staticmethod
    def _canonical_options(options: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve required option names case-insensitively."""
        by_casefold = {name.casefold(): option for name, option in options.items()}
        return {
            name: by_casefold[name.casefold()]
            for name in EnginePlayer._REQUIRED_OPTIONS
            if name.casefold() in by_casefold
        }

    def _metadata(self, protocol: Any, info: Mapping[str, Any] | None) -> dict[str, object]:
        engine_id = getattr(protocol, "id", {}) or {}
        metadata: dict[str, object] = {
            "engine_name": engine_id.get("name", self.name),
            "engine_author": engine_id.get("author", ""),
            "engine_path": self._engine_path,
            "uci_elo": self._uci_elo,
            "node_limit": self._node_limit,
            "threads": self._threads,
            "hash_mb": self._hash_mb,
        }
        # Keep metadata JSON-friendly. python-chess's other info values can be
        # rich score/PV objects and belong in a future analysis-specific API.
        for key in ("depth", "seldepth", "nodes", "time", "nps", "hashfull", "tbhits"):
            value = (info or {}).get(key)
            if isinstance(value, (int, float, str, bool)):
                metadata[key] = value
        return metadata

    async def close(self) -> None:
        """Shut down the process; safe to call repeatedly."""
        async with self._lifecycle_lock:
            transport, protocol = self._transport, self._protocol
            # Detach first so a second close is a no-op even when quit fails.
            self._transport = None
            self._protocol = None
            if protocol is None:
                if transport is not None:
                    transport.close()
                return

            try:
                await protocol.quit()
            except asyncio.CancelledError:
                if transport is not None:
                    transport.close()
                raise
            except Exception:
                if transport is not None:
                    transport.close()

    async def force_close(self) -> None:
        """Terminate the engine transport without waiting for ``play``.

        ``get_move`` deliberately holds ``_lifecycle_lock`` around the UCI
        request because the protocol is stateful.  A broken engine can ignore
        cancellation while inside ``protocol.play()``, which means the normal
        ``close`` method cannot acquire that lock.  Closing the transport is
        the only bounded escape hatch in that situation; detach the handles
        first so a later normal cleanup cannot wait on the same protocol.
        """
        transport = self._transport
        self._transport = None
        self._protocol = None
        if transport is not None:
            transport.close()

    @staticmethod
    async def _discard_engine(transport: Any, protocol: Any) -> None:
        """Best-effort cleanup for an engine that failed during startup."""
        try:
            await protocol.quit()
        except asyncio.CancelledError:
            transport.close()
            raise
        except Exception:
            transport.close()
