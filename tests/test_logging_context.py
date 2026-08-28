import asyncio
import logging
import unittest

from chessharness.logging_context import CorrelationFilter, logging_context


class LoggingContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_isolated_between_async_tasks(self) -> None:
        async def capture(game_id: str) -> str:
            record = logging.LogRecord(
                name="chessharness.test",
                level=logging.INFO,
                pathname=__file__,
                lineno=1,
                msg="test",
                args=(),
                exc_info=None,
            )
            with logging_context(game_id=game_id, match_id=f"match-{game_id}"):
                await asyncio.sleep(0)
                CorrelationFilter().filter(record)
            return record.game_id

        self.assertEqual(
            await asyncio.gather(capture("one"), capture("two")),
            ["one", "two"],
        )

    def test_filter_supplies_defaults_for_uncorrelated_records(self) -> None:
        record = logging.LogRecord(
            name="chessharness.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )

        CorrelationFilter().filter(record)

        self.assertEqual(record.game_id, "-")
        self.assertEqual(record.tournament_id, "-")
        self.assertEqual(record.match_id, "-")


if __name__ == "__main__":
    unittest.main()

