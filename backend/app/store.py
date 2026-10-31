"""Durable transaction store: SQLite locally and PostgreSQL for shared hosting."""

from __future__ import annotations

import json
import errno
import logging
import math
import os
import re
import socket
import ssl
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from app.game import Game, legal_moves, slide
from app.learner import ALGORITHM, FEATURE_ORDER, INITIAL_WEIGHTS, hint as choose_hint, update

SCHEMA_VERSION = 1
CREATE_SCOPE = "create"
SCHEMA_NAME = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}")
RESERVED_SCHEMAS = {
    "public", "information_schema", "auth", "storage", "realtime", "extensions",
    "vault", "graphql", "graphql_public", "supabase_functions", "supabase_migrations",
}
logger = logging.getLogger(__name__)
SQLSTATE_RE = re.compile(r"[0-9A-Z]{5}")
SAFE_EXCEPTION_MODULES = {"psycopg", "socket", "ssl", "builtins"}


class StoreError(Exception):
    code = "storage_unavailable"


class StoreBusy(StoreError):
    code = "storage_busy"


class StoreUnavailable(StoreError):
    code = "storage_unavailable"


class GameNotFound(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


class StaleRevision(Exception):
    def __init__(self, state):
        self.state = state


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def encode(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)


class Store:
    def __init__(self, database_url: str | None = None, db_path: str | None = None,
                 failure_injector: Callable[[str], None] | None = None, schema: str | None = None,
                 readonly: bool = False):
        self.database_url = database_url
        self.db_path = db_path
        self.failure_injector = failure_injector
        if schema is not None and not SCHEMA_NAME.fullmatch(schema):
            raise ValueError("Invalid database schema name")
        self.schema = schema
        self.readonly = readonly
        if database_url and db_path:
            raise ValueError("Configure only DATABASE_URL or GAME_DB_PATH")

    @classmethod
    def from_environment(cls, environ=None):
        env = os.environ if environ is None else environ
        url = env.get("DATABASE_URL")
        schema = env.get("GAME_DB_SCHEMA")
        if url and url.strip().lower().startswith(("http://", "https://")):
            raise ValueError(
                "DATABASE_URL must be a PostgreSQL connection string. "
                "For Supabase, use Connect > Transaction pooler; do not use the HTTP Project URL."
            )
        if schema is not None:
            if (not SCHEMA_NAME.fullmatch(schema)
                    or schema.lower() in RESERVED_SCHEMAS
                    or schema.lower().startswith(("pg_", "supabase_"))):
                raise ValueError("GAME_DB_SCHEMA must name a private application schema (1-63 ASCII identifier characters).")
            if not url:
                raise ValueError("GAME_DB_SCHEMA requires DATABASE_URL; it cannot configure local SQLite.")
        if url:
            if env.get("VERCEL") and schema is None:
                raise RuntimeError("GAME_DB_SCHEMA must name a preprovisioned private PostgreSQL schema on Vercel.")
            return cls(database_url=url, schema=schema)
        if env.get("VERCEL"):
            raise RuntimeError("DATABASE_URL must point to a persistent PostgreSQL database on Vercel; SQLite function storage is ephemeral.")
        return cls(db_path=env.get("GAME_DB_PATH", str(Path(__file__).resolve().parents[1] / "data" / "2048.sqlite3")))

    @property
    def is_postgres(self):
        return self.database_url is not None

    def _connect(self):
        try:
            if self.is_postgres:
                import psycopg
                from psycopg.rows import dict_row
                return psycopg.connect(self.database_url, connect_timeout=5,
                                       prepare_threshold=None,
                                       row_factory=dict_row, autocommit=True)
            path = Path(self.db_path or "")
            if self.readonly:
                connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=5, isolation_level=None)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(str(path), timeout=5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            if not self.readonly:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA busy_timeout = 5000")
            return connection
        except Exception as exc:
            self._log_connection_failure(exc)
            self._raise_storage(exc)

    @staticmethod
    def _connection_failure_reason(exc) -> str:
        """Map private driver details to a small, non-sensitive diagnostic enum."""
        sqlstate = getattr(exc, "sqlstate", None)
        exception_name = type(exc).__name__
        message = str(exc).lower()

        if sqlstate in {"28P01", "28000"} or exception_name in {
            "InvalidPassword", "InvalidAuthorizationSpecification",
        }:
            return "auth_failure"
        if isinstance(exc, ImportError) or exception_name in {"ModuleNotFoundError", "ImportError"}:
            return "missing_driver"
        if isinstance(exc, ssl.SSLError) or any(token in message for token in (
            "ssl error", "tls error", "certificate verify", "certificate has expired",
            "ssl connection", "tls handshake",
        )):
            return "tls_failure"
        if isinstance(exc, socket.gaierror) or any(token in message for token in (
            "could not translate host name", "name or service not known",
            "nodename nor servname", "temporary failure in name resolution",
            "no address associated with hostname",
        )):
            return "host_resolution"
        if isinstance(exc, (TimeoutError, socket.timeout)) or getattr(exc, "errno", None) == errno.ETIMEDOUT or any(
            token in message for token in ("connection timed out", "connect timeout", "timed out")
        ):
            return "network_timeout"
        if getattr(exc, "errno", None) in {
            errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ECONNREFUSED, errno.ECONNRESET,
        } or any(token in message for token in (
            "network is unreachable", "no route to host", "connection refused",
            "connection reset by peer",
        )):
            return "network_unreachable"
        if any(token in message for token in (
            "invalid dsn", "invalid connection string", "invalid connection option",
            "missing '=' after", "invalid uri query parameter",
        )):
            return "invalid_dsn"
        return "unavailable"

    @classmethod
    def _log_connection_failure(cls, exc) -> None:
        """Emit only allowlisted metadata; never attach the exception or its text."""
        reason_code = cls._connection_failure_reason(exc)
        raw_sqlstate = getattr(exc, "sqlstate", None)
        sqlstate = raw_sqlstate if isinstance(raw_sqlstate, str) and SQLSTATE_RE.fullmatch(raw_sqlstate) else None
        exception_type = type(exc)
        module = exception_type.__module__
        name = exception_type.__name__
        exception_class = (
            name if (module in SAFE_EXCEPTION_MODULES or module.startswith("psycopg."))
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", name)
            else None
        )
        # No exc_info: driver messages often contain the full DSN and credentials.
        logger.error(
            "storage_connection_failed reason_code=%s exception_class=%s sqlstate=%s",
            reason_code, exception_class or "unknown", sqlstate or "unknown",
        )

    def _raise_storage(self, exc):
        if isinstance(exc, sqlite3.OperationalError) and any(x in str(exc).lower() for x in ("locked", "busy")):
            raise StoreBusy() from None
        try:
            import psycopg
            if isinstance(exc, psycopg.errors.LockNotAvailable) or isinstance(exc, psycopg.errors.QueryCanceled):
                raise StoreBusy() from None
            if isinstance(exc, psycopg.Error):
                raise StoreUnavailable() from None
        except ImportError:
            pass
        if isinstance(exc, (sqlite3.Error, OSError, ImportError)):
            raise StoreUnavailable() from None
        raise exc

    def _execute(self, connection, sql, params=()):
        if self.is_postgres:
            sql = sql.replace("?", "%s")
        return connection.execute(sql, params)

    def _configure_transaction(self, connection):
        self._execute(connection, "SET LOCAL lock_timeout = '5s'")
        self._execute(connection, "SET LOCAL statement_timeout = '15s'")
        if self.schema:
            # schema is validated in __init__; transaction-local settings also
            # work through poolers that ignore connection startup parameters.
            self._execute(connection, f'SET LOCAL search_path TO "{self.schema}"')
            actual = self._execute(connection, "SELECT current_schema() AS name").fetchone()
            if not actual or actual["name"] != self.schema:
                raise StoreUnavailable("Database schema isolation could not be verified")

    @contextmanager
    def _transaction(self, write=False, consistent=False) -> Iterator:
        if write and self.readonly:
            raise ValueError("read-only store cannot write")
        connection = self._connect()
        try:
            if self.is_postgres:
                if consistent and not write:
                    self._execute(connection, "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                else:
                    self._execute(connection, "BEGIN")
                self._configure_transaction(connection)
            else:
                self._execute(connection, "BEGIN IMMEDIATE" if write else "BEGIN")
            if write:
                # This row is locked before any game/receipt access on every write.
                self._execute(connection, "SELECT singleton FROM model WHERE singleton=1" + (" FOR UPDATE" if self.is_postgres else ""))
            yield connection
            connection.commit()
        except (StoreError, GameNotFound, IdempotencyConflict, StaleRevision):
            connection.rollback()
            raise
        except Exception as exc:
            connection.rollback()
            self._raise_storage(exc)
        finally:
            connection.close()

    def initialize(self):
        connection = self._connect()
        try:
            if self.is_postgres:
                statements = [
                    "CREATE TABLE IF NOT EXISTS schema_meta (singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL)",
                    "CREATE TABLE IF NOT EXISTS games (game_id TEXT PRIMARY KEY, board TEXT NOT NULL, score BIGINT NOT NULL, revision BIGINT NOT NULL, game_over INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
                    "CREATE TABLE IF NOT EXISTS requests (scope TEXT NOT NULL, request_id TEXT NOT NULL, game_id TEXT, request_json TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(scope, request_id), FOREIGN KEY(game_id) REFERENCES games(game_id))",
                    "CREATE TABLE IF NOT EXISTS transitions (game_id TEXT NOT NULL, resulting_revision BIGINT NOT NULL, before_board TEXT NOT NULL, after_board TEXT NOT NULL, direction TEXT NOT NULL, score_gained BIGINT NOT NULL, source TEXT NOT NULL, terminal INTEGER NOT NULL, reward DOUBLE PRECISION NOT NULL, request_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(game_id, resulting_revision), FOREIGN KEY(game_id) REFERENCES games(game_id))",
                    "CREATE TABLE IF NOT EXISTS model (singleton INTEGER PRIMARY KEY, algorithm TEXT NOT NULL, feature_order TEXT NOT NULL, weights TEXT NOT NULL, model_version BIGINT NOT NULL, training_samples BIGINT NOT NULL, human_moves BIGINT NOT NULL, ai_moves BIGINT NOT NULL, games_started BIGINT NOT NULL, completed_games BIGINT NOT NULL, updated_at TEXT)",
                ]
            else:
                statements = [
                    "CREATE TABLE IF NOT EXISTS schema_meta (singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL)",
                    "CREATE TABLE IF NOT EXISTS games (game_id TEXT PRIMARY KEY, board TEXT NOT NULL, score INTEGER NOT NULL, revision INTEGER NOT NULL, game_over INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
                    "CREATE TABLE IF NOT EXISTS requests (scope TEXT NOT NULL, request_id TEXT NOT NULL, game_id TEXT, request_json TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(scope, request_id), FOREIGN KEY(game_id) REFERENCES games(game_id))",
                    "CREATE TABLE IF NOT EXISTS transitions (game_id TEXT NOT NULL, resulting_revision INTEGER NOT NULL, before_board TEXT NOT NULL, after_board TEXT NOT NULL, direction TEXT NOT NULL, score_gained INTEGER NOT NULL, source TEXT NOT NULL, terminal INTEGER NOT NULL, reward REAL NOT NULL, request_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(game_id, resulting_revision), FOREIGN KEY(game_id) REFERENCES games(game_id))",
                    "CREATE TABLE IF NOT EXISTS model (singleton INTEGER PRIMARY KEY, algorithm TEXT NOT NULL, feature_order TEXT NOT NULL, weights TEXT NOT NULL, model_version INTEGER NOT NULL, training_samples INTEGER NOT NULL, human_moves INTEGER NOT NULL, ai_moves INTEGER NOT NULL, games_started INTEGER NOT NULL, completed_games INTEGER NOT NULL, updated_at TEXT)",
                ]
            if not self.is_postgres:
                connection.execute("BEGIN IMMEDIATE")
            else:
                self._execute(connection, "BEGIN")
                self._configure_transaction(connection)
                # Serialize first-time CREATE TABLE/type creation across cold starts.
                self._execute(connection, "SELECT pg_advisory_xact_lock(hashtext(current_schema()))")
            for statement in statements:
                self._execute(connection, statement)
            self._execute(connection, "INSERT INTO schema_meta(singleton,version) VALUES(1,?) ON CONFLICT(singleton) DO NOTHING", (SCHEMA_VERSION,))
            self._execute(connection, "INSERT INTO model(singleton,algorithm,feature_order,weights,model_version,training_samples,human_moves,ai_moves,games_started,completed_games,updated_at) VALUES(1,?,?,?,?,0,0,0,0,0,NULL) ON CONFLICT(singleton) DO NOTHING",
                          (ALGORITHM, encode(list(FEATURE_ORDER)), encode(INITIAL_WEIGHTS), 0))
            meta = self._execute(connection, "SELECT version FROM schema_meta WHERE singleton=1").fetchone()
            if meta is None or meta["version"] != SCHEMA_VERSION:
                raise RuntimeError("Unsupported database schema version; preserve the database and contact the project maintainer.")
            self._model(self._execute(connection, "SELECT * FROM model WHERE singleton=1").fetchone())
            connection.commit()
        except Exception as exc:
            connection.rollback()
            if isinstance(exc, RuntimeError):
                raise
            self._raise_storage(exc)
        finally:
            connection.close()

    def _checkpoint(self, label):
        if self.failure_injector:
            self.failure_injector(label)

    def _model(self, row):
        if not row:
            raise StoreUnavailable("model row missing")
        try:
            weights = json.loads(row["weights"])
            feature_order = json.loads(row["feature_order"])
            if (row["algorithm"] != ALGORITHM or feature_order != list(FEATURE_ORDER)
                    or len(weights) != len(FEATURE_ORDER)
                    or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                           or not math.isfinite(value) or not -10 <= value <= 10
                           for value in weights)):
                raise ValueError("unsupported model snapshot")
        except (ValueError, TypeError, json.JSONDecodeError):
            raise StoreUnavailable("unsupported model snapshot") from None
        return {"algorithm": row["algorithm"], "weights": weights,
                "model_version": row["model_version"], "training_samples": row["training_samples"],
                "human_moves": row["human_moves"], "ai_moves": row["ai_moves"],
                "games_started": row["games_started"], "completed_games": row["completed_games"],
                "updated_at": row["updated_at"]}

    def _state(self, game_row, model_row):
        board = json.loads(game_row["board"])
        moves = legal_moves(board)
        return {"game_id": game_row["game_id"], "board": board, "score": game_row["score"],
                "game_over": not moves, "revision": game_row["revision"], "legal_moves": moves,
                "model_version": model_row["model_version"]}

    def health(self):
        with self._transaction() as connection:
            row = self._execute(connection, "SELECT * FROM model WHERE singleton=1").fetchone()
            self._model(row)
        return True

    def get_game(self, game_id):
        with self._transaction(consistent=True) as connection:
            game = self._execute(connection, "SELECT * FROM games WHERE game_id=?", (str(game_id),)).fetchone()
            if not game:
                raise GameNotFound()
            model = self._execute(connection, "SELECT * FROM model WHERE singleton=1").fetchone()
            return self._state(game, model)

    def create_game(self, request_id):
        request_id = str(request_id)
        payload = encode({"request_id": request_id})
        with self._transaction(write=True) as connection:
            receipt = self._execute(connection, "SELECT request_json,response_json FROM requests WHERE scope=? AND request_id=?", (CREATE_SCOPE, request_id)).fetchone()
            if receipt:
                if receipt["request_json"] != payload:
                    raise IdempotencyConflict()
                return json.loads(receipt["response_json"])
            game = Game()
            game_id, now = str(uuid.uuid4()), utc_now()
            model = self._model(self._execute(connection, "SELECT * FROM model WHERE singleton=1").fetchone())
            self._execute(connection, "INSERT INTO games VALUES(?,?,?,?,?,?,?)", (game_id, encode(game.board), 0, 0, int(game.game_over), now, now))
            state = self._state({"game_id": game_id, "board": encode(game.board), "score": 0, "revision": 0},
                                {"model_version": model["model_version"]})
            self._checkpoint("create_game")
            self._execute(connection, "UPDATE model SET games_started=games_started+1 WHERE singleton=1")
            self._execute(connection, "INSERT INTO requests VALUES(?,?,?,?,?,?)", (CREATE_SCOPE, request_id, game_id, payload, encode(state), now))
            self._checkpoint("create_receipt")
            return state

    def move(self, game_id, direction, expected_revision, request_id, source):
        game_id, request_id = str(game_id), str(request_id)
        payload = encode({"game_id": game_id, "direction": direction, "expected_revision": expected_revision,
                          "request_id": request_id, "source": source})
        with self._transaction(write=True) as connection:
            game = self._execute(connection, "SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
            if not game:
                raise GameNotFound()
            receipt = self._execute(connection, "SELECT request_json,response_json FROM requests WHERE scope=? AND request_id=?", (game_id, request_id)).fetchone()
            if receipt:
                if receipt["request_json"] != payload:
                    raise IdempotencyConflict()
                return json.loads(receipt["response_json"])
            model_row = self._execute(connection, "SELECT * FROM model WHERE singleton=1").fetchone()
            model = self._model(model_row)
            if expected_revision != game["revision"]:
                raise StaleRevision(self._state(game, model_row))
            before = json.loads(game["board"])
            slid, gained = slide(before, direction)
            moved = slid != before
            now = utc_now()
            if moved:
                after = [row[:] for row in slid]
                occupied = [(r, c) for r in range(4) for c in range(4) if after[r][c] == 0]
                # Same 90/10 spawn policy as Game, sampled only inside this transaction.
                import random
                r, c = random.choice(occupied)
                after[r][c] = 2 if random.random() < 0.9 else 4
                terminal = not legal_moves(after)
                revision = game["revision"] + 1
                score = game["score"] + gained
                shaped = min(math.log2(1 + gained) / 16, 1) - (1 if terminal else 0)
                self._execute(connection, "UPDATE games SET board=?,score=?,revision=?,game_over=?,updated_at=? WHERE game_id=?",
                              (encode(after), score, revision, int(terminal), now, game_id))
                self._checkpoint("game")
                if source == "human":
                    weights, _delta, shaped = update(model["weights"], model["training_samples"], before, after, gained, terminal)
                    self._execute(connection, "UPDATE model SET weights=?,model_version=model_version+1,training_samples=training_samples+1,human_moves=human_moves+1,completed_games=completed_games+?,updated_at=? WHERE singleton=1",
                                  (encode(weights), int(terminal), now))
                else:
                    self._execute(connection, "UPDATE model SET ai_moves=ai_moves+1,completed_games=completed_games+? WHERE singleton=1", (int(terminal),))
                self._checkpoint("model")
                self._execute(connection, "INSERT INTO transitions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                              (game_id, revision, encode(before), encode(after), direction, gained, source, int(terminal), shaped, request_id, now))
                self._checkpoint("transition")
                game = {"game_id": game_id, "board": encode(after), "score": score, "revision": revision}
                model_row = self._execute(connection, "SELECT * FROM model WHERE singleton=1").fetchone()
            state = self._state(game, model_row)
            result = {**state, "moved": moved, "score_gained": gained if moved else 0}
            self._execute(connection, "INSERT INTO requests VALUES(?,?,?,?,?,?)", (game_id, request_id, game_id, payload, encode(result), now))
            self._checkpoint("receipt")
            return result

    def get_hint(self, game_id):
        with self._transaction(consistent=True) as connection:
            game = self._execute(connection, "SELECT * FROM games WHERE game_id=?", (str(game_id),)).fetchone()
            if not game:
                raise GameNotFound()
            model = self._model(self._execute(connection, "SELECT * FROM model WHERE singleton=1").fetchone())
            board = json.loads(game["board"])
            return {"game_id": str(game_id), "revision": game["revision"],
                    "direction": choose_hint(board, model["weights"]), "legal_moves": legal_moves(board),
                    "model_version": model["model_version"]}

    def stats(self):
        with self._transaction(consistent=True) as connection:
            model = self._model(self._execute(connection, "SELECT * FROM model WHERE singleton=1").fetchone())
            return {"algorithm": model["algorithm"], "model_version": model["model_version"],
                    "training_samples": model["training_samples"], "human_moves": model["human_moves"],
                    "ai_moves": model["ai_moves"], "games_started": model["games_started"],
                    "completed_games": model["completed_games"], "updated_at": model["updated_at"]}

    def snapshot(self):
        """Read-only learner snapshot used by the offline evaluator."""
        with self._transaction(consistent=True) as connection:
            model = self._model(self._execute(connection, "SELECT * FROM model WHERE singleton=1").fetchone())
            return {"algorithm": model["algorithm"], "weights": model["weights"], "model_version": model["model_version"],
                    "training_samples": model["training_samples"]}

