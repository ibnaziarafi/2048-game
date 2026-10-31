"""Configuration validation needs no database connection or player data."""

import pytest

from app.store import Store


def test_private_schema_reaches_postgres_store_from_environment():
    store = Store.from_environment({
        "VERCEL": "1", "DATABASE_URL": "postgresql://example.invalid/postgres",
        "GAME_DB_SCHEMA": "game2048",
    })
    assert store.is_postgres and store.schema == "game2048"
    assert store.db_path is None


@pytest.mark.parametrize("schema", [
    "", " game2048", "game2048 ", "game-2048", "game2048;drop", 'game"2048',
    "2048", "café", "a" * 64, "public", "PUBLIC", "pg_catalog", "pg_temp_1",
    "information_schema", "auth", "storage", "realtime", "extensions", "vault",
    "graphql_public", "supabase_functions", "supabase_custom",
])
def test_invalid_or_managed_environment_schema_is_rejected(schema):
    with pytest.raises(ValueError, match="GAME_DB_SCHEMA"):
        Store.from_environment({"DATABASE_URL": "postgresql://example.invalid/postgres", "GAME_DB_SCHEMA": schema})


def test_vercel_postgres_requires_explicit_private_schema():
    with pytest.raises(RuntimeError, match="GAME_DB_SCHEMA"):
        Store.from_environment({"VERCEL": "1", "DATABASE_URL": "postgresql://example.invalid/postgres"})


def test_schema_without_postgres_does_not_silently_fall_back_to_sqlite():
    with pytest.raises(ValueError, match="requires DATABASE_URL"):
        Store.from_environment({"GAME_DB_SCHEMA": "game2048"})


def test_local_sqlite_configuration_is_preserved():
    store = Store.from_environment({"GAME_DB_PATH": "/unused/local.sqlite3"})
    assert not store.is_postgres and store.db_path == "/unused/local.sqlite3"
    assert store.schema is None


def test_schema_name_cannot_be_silently_truncated_by_postgres():
    assert Store(database_url="postgresql://example.invalid/postgres", schema="a" * 63).schema == "a" * 63
    with pytest.raises(ValueError, match="schema name"):
        Store(database_url="postgresql://example.invalid/postgres", schema="a" * 64)
