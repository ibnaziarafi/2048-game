"""Optional real-Postgres correctness tests; only TEST_DATABASE_URL is read."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql

from app.store import Store, StoreUnavailable


@pytest.fixture
def postgres_store():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured; Postgres parity remains unverified locally")
    schema = "test_2048_" + uuid4().hex
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    store = Store(database_url=database_url, schema=schema)
    try:
        store.initialize()
        yield store
    finally:
        with psycopg.connect(database_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def seed_game(store):
    game_id = str(uuid4())
    board = [[2, 2, 4, 4], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    with store._transaction(write=True) as connection:
        store._execute(connection, "INSERT INTO games VALUES(?,?,?,?,?,?,?)",
                       (game_id, json.dumps(board), 0, 0, 0, "now", "now"))
    return game_id


def test_postgres_serializes_competing_revision_and_trains_once(postgres_store):
    from fastapi.testclient import TestClient
    from app.main import create_app

    game_id = seed_game(postgres_store)
    app = create_app(store=postgres_store)
    barrier = threading.Barrier(2)
    payloads = [{"game_id": game_id, "direction": "left", "expected_revision": 0,
                 "request_id": str(uuid4()), "source": "human"} for _ in range(2)]

    def send(payload):
        barrier.wait(timeout=5)
        return TestClient(app).post("/game/move", json=payload).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(send, payloads))
    assert statuses == [200, 409]
    assert postgres_store.get_game(game_id)["revision"] == 1
    assert postgres_store.stats()["training_samples"] == 1


def test_postgres_receipt_and_model_survive_store_reopen(postgres_store):
    game_id = seed_game(postgres_store)
    request_id = str(uuid4())
    original = postgres_store.move(game_id, "left", 0, request_id, "human")
    reopened = Store(database_url=postgres_store.database_url, schema=postgres_store.schema)
    assert reopened.get_game(game_id)["revision"] == 1
    assert reopened.move(game_id, "left", 0, request_id, "human") == original
    assert reopened.stats()["training_samples"] == 1


def test_postgres_concurrent_cold_start_initializes_one_schema(postgres_store):
    database_url = postgres_store.database_url
    schema = "test_2048_init_" + uuid4().hex
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    stores = [Store(database_url=database_url, schema=schema) for _ in range(2)]
    barrier = threading.Barrier(2)
    def initialize(store):
        barrier.wait(timeout=5)
        store.initialize()
        return store.stats()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            snapshots = list(pool.map(initialize, stores))
        assert snapshots[0] == snapshots[1]
        assert snapshots[0]["model_version"] == 0
    finally:
        with psycopg.connect(database_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_postgres_schema_scope_is_transaction_local_and_missing_schema_fails_closed(postgres_store, monkeypatch):
    original_connect = psycopg.connect

    def connect_without_startup_options(*args, **kwargs):
        # A pooler may reject or ignore startup options. Isolation must be set
        # inside the transaction rather than depend on those parameters.
        assert "options" not in kwargs
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(psycopg, "connect", connect_without_startup_options)
    for settings in ({"consistent": True}, {"write": True}):
        with postgres_store._transaction(**settings) as connection:
            actual = postgres_store._execute(connection, "SELECT current_schema() AS name").fetchone()
            assert actual["name"] == postgres_store.schema

    with original_connect(postgres_store.database_url, autocommit=True) as admin:
        before = admin.execute("SELECT schemaname, tablename FROM pg_tables ORDER BY schemaname, tablename").fetchall()
    missing = Store(database_url=postgres_store.database_url, schema="missing_2048_" + uuid4().hex)
    with pytest.raises(StoreUnavailable, match="schema isolation"):
        missing.initialize()
    with original_connect(postgres_store.database_url, autocommit=True) as admin:
        after = admin.execute("SELECT schemaname, tablename FROM pg_tables ORDER BY schemaname, tablename").fetchall()
    assert after == before
