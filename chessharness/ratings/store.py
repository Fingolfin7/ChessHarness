"""Durable storage for ChessHarness games and Glicko-2 state.

The store deliberately does not know anything about Glicko-2 mathematics.  It
keeps the immutable game ledger and the before/after state produced by a
rating calculator.  Keeping those responsibilities separate makes it
possible to replay the ledger when the calculator is corrected or versioned.

``RatingStore`` owns one SQLite connection.  SQLite connections are normally
thread-affine, while games in a tournament can finish on different worker
threads, so the connection is created with ``check_same_thread=False`` and
every operation is protected by the store's re-entrant lock.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator, Mapping, Sequence


# Keep this identifier aligned with ``RatingConfig.algorithm_version``.  It is
# stored with every rating change so a future calculator can coexist with the
# current projection and be replayed independently.
DEFAULT_ALGORITHM_VERSION = "glicko2-v1"


class RatingStoreError(RuntimeError):
    """Base class for storage-level errors."""


class DuplicateGameError(RatingStoreError):
    """Raised when a game UUID is reused with different game data."""


class CompetitorConflictError(RatingStoreError):
    """Raised when an identity is registered with immutable data changed."""


class BatchNotFoundError(RatingStoreError):
    """Raised when a requested rating batch does not exist."""


class BatchAlreadyFinalizedError(RatingStoreError):
    """Raised when a finalized batch is given different rating changes."""


class BatchConflictError(RatingStoreError):
    """Raised when a batch would contain conflicting rating changes."""


@dataclass(frozen=True)
class Competitor:
    """A stable identity that can appear on either side of a game."""

    competitor_id: str
    display_name: str
    kind: str
    metadata: dict[str, Any] = field(default_factory=dict)
    is_anchor: bool = False
    created_at: str | None = None


@dataclass(frozen=True)
class GameRecord:
    """The immutable, storage-facing representation of a completed game."""

    game_uuid: str
    white_competitor_id: str
    black_competitor_id: str
    result: str
    termination: str
    rated: bool
    ruleset_id: str
    ruleset_hash: str
    unrated_reason: str | None = None
    batch_id: str | None = None
    attempt_failures: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None

    @property
    def game_id(self) -> str:
        """Compatibility alias for callers that call the UUID a game ID."""

        return self.game_uuid


@dataclass(frozen=True)
class RatingBatch:
    """A group of games rated against one pre-batch snapshot."""

    batch_id: str
    algorithm_version: str
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)
    ruleset_id: str | None = None
    ruleset_hash: str | None = None
    created_at: str | None = None
    finalized_at: str | None = None
    finalization_order: int | None = None


@dataclass(frozen=True)
class GlickoState:
    """A Glicko-2 state snapshot, without any update logic."""

    rating: float
    rd: float
    volatility: float
    games_played: int

    @property
    def deviation(self) -> float:
        """Alias used by the Glicko calculator for rating deviation (RD)."""

        return self.rd


@dataclass(frozen=True)
class RatingChange:
    """Complete before/after state for one competitor in one batch."""

    competitor_id: str
    algorithm_version: str
    pre_rating: float
    pre_rd: float
    pre_volatility: float
    pre_games_played: int
    post_rating: float
    post_rd: float
    post_volatility: float
    post_games_played: int

    @property
    def pre_state(self) -> GlickoState:
        return GlickoState(
            rating=self.pre_rating,
            rd=self.pre_rd,
            volatility=self.pre_volatility,
            games_played=self.pre_games_played,
        )

    @property
    def post_state(self) -> GlickoState:
        return GlickoState(
            rating=self.post_rating,
            rd=self.post_rd,
            volatility=self.post_volatility,
            games_played=self.post_games_played,
        )


@dataclass(frozen=True)
class CurrentRating:
    """The rebuildable materialized current rating for one competitor."""

    competitor_id: str
    algorithm_version: str
    rating: float
    rd: float
    volatility: float
    games_played: int
    last_batch_id: str | None = None
    updated_at: str | None = None

    @property
    def state(self) -> GlickoState:
        return GlickoState(
            rating=self.rating,
            rd=self.rd,
            volatility=self.volatility,
            games_played=self.games_played,
        )

    @property
    def deviation(self) -> float:
        """Alias for callers that use Glicko's ``deviation`` terminology."""

        return self.rd


@dataclass(frozen=True)
class BatchCommitResult:
    """Result of committing a batch.

    ``idempotent`` is true when the batch was already finalized with the same
    changes.  A retry therefore returns the original persisted changes without
    writing a second set of rows.
    """

    batch: RatingBatch
    changes: tuple[RatingChange, ...]
    idempotent: bool = False


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS competitors (
        competitor_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        kind TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        is_anchor INTEGER NOT NULL DEFAULT 0 CHECK (is_anchor IN (0, 1)),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rating_batches (
        batch_id TEXT PRIMARY KEY,
        algorithm_version TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'finalized')),
        metadata_json TEXT NOT NULL DEFAULT '{}',
        ruleset_id TEXT,
        ruleset_hash TEXT,
        created_at TEXT NOT NULL,
        finalized_at TEXT,
        finalization_order INTEGER UNIQUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS games (
        game_uuid TEXT PRIMARY KEY,
        white_competitor_id TEXT NOT NULL
            REFERENCES competitors(competitor_id),
        black_competitor_id TEXT NOT NULL
            REFERENCES competitors(competitor_id),
        result TEXT NOT NULL,
        termination TEXT NOT NULL,
        rated INTEGER NOT NULL CHECK (rated IN (0, 1)),
        unrated_reason TEXT,
        ruleset_id TEXT NOT NULL,
        ruleset_hash TEXT NOT NULL,
        batch_id TEXT REFERENCES rating_batches(batch_id),
        attempt_failures_json TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        started_at TEXT,
        completed_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rating_changes (
        batch_id TEXT NOT NULL REFERENCES rating_batches(batch_id),
        competitor_id TEXT NOT NULL REFERENCES competitors(competitor_id),
        algorithm_version TEXT NOT NULL,
        pre_rating REAL NOT NULL,
        pre_rd REAL NOT NULL,
        pre_volatility REAL NOT NULL,
        pre_games_played INTEGER NOT NULL,
        post_rating REAL NOT NULL,
        post_rd REAL NOT NULL,
        post_volatility REAL NOT NULL,
        post_games_played INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (batch_id, competitor_id, algorithm_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS current_ratings (
        competitor_id TEXT NOT NULL REFERENCES competitors(competitor_id),
        algorithm_version TEXT NOT NULL,
        rating REAL NOT NULL,
        rd REAL NOT NULL,
        volatility REAL NOT NULL,
        games_played INTEGER NOT NULL,
        last_batch_id TEXT REFERENCES rating_batches(batch_id),
        updated_at TEXT NOT NULL,
        PRIMARY KEY (competitor_id, algorithm_version)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_games_batch_id ON games(batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_games_rated ON games(rated)",
    "CREATE INDEX IF NOT EXISTS idx_rating_changes_competitor ON rating_changes(competitor_id, algorithm_version)",
    "CREATE INDEX IF NOT EXISTS idx_batches_status ON rating_batches(status)",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json_dumps(value: Any) -> str:
    """Encode JSON fields consistently so duplicate checks are deterministic."""

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise TypeError("Rating store metadata and attempt failures must be JSON serializable") from exc


def _json_loads(value: str, *, default: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RatingStoreError("Rating store contains invalid JSON") from exc


def _as_bool(value: int | bool) -> bool:
    return bool(int(value))


class RatingStore:
    """Thread-safe SQLite persistence for the ratings subsystem.

    Writes are short explicit ``BEGIN IMMEDIATE`` transactions.  Recording a
    game and committing its rating batch are separate public operations and
    therefore separate transactions.  All methods on one instance share one
    connection; callers should create another ``RatingStore`` only when they
    need another process to access the same database.
    """

    def __init__(self, path: str | Path = "ratings.sqlite3") -> None:
        self.path = path
        self._lock = threading.RLock()
        self._closed = False

        path_value = str(path)
        # The default configured database lives under ``./data``.  Creating
        # that narrow parent directory here keeps first-run setup predictable
        # while leaving special in-memory databases untouched.
        if path_value not in {":memory:", ""} and not path_value.startswith("file:"):
            Path(path_value).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            path_value,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        # PRAGMAs must be set before schema creation.  WAL is not available for
        # :memory: databases, where SQLite intentionally reports "memory".
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 30000")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._create_schema()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RatingStoreError("RatingStore is closed")

    def _create_schema(self) -> None:
        self._ensure_open()
        # Execute statements individually so schema initialization itself is
        # an explicit transaction rather than relying on executescript's
        # implicit transaction behavior.
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _SCHEMA:
                self._conn.execute(statement)
            self._conn.execute("COMMIT")
        except BaseException:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._ensure_open()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Expose a guarded explicit transaction for advanced integrations.

        Normal callers should use the typed methods.  The yielded connection
        is the store's sole connection and must not be used after the context
        exits.
        """

        with self._transaction() as connection:
            yield connection

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True

    def __enter__(self) -> "RatingStore":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Competitors
    # ------------------------------------------------------------------

    def register_competitor(
        self,
        competitor_id: str,
        display_name: str,
        kind: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        is_anchor: bool = False,
    ) -> Competitor:
        """Insert a stable competitor identity, or return its existing row.

        Display names and metadata are safe to refresh.  ``kind`` and anchor
        status are identity properties; changing either is rejected rather
        than silently changing the meaning of historical games.
        """

        if not competitor_id:
            raise ValueError("competitor_id must not be empty")
        if not display_name:
            raise ValueError("display_name must not be empty")
        if not kind:
            raise ValueError("kind must not be empty")
        metadata_value = dict(metadata or {})
        metadata_json = _json_dumps(metadata_value)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM competitors WHERE competitor_id = ?",
                (competitor_id,),
            ).fetchone()
            if existing is not None:
                if existing["kind"] != kind or _as_bool(existing["is_anchor"]) != bool(is_anchor):
                    raise CompetitorConflictError(
                        f"Competitor {competitor_id!r} is already registered with different kind or anchor status"
                    )
                if metadata is None:
                    connection.execute(
                        "UPDATE competitors SET display_name = ? WHERE competitor_id = ?",
                        (display_name, competitor_id),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE competitors
                        SET display_name = ?, metadata_json = ?
                        WHERE competitor_id = ?
                        """,
                        (display_name, metadata_json, competitor_id),
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO competitors
                        (competitor_id, display_name, kind, metadata_json, is_anchor, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (competitor_id, display_name, kind, metadata_json, int(bool(is_anchor)), _utc_now()),
                )
            row = connection.execute(
                "SELECT * FROM competitors WHERE competitor_id = ?",
                (competitor_id,),
            ).fetchone()
            assert row is not None
            return self._competitor_from_row(row)

    # Common aliases make the storage API easy to discover from integration
    # code without creating a second implementation.
    add_competitor = register_competitor

    def get_competitor(self, competitor_id: str) -> Competitor | None:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                "SELECT * FROM competitors WHERE competitor_id = ?",
                (competitor_id,),
            ).fetchone()
            return self._competitor_from_row(row) if row is not None else None

    def list_competitors(self) -> list[Competitor]:
        with self._lock:
            self._ensure_open()
            rows = self._conn.execute(
                "SELECT * FROM competitors ORDER BY competitor_id"
            ).fetchall()
            return [self._competitor_from_row(row) for row in rows]

    @staticmethod
    def _competitor_from_row(row: sqlite3.Row) -> Competitor:
        return Competitor(
            competitor_id=str(row["competitor_id"]),
            display_name=str(row["display_name"]),
            kind=str(row["kind"]),
            metadata=_json_loads(row["metadata_json"], default={}),
            is_anchor=_as_bool(row["is_anchor"]),
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------
    # Games
    # ------------------------------------------------------------------

    def record_game(
        self,
        game: GameRecord | None = None,
        *,
        game_uuid: str | None = None,
        white_competitor_id: str | None = None,
        black_competitor_id: str | None = None,
        result: str | None = None,
        termination: str | None = None,
        rated: bool | None = None,
        ruleset_id: str | None = None,
        ruleset_hash: str | None = None,
        unrated_reason: str | None = None,
        batch_id: str | None = None,
        attempt_failures: Sequence[Mapping[str, Any]] | None = None,
        metadata: Mapping[str, Any] | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> GameRecord:
        """Persist one game and return the stored row.

        Repeating the same UUID with the same immutable game data is
        idempotent.  Reusing it for different players/result/ruleset data is a
        data-integrity error.  This operation never updates ratings.
        """

        if game is None:
            missing = {
                "game_uuid": game_uuid,
                "white_competitor_id": white_competitor_id,
                "black_competitor_id": black_competitor_id,
                "result": result,
                "termination": termination,
                "rated": rated,
                "ruleset_id": ruleset_id,
                "ruleset_hash": ruleset_hash,
            }
            if any(value is None for value in missing.values()):
                missing_names = ", ".join(name for name, value in missing.items() if value is None)
                raise TypeError(f"Missing game fields: {missing_names}")
            game = GameRecord(
                game_uuid=str(game_uuid),
                white_competitor_id=str(white_competitor_id),
                black_competitor_id=str(black_competitor_id),
                result=str(result),
                termination=str(termination),
                rated=bool(rated),
                ruleset_id=str(ruleset_id),
                ruleset_hash=str(ruleset_hash),
                unrated_reason=unrated_reason,
                batch_id=batch_id,
                attempt_failures=[dict(item) for item in (attempt_failures or ())],
                metadata=dict(metadata or {}),
                started_at=started_at,
                completed_at=completed_at,
            )
        elif any(
            value is not None
            for value in (
                game_uuid,
                white_competitor_id,
                black_competitor_id,
                result,
                termination,
                rated,
                ruleset_id,
                ruleset_hash,
                unrated_reason,
                batch_id,
                attempt_failures,
                metadata,
                started_at,
                completed_at,
            )
        ):
            raise TypeError("Pass either a GameRecord or keyword game fields, not both")

        self._validate_game(game)
        attempt_failures_json = _json_dumps(game.attempt_failures)
        metadata_json = _json_dumps(game.metadata)
        completed_at_value = game.completed_at or _utc_now()

        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM games WHERE game_uuid = ?",
                (game.game_uuid,),
            ).fetchone()
            if existing is not None:
                existing_game = self._game_from_row(existing)
                if not self._same_game_payload(existing_game, game):
                    raise DuplicateGameError(
                        f"Game UUID {game.game_uuid!r} already exists with different game data"
                    )
                return existing_game

            if game.batch_id is not None:
                batch = connection.execute(
                    "SELECT status FROM rating_batches WHERE batch_id = ?",
                    (game.batch_id,),
                ).fetchone()
                if batch is None:
                    raise BatchNotFoundError(f"Unknown rating batch: {game.batch_id}")
                if batch["status"] == "finalized":
                    raise BatchAlreadyFinalizedError(f"Rating batch {game.batch_id!r} is already finalized")

            connection.execute(
                """
                INSERT INTO games (
                    game_uuid, white_competitor_id, black_competitor_id, result,
                    termination, rated, unrated_reason, ruleset_id, ruleset_hash,
                    batch_id, attempt_failures_json, metadata_json, started_at,
                    completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    game.game_uuid,
                    game.white_competitor_id,
                    game.black_competitor_id,
                    game.result,
                    game.termination,
                    int(game.rated),
                    game.unrated_reason,
                    game.ruleset_id,
                    game.ruleset_hash,
                    game.batch_id,
                    attempt_failures_json,
                    metadata_json,
                    game.started_at,
                    completed_at_value,
                ),
            )
            row = connection.execute(
                "SELECT * FROM games WHERE game_uuid = ?",
                (game.game_uuid,),
            ).fetchone()
            assert row is not None
            return self._game_from_row(row)

    add_game = record_game

    @staticmethod
    def _validate_game(game: GameRecord) -> None:
        if not game.game_uuid:
            raise ValueError("game_uuid must not be empty")
        if not game.white_competitor_id or not game.black_competitor_id:
            raise ValueError("Both game competitor IDs are required")
        if not game.result:
            raise ValueError("result must not be empty")
        if not game.termination:
            raise ValueError("termination must not be empty")
        if not game.ruleset_id or not game.ruleset_hash:
            raise ValueError("ruleset_id and ruleset_hash are required")

    @staticmethod
    def _same_game_payload(existing: GameRecord, candidate: GameRecord) -> bool:
        """Compare immutable fields; timestamps are observational metadata."""

        return (
            existing.white_competitor_id == candidate.white_competitor_id
            and existing.black_competitor_id == candidate.black_competitor_id
            and existing.result == candidate.result
            and existing.termination == candidate.termination
            and existing.rated == candidate.rated
            and existing.unrated_reason == candidate.unrated_reason
            and existing.ruleset_id == candidate.ruleset_id
            and existing.ruleset_hash == candidate.ruleset_hash
            and existing.batch_id == candidate.batch_id
            and _json_dumps(existing.attempt_failures) == _json_dumps(candidate.attempt_failures)
            and _json_dumps(existing.metadata) == _json_dumps(candidate.metadata)
        )

    def get_game(self, game_uuid: str) -> GameRecord | None:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                "SELECT * FROM games WHERE game_uuid = ?",
                (game_uuid,),
            ).fetchone()
            return self._game_from_row(row) if row is not None else None

    def list_games(
        self,
        *,
        batch_id: str | None = None,
        rated: bool | None = None,
        limit: int | None = None,
    ) -> list[GameRecord]:
        with self._lock:
            self._ensure_open()
            clauses: list[str] = []
            parameters: list[Any] = []
            if batch_id is not None:
                clauses.append("batch_id = ?")
                parameters.append(batch_id)
            if rated is not None:
                clauses.append("rated = ?")
                parameters.append(int(rated))
            query = "SELECT * FROM games"
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY completed_at, game_uuid"
            if limit is not None:
                if limit < 0:
                    raise ValueError("limit must be non-negative")
                query += " LIMIT ?"
                parameters.append(limit)
            rows = self._conn.execute(query, parameters).fetchall()
            return [self._game_from_row(row) for row in rows]

    @staticmethod
    def _game_from_row(row: sqlite3.Row) -> GameRecord:
        attempts = _json_loads(row["attempt_failures_json"], default=[])
        metadata = _json_loads(row["metadata_json"], default={})
        return GameRecord(
            game_uuid=str(row["game_uuid"]),
            white_competitor_id=str(row["white_competitor_id"]),
            black_competitor_id=str(row["black_competitor_id"]),
            result=str(row["result"]),
            termination=str(row["termination"]),
            rated=_as_bool(row["rated"]),
            ruleset_id=str(row["ruleset_id"]),
            ruleset_hash=str(row["ruleset_hash"]),
            unrated_reason=row["unrated_reason"],
            batch_id=row["batch_id"],
            attempt_failures=attempts,
            metadata=metadata,
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    # ------------------------------------------------------------------
    # Batches
    # ------------------------------------------------------------------

    def create_batch(
        self,
        batch_id: str,
        *,
        algorithm_version: str = DEFAULT_ALGORITHM_VERSION,
        metadata: Mapping[str, Any] | None = None,
        ruleset_id: str | None = None,
        ruleset_hash: str | None = None,
    ) -> RatingBatch:
        """Create an open batch, idempotently returning an existing batch."""

        if not batch_id:
            raise ValueError("batch_id must not be empty")
        if not algorithm_version:
            raise ValueError("algorithm_version must not be empty")
        metadata_value = dict(metadata or {})
        metadata_json = _json_dumps(metadata_value)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM rating_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["algorithm_version"] != algorithm_version
                    or existing["ruleset_id"] != ruleset_id
                    or existing["ruleset_hash"] != ruleset_hash
                ):
                    raise BatchConflictError(
                        f"Rating batch {batch_id!r} already exists with different algorithm or ruleset"
                    )
                return self._batch_from_row(existing)
            connection.execute(
                """
                INSERT INTO rating_batches (
                    batch_id, algorithm_version, status, metadata_json,
                    ruleset_id, ruleset_hash, created_at
                ) VALUES (?, ?, 'open', ?, ?, ?, ?)
                """,
                (batch_id, algorithm_version, metadata_json, ruleset_id, ruleset_hash, _utc_now()),
            )
            row = connection.execute(
                "SELECT * FROM rating_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            assert row is not None
            return self._batch_from_row(row)

    begin_batch = create_batch

    def get_batch(self, batch_id: str) -> RatingBatch | None:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                "SELECT * FROM rating_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            return self._batch_from_row(row) if row is not None else None

    def list_batches(self, *, status: str | None = None) -> list[RatingBatch]:
        with self._lock:
            self._ensure_open()
            if status is None:
                rows = self._conn.execute(
                    "SELECT * FROM rating_batches ORDER BY created_at, batch_id"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM rating_batches WHERE status = ? ORDER BY created_at, batch_id",
                    (status,),
                ).fetchall()
            return [self._batch_from_row(row) for row in rows]

    @staticmethod
    def _batch_from_row(row: sqlite3.Row) -> RatingBatch:
        return RatingBatch(
            batch_id=str(row["batch_id"]),
            algorithm_version=str(row["algorithm_version"]),
            status=str(row["status"]),
            metadata=_json_loads(row["metadata_json"], default={}),
            ruleset_id=row["ruleset_id"],
            ruleset_hash=row["ruleset_hash"],
            created_at=row["created_at"],
            finalized_at=row["finalized_at"],
            finalization_order=row["finalization_order"],
        )

    def finalize_batch(
        self,
        batch_id: str,
        changes: Sequence[RatingChange] = (),
    ) -> BatchCommitResult:
        """Atomically persist changes, update the current cache, and finalize.

        The caller supplies already-computed Glicko-2 states.  This method is
        intentionally agnostic about how those states were calculated.

        Finalization is idempotent: retrying with the same changes returns the
        persisted result.  A retry with different changes raises
        :class:`BatchAlreadyFinalizedError` and cannot alter the database.
        """

        normalized = self._normalize_changes(changes)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM rating_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                raise BatchNotFoundError(f"Unknown rating batch: {batch_id}")
            batch = self._batch_from_row(row)
            if any(change.algorithm_version != batch.algorithm_version for change in normalized):
                raise BatchConflictError(
                    f"Rating change algorithm version does not match batch {batch_id!r}"
                )

            if batch.status == "finalized":
                stored = self._rating_changes_for_connection(connection, batch_id)
                if stored != tuple(normalized):
                    raise BatchAlreadyFinalizedError(
                        f"Rating batch {batch_id!r} is already finalized with different changes"
                    )
                return BatchCommitResult(batch=batch, changes=stored, idempotent=True)

            # The public API only allows changes to enter through this method.
            # Still detect a partial/manual insertion rather than silently
            # mixing it with a new calculation.
            existing_changes = self._rating_changes_for_connection(connection, batch_id)
            if existing_changes:
                if existing_changes != tuple(normalized):
                    raise BatchConflictError(
                        f"Rating batch {batch_id!r} already contains different changes"
                    )
                raise BatchConflictError(
                    f"Rating batch {batch_id!r} contains changes but is not finalized"
                )

            # Validate all competitor references before making any inserts so a
            # failed batch cannot partially update rating_changes/current_ratings.
            for change in normalized:
                competitor = connection.execute(
                    "SELECT 1 FROM competitors WHERE competitor_id = ?",
                    (change.competitor_id,),
                ).fetchone()
                if competitor is None:
                    raise sqlite3.IntegrityError(
                        f"Unknown competitor for rating change: {change.competitor_id}"
                    )

            # Optimistic pre-state validation prevents a stale batch snapshot
            # from overwriting a newer current rating.  Rating services should
            # call ``ensure_current_rating`` when they register a competitor;
            # requiring a row here makes that initialization explicit and
            # avoids silently inventing a second default state.
            for change in normalized:
                current_row = connection.execute(
                    """
                    SELECT rating, rd, volatility, games_played
                    FROM current_ratings
                    WHERE competitor_id = ? AND algorithm_version = ?
                    """,
                    (change.competitor_id, change.algorithm_version),
                ).fetchone()
                if current_row is None:
                    raise BatchConflictError(
                        f"No current rating for {change.competitor_id!r} / "
                        f"{change.algorithm_version!r}; call ensure_current_rating first"
                    )
                if not self._state_matches_row(change, current_row):
                    raise BatchConflictError(
                        f"Stale pre-state for {change.competitor_id!r} / "
                        f"{change.algorithm_version!r}"
                    )

            now = _utc_now()
            for change in normalized:
                connection.execute(
                    """
                    INSERT INTO rating_changes (
                        batch_id, competitor_id, algorithm_version,
                        pre_rating, pre_rd, pre_volatility, pre_games_played,
                        post_rating, post_rd, post_volatility, post_games_played,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        change.competitor_id,
                        change.algorithm_version,
                        change.pre_rating,
                        change.pre_rd,
                        change.pre_volatility,
                        change.pre_games_played,
                        change.post_rating,
                        change.post_rd,
                        change.post_volatility,
                        change.post_games_played,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO current_ratings (
                        competitor_id, algorithm_version, rating, rd, volatility,
                        games_played, last_batch_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (competitor_id, algorithm_version) DO UPDATE SET
                        rating = excluded.rating,
                        rd = excluded.rd,
                        volatility = excluded.volatility,
                        games_played = excluded.games_played,
                        last_batch_id = excluded.last_batch_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        change.competitor_id,
                        change.algorithm_version,
                        change.post_rating,
                        change.post_rd,
                        change.post_volatility,
                        change.post_games_played,
                        batch_id,
                        now,
                    ),
                )

            finalization_order_row = connection.execute(
                "SELECT COALESCE(MAX(finalization_order), 0) + 1 AS next_order FROM rating_batches"
            ).fetchone()
            finalization_order = int(finalization_order_row["next_order"])
            finalized_at = _utc_now()
            connection.execute(
                """
                UPDATE rating_batches
                SET status = 'finalized', finalized_at = ?, finalization_order = ?
                WHERE batch_id = ? AND status = 'open'
                """,
                (finalized_at, finalization_order, batch_id),
            )
            final_row = connection.execute(
                "SELECT * FROM rating_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            assert final_row is not None
            final_batch = self._batch_from_row(final_row)
            return BatchCommitResult(
                batch=final_batch,
                changes=tuple(normalized),
                idempotent=False,
            )

    commit_rating_batch = finalize_batch
    commit_batch = finalize_batch

    @staticmethod
    def _normalize_changes(changes: Sequence[RatingChange]) -> tuple[RatingChange, ...]:
        by_competitor: dict[tuple[str, str], RatingChange] = {}
        for change in changes:
            if not isinstance(change, RatingChange):
                if isinstance(change, Mapping):
                    try:
                        change = RatingChange(**change)
                    except TypeError as exc:
                        raise TypeError("Rating changes must be RatingChange instances or matching mappings") from exc
                else:
                    raise TypeError("Rating changes must be RatingChange instances or matching mappings")
            key = (change.competitor_id, change.algorithm_version)
            previous = by_competitor.get(key)
            if previous is not None and previous != change:
                raise BatchConflictError(
                    f"Conflicting duplicate rating change for {change.competitor_id!r}"
                )
            by_competitor[key] = change
        return tuple(sorted(by_competitor.values(), key=lambda item: (item.competitor_id, item.algorithm_version)))

    @staticmethod
    def _state_matches_row(change: RatingChange, row: sqlite3.Row) -> bool:
        """Compare a calculator snapshot with persisted state safely.

        SQLite stores Python floats as IEEE doubles.  A tiny tolerance handles
        the normal Python/SQLite round trip while still rejecting a genuinely
        stale batch.  Games played is integral and must match exactly.
        """

        return (
            math.isclose(float(row["rating"]), change.pre_rating, rel_tol=1e-12, abs_tol=1e-9)
            and math.isclose(float(row["rd"]), change.pre_rd, rel_tol=1e-12, abs_tol=1e-9)
            and math.isclose(float(row["volatility"]), change.pre_volatility, rel_tol=1e-12, abs_tol=1e-12)
            and int(row["games_played"]) == change.pre_games_played
        )

    def get_rating_changes(
        self,
        batch_id: str,
        *,
        algorithm_version: str | None = None,
    ) -> list[RatingChange]:
        with self._lock:
            self._ensure_open()
            if algorithm_version is None:
                rows = self._conn.execute(
                    """
                    SELECT * FROM rating_changes
                    WHERE batch_id = ?
                    ORDER BY competitor_id, algorithm_version
                    """,
                    (batch_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT * FROM rating_changes
                    WHERE batch_id = ? AND algorithm_version = ?
                    ORDER BY competitor_id
                    """,
                    (batch_id, algorithm_version),
                ).fetchall()
            return [self._change_from_row(row) for row in rows]

    def _rating_changes_for_connection(
        self,
        connection: sqlite3.Connection,
        batch_id: str,
    ) -> tuple[RatingChange, ...]:
        rows = connection.execute(
            """
            SELECT * FROM rating_changes
            WHERE batch_id = ?
            ORDER BY competitor_id, algorithm_version
            """,
            (batch_id,),
        ).fetchall()
        return tuple(self._change_from_row(row) for row in rows)

    @staticmethod
    def _change_from_row(row: sqlite3.Row) -> RatingChange:
        return RatingChange(
            competitor_id=str(row["competitor_id"]),
            algorithm_version=str(row["algorithm_version"]),
            pre_rating=float(row["pre_rating"]),
            pre_rd=float(row["pre_rd"]),
            pre_volatility=float(row["pre_volatility"]),
            pre_games_played=int(row["pre_games_played"]),
            post_rating=float(row["post_rating"]),
            post_rd=float(row["post_rd"]),
            post_volatility=float(row["post_volatility"]),
            post_games_played=int(row["post_games_played"]),
        )

    # ------------------------------------------------------------------
    # Current ratings and replay
    # ------------------------------------------------------------------

    def ensure_current_rating(
        self,
        competitor_id: str,
        *,
        algorithm_version: str = DEFAULT_ALGORITHM_VERSION,
        rating: float = 1500.0,
        rd: float = 350.0,
        volatility: float = 0.06,
        games_played: int = 0,
    ) -> CurrentRating:
        """Seed a current state once; subsequent calls leave it unchanged."""

        with self._transaction() as connection:
            competitor = connection.execute(
                "SELECT 1 FROM competitors WHERE competitor_id = ?",
                (competitor_id,),
            ).fetchone()
            if competitor is None:
                raise sqlite3.IntegrityError(f"Unknown competitor: {competitor_id}")
            existing = connection.execute(
                """
                SELECT * FROM current_ratings
                WHERE competitor_id = ? AND algorithm_version = ?
                """,
                (competitor_id, algorithm_version),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO current_ratings (
                        competitor_id, algorithm_version, rating, rd, volatility,
                        games_played, last_batch_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (competitor_id, algorithm_version, rating, rd, volatility, games_played, _utc_now()),
                )
                existing = connection.execute(
                    """
                    SELECT * FROM current_ratings
                    WHERE competitor_id = ? AND algorithm_version = ?
                    """,
                    (competitor_id, algorithm_version),
                ).fetchone()
            assert existing is not None
            return self._current_from_row(existing)

    initialize_current_rating = ensure_current_rating

    def get_current_rating(
        self,
        competitor_id: str,
        *,
        algorithm_version: str = DEFAULT_ALGORITHM_VERSION,
    ) -> CurrentRating | None:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                """
                SELECT * FROM current_ratings
                WHERE competitor_id = ? AND algorithm_version = ?
                """,
                (competitor_id, algorithm_version),
            ).fetchone()
            return self._current_from_row(row) if row is not None else None

    def list_current_ratings(
        self,
        *,
        algorithm_version: str = DEFAULT_ALGORITHM_VERSION,
    ) -> list[CurrentRating]:
        with self._lock:
            self._ensure_open()
            rows = self._conn.execute(
                """
                SELECT * FROM current_ratings
                WHERE algorithm_version = ?
                ORDER BY rating DESC, competitor_id
                """,
                (algorithm_version,),
            ).fetchall()
            return [self._current_from_row(row) for row in rows]

    @staticmethod
    def _current_from_row(row: sqlite3.Row) -> CurrentRating:
        return CurrentRating(
            competitor_id=str(row["competitor_id"]),
            algorithm_version=str(row["algorithm_version"]),
            rating=float(row["rating"]),
            rd=float(row["rd"]),
            volatility=float(row["volatility"]),
            games_played=int(row["games_played"]),
            last_batch_id=row["last_batch_id"],
            updated_at=row["updated_at"],
        )

    def rebuild_current_ratings(self, *, algorithm_version: str | None = None) -> list[CurrentRating]:
        """Rebuild the materialized cache from finalized change history.

        The calculation is intentionally simple: each persisted post-state is
        applied in batch finalization order.  The rating calculator remains the
        source of those states; this method only reconstructs the cache.
        """

        with self._transaction() as connection:
            anchor_query = """
                SELECT cr.*
                FROM current_ratings AS cr
                JOIN competitors AS c ON c.competitor_id = cr.competitor_id
                WHERE c.is_anchor = 1
            """
            anchor_parameters: tuple[Any, ...] = ()
            if algorithm_version is not None:
                anchor_query += " AND cr.algorithm_version = ?"
                anchor_parameters = (algorithm_version,)
            anchor_rows = connection.execute(
                anchor_query,
                anchor_parameters,
            ).fetchall()

            if algorithm_version is None:
                connection.execute("DELETE FROM current_ratings")
                version_clause = ""
                parameters: tuple[Any, ...] = ()
            else:
                connection.execute(
                    "DELETE FROM current_ratings WHERE algorithm_version = ?",
                    (algorithm_version,),
                )
                version_clause = "AND rc.algorithm_version = ?"
                parameters = (algorithm_version,)

            rows = connection.execute(
                f"""
                SELECT rc.*, b.finalization_order
                FROM rating_changes AS rc
                JOIN rating_batches AS b ON b.batch_id = rc.batch_id
                WHERE b.status = 'finalized' {version_clause}
                ORDER BY b.finalization_order, rc.competitor_id, rc.algorithm_version
                """,
                parameters,
            ).fetchall()
            for row in rows:
                timestamp = row["created_at"]
                connection.execute(
                    """
                    INSERT INTO current_ratings (
                        competitor_id, algorithm_version, rating, rd, volatility,
                        games_played, last_batch_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (competitor_id, algorithm_version) DO UPDATE SET
                        rating = excluded.rating,
                        rd = excluded.rd,
                        volatility = excluded.volatility,
                        games_played = excluded.games_played,
                        last_batch_id = excluded.last_batch_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        row["competitor_id"],
                        row["algorithm_version"],
                        row["post_rating"],
                        row["post_rd"],
                        row["post_volatility"],
                        row["post_games_played"],
                        row["batch_id"],
                        timestamp,
                    ),
                )

            # Fixed anchors intentionally have no rating_changes rows. Preserve
            # their immutable seed states while rebuilding the calculated
            # projection so a cache repair cannot silently remove benchmarks.
            for row in anchor_rows:
                connection.execute(
                    """
                    INSERT INTO current_ratings (
                        competitor_id, algorithm_version, rating, rd, volatility,
                        games_played, last_batch_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (competitor_id, algorithm_version) DO NOTHING
                    """,
                    (
                        row["competitor_id"],
                        row["algorithm_version"],
                        row["rating"],
                        row["rd"],
                        row["volatility"],
                        row["games_played"],
                        row["last_batch_id"],
                        row["updated_at"],
                    ),
                )

            if algorithm_version is None:
                rows = connection.execute(
                    "SELECT * FROM current_ratings ORDER BY rating DESC, competitor_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM current_ratings
                    WHERE algorithm_version = ?
                    ORDER BY rating DESC, competitor_id
                    """,
                    (algorithm_version,),
                ).fetchall()
            return [self._current_from_row(row) for row in rows]


__all__ = [
    "BatchAlreadyFinalizedError",
    "BatchCommitResult",
    "BatchConflictError",
    "BatchNotFoundError",
    "Competitor",
    "CompetitorConflictError",
    "CurrentRating",
    "DEFAULT_ALGORITHM_VERSION",
    "DuplicateGameError",
    "GameRecord",
    "GlickoState",
    "RatingBatch",
    "RatingChange",
    "RatingStore",
    "RatingStoreError",
]
