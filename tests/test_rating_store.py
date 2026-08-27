"""Focused persistence tests for the ratings ledger."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from chessharness.ratings.store import (
    BatchAlreadyFinalizedError,
    BatchConflictError,
    GameRecord,
    RatingChange,
    RatingStore,
)


class RatingStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "ratings.sqlite3"
        self.store = RatingStore(self.db_path)
        self.addCleanup(self._close)

        self.store.register_competitor("llm:a", "A", "llm")
        self.store.register_competitor("llm:b", "B", "llm")

    def _close(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def _game(self, *, game_uuid: str = "game-1", **overrides: object) -> GameRecord:
        values: dict[str, object] = {
            "game_uuid": game_uuid,
            "white_competitor_id": "llm:a",
            "black_competitor_id": "llm:b",
            "result": "1-0",
            "termination": "checkmate",
            "rated": True,
            "ruleset_id": "standard-v1",
            "ruleset_hash": "hash-v1",
            "attempt_failures": [],
            "metadata": {},
        }
        values.update(overrides)
        return GameRecord(**values)  # type: ignore[arg-type]

    def _change(
        self,
        competitor_id: str,
        *,
        algorithm_version: str = "glicko2-v1",
        pre_rating: float = 1500.0,
        pre_rd: float = 350.0,
        pre_volatility: float = 0.06,
        pre_games_played: int = 0,
        post_rating: float = 1510.0,
        post_games_played: int = 1,
    ) -> RatingChange:
        return RatingChange(
            competitor_id=competitor_id,
            algorithm_version=algorithm_version,
            pre_rating=pre_rating,
            pre_rd=pre_rd,
            pre_volatility=pre_volatility,
            pre_games_played=pre_games_played,
            post_rating=post_rating,
            post_rd=300.0,
            post_volatility=0.059,
            post_games_played=post_games_played,
        )

    def test_schema_has_required_tables_and_constraints(self) -> None:
        tables = {
            row[0]
            for row in self.store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        self.assertTrue(
            {
                "competitors",
                "games",
                "rating_batches",
                "rating_changes",
                "current_ratings",
            }.issubset(tables)
        )
        self.assertEqual(self.store._conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        # File-backed SQLite databases should use WAL for concurrent readers.
        self.assertEqual(self.store._conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        columns = {
            row[1]
            for row in self.store._conn.execute("PRAGMA table_info(rating_changes)").fetchall()
        }
        self.assertTrue(
            {
                "pre_rating",
                "pre_rd",
                "pre_volatility",
                "pre_games_played",
                "post_rating",
                "post_rd",
                "post_volatility",
                "post_games_played",
            }.issubset(columns)
        )

    def test_duplicate_game_uuid_is_idempotent_but_conflicts_are_rejected(self) -> None:
        game = self._game()
        first = self.store.record_game(game)
        second = self.store.record_game(game)

        self.assertEqual(first, second)
        self.assertEqual(len(self.store.list_games()), 1)

    def test_unrated_same_identity_game_can_be_recorded(self) -> None:
        game = self._game(
            white_competitor_id="llm:a",
            black_competitor_id="llm:a",
            rated=False,
            unrated_reason="same competitor identity",
        )
        stored = self.store.record_game(game)
        self.assertEqual(stored.game_uuid, game.game_uuid)
        self.assertFalse(stored.rated)
        with self.assertRaises(Exception):
            self.store.record_game(self._game(result="0-1"))
        self.assertEqual(len(self.store.list_games()), 1)

    def test_game_recording_and_batch_commit_are_separate_transactions(self) -> None:
        game = self.store.record_game(self._game())
        self.store.create_batch("batch-1")

        # The failed batch transaction must not roll back the already recorded
        # game, and must leave its batch open with no partial changes.
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.finalize_batch("batch-1", [self._change("missing")])

        self.assertEqual(self.store.get_game(game.game_uuid), game)
        self.assertEqual(self.store.get_batch("batch-1").status, "open")  # type: ignore[union-attr]
        self.assertEqual(self.store.get_rating_changes("batch-1"), [])
        self.assertEqual(self.store.list_current_ratings(), [])

    def test_batch_commit_atomically_updates_changes_and_current_cache(self) -> None:
        self.store.ensure_current_rating("llm:a")
        self.store.ensure_current_rating("llm:b")
        self.store.create_batch("batch-1")
        result = self.store.finalize_batch(
            "batch-1",
            [self._change("llm:a", post_rating=1520.5), self._change("llm:b", post_rating=1480.5)],
        )

        self.assertFalse(result.idempotent)
        self.assertEqual(result.batch.status, "finalized")
        self.assertEqual(len(self.store.get_rating_changes("batch-1")), 2)
        self.assertEqual(self.store.get_current_rating("llm:a").rating, 1520.5)  # type: ignore[union-attr]

        self.store.create_batch("batch-3")
        with self.assertRaises(BatchConflictError):
            self.store.finalize_batch("batch-3", [self._change("llm:a", post_rating=1530.0)])
        self.assertEqual(self.store.get_batch("batch-3").status, "open")  # type: ignore[union-attr]
        self.assertEqual(self.store.get_rating_changes("batch-3"), [])
        self.assertEqual(self.store.get_current_rating("llm:a").rating, 1520.5)  # type: ignore[union-attr]
        self.assertEqual(self.store.get_current_rating("llm:b").rating, 1480.5)  # type: ignore[union-attr]
        self.assertEqual(self.store.get_current_rating("llm:a").last_batch_id, "batch-1")  # type: ignore[union-attr]

        # A later invalid batch cannot leave one competitor's current cache
        # updated while the other insert fails.
        self.store.create_batch("batch-2")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.finalize_batch(
                "batch-2",
                [self._change("llm:a", post_rating=1530.0), self._change("missing", post_rating=1470.0)],
            )
        self.assertEqual(self.store.get_batch("batch-2").status, "open")  # type: ignore[union-attr]
        self.assertEqual(self.store.get_rating_changes("batch-2"), [])
        self.assertEqual(self.store.get_current_rating("llm:a").rating, 1520.5)  # type: ignore[union-attr]

    def test_batch_finalization_is_idempotent_and_conflicting_retries_rejected(self) -> None:
        self.store.ensure_current_rating("llm:a")
        self.store.create_batch("batch-1")
        change = self._change("llm:a")
        first = self.store.finalize_batch("batch-1", [change])
        retry = self.store.finalize_batch("batch-1", [change])

        self.assertFalse(first.idempotent)
        self.assertTrue(retry.idempotent)
        self.assertEqual(retry.changes, (change,))
        self.assertEqual(len(self.store.get_rating_changes("batch-1")), 1)
        with self.assertRaises(BatchAlreadyFinalizedError):
            self.store.finalize_batch("batch-1", [self._change("llm:a", post_rating=1599.0)])
        self.assertEqual(self.store.get_current_rating("llm:a").rating, 1510.0)  # type: ignore[union-attr]

    def test_duplicate_changes_in_one_batch_are_idempotently_deduplicated(self) -> None:
        self.store.ensure_current_rating("llm:a")
        self.store.create_batch("batch-1")
        result = self.store.finalize_batch("batch-1", [self._change("llm:a"), self._change("llm:a")])
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(len(self.store.get_rating_changes("batch-1")), 1)
        with self.assertRaises(BatchConflictError):
            self.store.create_batch("batch-1", algorithm_version="different-v2")

    def test_json_fields_round_trip_nested_values(self) -> None:
        failures = [
            {"kind": "illegal_move", "attempt": 1, "details": {"move": "Qz9"}},
            {"kind": "provider_error", "retryable": True, "values": [1, 2, None]},
        ]
        metadata = {"provider": "mock", "options": {"temperature": 0.1}, "tags": ["rated", "pilot"]}
        stored = self.store.record_game(self._game(attempt_failures=failures, metadata=metadata))
        self.assertEqual(stored.attempt_failures, failures)
        self.assertEqual(stored.metadata, metadata)

        batch = self.store.create_batch("batch-json", metadata={"seed": 17, "labels": ["benchmark"]})
        self.assertEqual(batch.metadata, {"seed": 17, "labels": ["benchmark"]})

    def test_reopen_preserves_ledger_batches_and_current_state(self) -> None:
        self.store.ensure_current_rating("llm:a")
        self.store.record_game(self._game())
        self.store.create_batch("batch-1")
        self.store.finalize_batch("batch-1", [self._change("llm:a", post_rating=1517.25)])
        self.store.close()

        reopened = RatingStore(self.db_path)
        self.addCleanup(reopened.close)
        self.assertIsNotNone(reopened.get_game("game-1"))
        self.assertEqual(reopened.get_batch("batch-1").status, "finalized")  # type: ignore[union-attr]
        current = reopened.get_current_rating("llm:a")
        self.assertIsNotNone(current)
        self.assertEqual(current.rating, 1517.25)  # type: ignore[union-attr]
        self.assertEqual(current.games_played, 1)  # type: ignore[union-attr]

    def test_rebuild_current_ratings_uses_persisted_post_states(self) -> None:
        self.store.ensure_current_rating("llm:a")
        self.store.create_batch("batch-1")
        self.store.finalize_batch("batch-1", [self._change("llm:a", post_rating=1510.0)])
        self.store.create_batch("batch-2")
        self.store.finalize_batch(
            "batch-2",
            [
                self._change(
                    "llm:a",
                    pre_rating=1510.0,
                    pre_rd=300.0,
                    pre_volatility=0.059,
                    pre_games_played=1,
                    post_rating=1525.0,
                    post_games_played=2,
                )
            ],
        )

        self.store._conn.execute("DELETE FROM current_ratings")
        ratings = self.store.rebuild_current_ratings()
        current = next(item for item in ratings if item.competitor_id == "llm:a")
        self.assertEqual(current.rating, 1525.0)
        self.assertEqual(current.games_played, 2)
        self.assertEqual(current.last_batch_id, "batch-2")

    def test_rebuild_preserves_fixed_anchor_seed_without_rating_changes(self) -> None:
        self.store.register_competitor(
            "engine:anchor",
            "Stockfish Anchor",
            "engine",
            is_anchor=True,
        )
        self.store.ensure_current_rating(
            "engine:anchor",
            algorithm_version="glicko2-v1",
            rating=1600,
            rd=60,
        )

        rebuilt = self.store.rebuild_current_ratings(algorithm_version="glicko2-v1")

        anchor = next(item for item in rebuilt if item.competitor_id == "engine:anchor")
        self.assertEqual(anchor.rating, 1600)
        self.assertEqual(anchor.rd, 60)


if __name__ == "__main__":
    unittest.main()
