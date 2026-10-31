"""RPC integration against TEST_DATABASE_URL only, in unique disposable schemas."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql
from psycopg.rows import dict_row

from app.game import legal_moves, slide
from app.learner import reward, update
from app.store import Store


@pytest.fixture
def rpc():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL absent; HTTP RPC SQL is not verified locally")
    prefix = "rpc2048_" + uuid4().hex
    helper = prefix + "_rpc"
    function = prefix + "_storage"
    created_roles = []
    with psycopg.connect(url, autocommit=True) as admin:
        owner = admin.execute("SELECT current_user").fetchone()[0]
        for role in ("anon", "authenticated", "service_role"):
            if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role)))
                created_roles.append(role)
        admin.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(sql.Identifier(prefix), sql.Identifier(owner)))
    store = Store(database_url=url, schema=prefix)
    store.initialize()
    script = (Path(__file__).parents[1] / "sql" / "supabase_http.sql").read_text()
    # The application SQL uses fixed names deliberately; substitute ALL app and
    # wrapper names in this isolated fixture. Never load deployment credentials.
    script = script.replace("game2048", prefix)
    script = script.replace("'postgres'", "'" + owner.replace("'", "''") + "'")
    script = script.replace("AUTHORIZATION postgres", "AUTHORIZATION " + sql.Identifier(owner).as_string())

    def deploy():
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(script, prepare=False)

    def call(action, payload, role="service_role"):
        with psycopg.connect(url, row_factory=dict_row) as conn:
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            return conn.execute(sql.SQL("SELECT public.{}(%s,%s::jsonb) AS value").format(sql.Identifier(function)),
                                (action, json.dumps(payload))).fetchone()["value"]

    try:
        deploy()
        yield call, store, deploy
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP FUNCTION IF EXISTS public.{}(text,jsonb)").format(sql.Identifier(function)))
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(helper)))
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(prefix)))
            for role in created_roles:
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def create(call, request_id=None):
    board = [[2, 2, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    payload = {"request": {"request_id": str(request_id or uuid4())},
               "game": {"game_id": str(uuid4()), "board": board}, "legal_moves": legal_moves(board)}
    return payload, call("commit_create", payload)


def proposal(snapshot, *, source="human", request_id=None, direction="left"):
    game, model = snapshot["game"], snapshot["model"]
    before = game["board"]
    board, gained = slide(before, direction)
    moved = board != before
    if moved:
        r, c = next((r, c) for r in range(4) for c in range(4) if board[r][c] == 0)
        board[r][c] = 2
    moves = legal_moves(board)
    terminal = not moves
    weights = None
    if moved and source == "human":
        weights, _, _ = update(model["weights"], model["training_samples"], before, board, gained, terminal)
    return {"request": {"game_id": game["game_id"], "request_id": str(request_id or uuid4()),
                        "direction": direction, "expected_revision": game["revision"], "source": source},
            "game": {"board": board, "score": game["score"] + gained,
                     "revision": game["revision"] + int(moved), "game_over": terminal},
            "legal_moves": moves, "moved": moved, "score_gained": gained,
            "reward": reward(gained, terminal), "model_version": model["model_version"], "weights": weights}


def test_rpc_create_move_replay_and_redeployment_preserve_data(rpc):
    call, store, deploy = rpc
    payload, created = create(call)
    assert created["status"] == "ok"
    assert call("commit_create", {**payload, "game": {**payload["game"], "game_id": str(uuid4())}}) == created
    game_id = created["result"]["game_id"]
    before = call("snapshot", {"game_id": game_id})
    commit = proposal(before)
    moved = call("commit_move", commit)
    assert moved["status"] == "ok" and moved["result"]["revision"] == 1
    assert moved["result"]["score_gained"] == 4
    assert call("commit_move", commit) == moved
    conflict = {**commit, "request": {**commit["request"], "direction": "right"}}
    assert call("commit_move", conflict)["code"] == "idempotency_conflict"
    after = call("snapshot", {"game_id": game_id, "request_id": commit["request"]["request_id"]})
    assert after["receipt"]["response"] == moved["result"]
    assert after["model"]["training_samples"] == 1 and after["model"]["weights"] != before["model"]["weights"]
    assert store.snapshot()["model_version"] == 1
    deploy()
    assert call("snapshot", {"game_id": game_id})["model"] == after["model"]
    assert call("commit_move", commit) == moved


def test_rpc_parallel_same_revision_and_same_request_commit_once(rpc):
    call, _, _ = rpc
    create_id = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        created_twice = list(pool.map(lambda _: create(call, create_id)[1], range(2)))
    assert created_twice[0] == created_twice[1]
    assert call("snapshot", {})["model"]["games_started"] == 1
    _, created = create(call)
    snap = call("snapshot", {"game_id": created["result"]["game_id"]})
    first, second = proposal(snap), proposal(snap)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda p: call("commit_move", p), (first, second)))
    assert sorted(r["status"] for r in results) == ["error", "ok"]
    assert next(r for r in results if r["status"] == "error")["code"] == "stale_revision"
    _, another = create(call)
    same = proposal(call("snapshot", {"game_id": another["result"]["game_id"]}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call("commit_move", same), range(2)))
    assert results[0] == results[1]
    assert call("snapshot", {})["model"]["training_samples"] == 2


def test_rpc_model_cas_retry_and_ai_noop_do_not_train(rpc):
    call, _, _ = rpc
    _, a = create(call)
    _, b = create(call)
    game_a, game_b = a["result"]["game_id"], b["result"]["game_id"]
    move_a = proposal(call("snapshot", {"game_id": game_a}))
    move_b = proposal(call("snapshot", {"game_id": game_b}))
    assert call("commit_move", move_a)["status"] == "ok"
    assert call("commit_move", move_b) == {"status": "retry", "code": "model_conflict"}
    unchanged = call("snapshot", {"game_id": game_b, "request_id": move_b["request"]["request_id"]})
    assert unchanged["game"]["revision"] == 0 and unchanged["receipt"] is None
    retry = proposal(unchanged, request_id=move_b["request"]["request_id"])
    assert call("commit_move", retry)["status"] == "ok"
    _, c = create(call)
    game_c = c["result"]["game_id"]
    # Initial first row is already compressed for up; an up action is a no-op.
    noop = proposal(call("snapshot", {"game_id": game_c}), direction="up")
    assert call("commit_move", noop)["result"]["moved"] is False
    ai = proposal(call("snapshot", {"game_id": game_c}), source="ai")
    assert call("commit_move", ai)["status"] == "ok"
    model = call("snapshot", {})["model"]
    assert model["training_samples"] == model["human_moves"] == model["model_version"] == 2
    assert model["ai_moves"] == 1


def test_rpc_rejects_public_roles_and_invalid_proposals_without_side_effects(rpc):
    call, store, _ = rpc
    for role in ("anon", "authenticated"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            call("snapshot", {}, role=role)
    with store._transaction() as connection:
        assert not store._execute(connection, "SELECT has_schema_privilege('service_role', ?, 'USAGE') AS allowed", (store.schema,)).fetchone()["allowed"]
    _, created = create(call)
    game_id = created["result"]["game_id"]
    before = call("snapshot", {"game_id": game_id})
    invalid = proposal(before)
    invalid["request"]["source"] = None
    assert call("commit_move", invalid)["code"] == "invalid_payload"
    invalid = proposal(before)
    invalid["game"]["score"] = 999
    assert call("commit_move", invalid)["code"] == "invalid_payload"
    after = call("snapshot", {"game_id": game_id})
    assert after["model"] == before["model"] and after["game"] == before["game"]


def test_rpc_failure_at_receipt_write_rolls_back_game_model_and_transition(rpc):
    call, store, _ = rpc
    _, created = create(call)
    game_id = created["result"]["game_id"]
    before = call("snapshot", {"game_id": game_id})
    # Only this disposable schema is altered. Force the final receipt write to
    # fail after the game/model/transition writes have executed.
    with store._transaction(write=True) as connection:
        connection.execute(sql.SQL("ALTER TABLE {}.requests ADD CONSTRAINT review_fail_receipt CHECK (scope='create')")
                           .format(sql.Identifier(store.schema)))
    commit = proposal(before)
    with pytest.raises(psycopg.errors.CheckViolation):
        call("commit_move", commit)
    after = call("snapshot", {"game_id": game_id, "request_id": commit["request"]["request_id"]})
    assert after["model"] == before["model"] and after["game"] == before["game"]
    assert after["receipt"] is None
    with store._transaction() as connection:
        assert store._execute(connection, "SELECT count(*) AS n FROM transitions").fetchone()["n"] == 0
