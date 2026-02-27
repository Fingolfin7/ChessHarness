import asyncio
import unittest

from chessharness.players.base import GameState
from chessharness.players.llm import LLMPlayer
from chessharness.providers.base import LLMProvider, Message


class _FakeProvider(LLMProvider):
    def __init__(self, *, supports_vision: bool, chunks: list[str]) -> None:
        self._supports_vision = supports_vision
        self._chunks = chunks
        self.last_messages: list[Message] | None = None

    @property
    def supports_vision(self) -> bool:
        return self._supports_vision

    async def complete(self, messages: list[Message], *, max_tokens: int = 5120) -> str:
        self.last_messages = messages
        return "".join(self._chunks)

    async def stream(self, messages: list[Message], *, max_tokens: int = 5120):
        self.last_messages = messages
        for c in self._chunks:
            yield c


def _state(**overrides) -> GameState:
    base = dict(
        fen="startpos-fen",
        board_ascii="ASCII-BOARD",
        legal_moves_uci=["e2e4", "d2d4"],
        legal_moves_san=["e4", "d4"],
        move_history_san=[],
        color="white",
        move_number=1,
        board_image_bytes=None,
        attempt_num=1,
        previous_invalid_move=None,
        previous_error=None,
    )
    base.update(overrides)
    return GameState(**base)


class LLMPlayerTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_yields_chunks_and_parses_move(self) -> None:
        provider = _FakeProvider(
            supports_vision=True,
            chunks=["## Reasoning\nGood line\n\n", "## Move\ne2e4\n"],
        )
        player = LLMPlayer(name="P", provider=provider)
        q: asyncio.Queue = asyncio.Queue()

        response = await player.get_move(_state(), chunk_queue=q)

        chunks: list[str] = []
        while not q.empty():
            chunks.append(q.get_nowait())
        self.assertEqual("".join(chunks), response.raw)
        self.assertEqual(response.move, "e2e4")
        self.assertIn("Good line", response.reasoning)

    async def test_history_is_carried_between_turns(self) -> None:
        provider = _FakeProvider(
            supports_vision=False,
            chunks=["## Reasoning\nr\n\n## Move\ne2e4\n"],
        )
        player = LLMPlayer(name="P", provider=provider)

        await player.get_move(_state(move_number=1))
        messages = player._build_messages(_state(move_number=2, move_history_san=["e4"]))

        self.assertGreaterEqual(len(messages), 4)
        self.assertEqual(messages[1].role, "user")
        self.assertIn("[Move 1", messages[1].content)
        self.assertEqual(messages[2].role, "assistant")
        self.assertIn("## Move", messages[2].content)

    def test_image_prompt_omits_fen_ascii_when_image_attached(self) -> None:
        provider = _FakeProvider(supports_vision=True, chunks=["ok"])
        player = LLMPlayer(name="P", provider=provider)
        messages = player._build_messages(_state(board_image_bytes=b"png-bytes"))
        user = messages[-1]

        self.assertIsNotNone(user.image_bytes)
        self.assertNotIn("Position (FEN)", user.content)
        self.assertNotIn("ASCII-BOARD", user.content)
        self.assertIn("Board image is attached", user.content)

    def test_text_prompt_used_when_provider_not_vision(self) -> None:
        provider = _FakeProvider(supports_vision=False, chunks=["ok"])
        player = LLMPlayer(name="P", provider=provider)
        messages = player._build_messages(_state(board_image_bytes=b"png-bytes"))
        user = messages[-1]

        self.assertIsNone(user.image_bytes)
        self.assertIn("Position (FEN)", user.content)
        self.assertIn("ASCII-BOARD", user.content)

