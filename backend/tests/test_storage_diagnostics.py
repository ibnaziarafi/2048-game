import errno
import logging
import socket
import ssl

import psycopg
import pytest

from app.store import Store, StoreUnavailable


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (psycopg.errors.InvalidPassword("password=private"), "auth_failure"),
        (socket.gaierror(socket.EAI_NONAME, "could not resolve db.internal"), "host_resolution"),
        (OSError(errno.ENETUNREACH, "network unreachable to db.internal"), "network_unreachable"),
        (TimeoutError("connect timed out to db.internal"), "network_timeout"),
        (ssl.SSLError("TLS certificate verify failed for db.internal"), "tls_failure"),
        (psycopg.OperationalError("invalid connection option password=private"), "invalid_dsn"),
        (ImportError("No module named psycopg"), "missing_driver"),
        (psycopg.OperationalError("connection failed"), "unavailable"),
    ],
)
def test_connection_failure_reasons_are_stable_allowlisted_codes(error, expected):
    assert Store._connection_failure_reason(error) == expected


def test_connect_logs_safe_metadata_only_and_keeps_http_storage_error_generic(monkeypatch, caplog):
    secret_url = "postgresql://private_user:very-secret-password@private.host.invalid/private_db"
    secret_message = f"could not translate host name private.host.invalid for DSN {secret_url}"

    def fail_connect(*_args, **_kwargs):
        raise psycopg.OperationalError(secret_message)

    monkeypatch.setattr(psycopg, "connect", fail_connect)
    caplog.set_level(logging.ERROR, logger="app.store")
    with pytest.raises(StoreUnavailable) as failure:
        Store(database_url=secret_url)._connect()

    assert str(failure.value) == ""
    assert "storage_connection_failed" in caplog.text
    assert "reason_code=host_resolution" in caplog.text
    assert "OperationalError" in caplog.text
    assert secret_url not in caplog.text
    assert secret_message not in caplog.text
    assert "very-secret-password" not in caplog.text
    assert "private_user" not in caplog.text
    assert "private.host.invalid" not in caplog.text
    record = caplog.records[-1]
    assert not record.exc_info
    assert record.args == ("host_resolution", "OperationalError", "unknown")


def test_only_valid_sqlstate_is_logged(caplog):
    class SafeDatabaseError(Exception):
        sqlstate = "28P01"

    caplog.set_level(logging.ERROR, logger="app.store")
    Store._log_connection_failure(SafeDatabaseError("do not log this secret"))
    assert "sqlstate=28P01" in caplog.text
    assert "do not log this secret" not in caplog.text

    caplog.clear()
    class MalformedStateError(Exception):
        sqlstate = "28P01-password=secret"

    Store._log_connection_failure(MalformedStateError("private"))
    assert "sqlstate=unknown" in caplog.text
    assert "28P01-password" not in caplog.text
    assert "private" not in caplog.text


def test_genuine_psycopg_auth_sqlstate_is_safe_and_actionable(caplog):
    error = psycopg.errors.InvalidPassword("password=driver-secret")
    caplog.set_level(logging.ERROR, logger="app.store")
    Store._log_connection_failure(error)
    assert Store._connection_failure_reason(error) == "auth_failure"
    assert "reason_code=auth_failure" in caplog.text
    assert "exception_class=InvalidPassword" in caplog.text
    assert "sqlstate=28P01" in caplog.text
    assert "driver-secret" not in caplog.text


@pytest.mark.parametrize("database_url", [
    "postgresql://alice:secret@db.example.invalid:5432/game?sslmode=require",
    "postgres://alice:secret@db.example.invalid/game",
    "host=db.example.invalid port=5432 dbname=game user=alice password=secret sslmode=require",
])
def test_postgres_uri_and_libpq_dsn_are_accepted(database_url):
    store = Store.from_environment({"DATABASE_URL": database_url})
    assert store.is_postgres and store.database_url == database_url


def test_supabase_http_project_url_is_rejected_without_echoing_it():
    http_url = "https://private-user:private-password@project.supabase.co"
    with pytest.raises(ValueError) as failure:
        Store.from_environment({"DATABASE_URL": http_url, "VERCEL": "1"})
    message = str(failure.value)
    assert "Connect > Transaction pooler" in message
    assert http_url not in message
    assert "private-password" not in message
    assert "private-user" not in message
    assert "project.supabase.co" not in message
