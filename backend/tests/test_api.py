import json
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.game import DIRECTIONS, slide
from app.main import create_app
from app.store import Store


@pytest.fixture
def harness(tmp_path):
    store = Store(db_path=str(tmp_path / "games.sqlite3"))
    app = create_app(store=store, config={"cors_origins": "http://localhost:5173"})
    with TestClient(app) as client:
        yield client, store


def game_fixture(store, board=None):
    game_id = str(uuid4())
    board = board or [[2, 2, 4, 4], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    connection = store._connect()
    try:
        connection.execute("INSERT INTO games VALUES(?,?,?,?,?,?,?)",
                           (game_id, json.dumps(board), 0, 0, 0, "now", "now"))
        connection.commit()
    finally:
        connection.close()
    return game_id


def move_payload(game_id, *, direction="left", revision=0, request_id=None, source="human"):
    return {"game_id": game_id, "direction": direction, "expected_revision": revision,
            "request_id": str(request_id or uuid4()), "source": source}


def test_explicit_game_create_and_health(harness):
    client, _store = harness
    created = client.post("/game", json={"request_id": str(uuid4())})
    assert created.status_code == 200
    state = created.json()
    assert len(state["board"]) == 4 and all(len(row) == 4 for row in state["board"])
    assert state["revision"] == 0 and state["model_version"] == 0
    assert state["legal_moves"] == [d for d in DIRECTIONS if slide(state["board"], d)[0] != state["board"]]
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/game").status_code == 422
    assert client.get("/game").json()["detail"]["code"] == "game_id_required"
    assert client.get("/game/" + state["game_id"]).headers["cache-control"] == "no-store"


def test_create_retry_is_idempotent_and_conflicting_payload_is_rejected(harness):
    client, store = harness
    request_id = str(uuid4())
    first = client.post("/game", json={"request_id": request_id})
    retry = client.post("/game", json={"request_id": request_id})
    assert retry.json() == first.json()
    assert client.get("/ai/stats").json()["games_started"] == 1


def test_duplicate_move_replays_exactly_and_conflicting_id_does_not_mutate(harness):
    client, store = harness
    game_id = game_fixture(store)
    payload = move_payload(game_id)
    first = client.post("/game/move", json=payload)
    replay = client.post("/game/move", json=payload)
    assert first.status_code == 200 and replay.json() == first.json()
    assert first.json()["score_gained"] == 12
    assert client.get("/ai/stats").json()["training_samples"] == 1
    conflict = {**payload, "direction": "right"}
    assert client.post("/game/move", json=conflict).json()["detail"]["code"] == "idempotency_conflict"
    assert client.get("/ai/stats").json()["training_samples"] == 1


def test_stale_revision_returns_current_state_without_training(harness):
    client, store = harness
    game_id = game_fixture(store)
    first = client.post("/game/move", json=move_payload(game_id))
    stale = client.post("/game/move", json=move_payload(game_id, direction="right"))
    assert first.status_code == 200 and stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_revision"
    assert stale.json()["detail"]["state"]["revision"] == 1
    assert client.get("/ai/stats").json()["training_samples"] == 1


def test_simultaneous_same_revision_requests_commit_only_once(harness):
    client, store = harness
    game_id = game_fixture(store)
    barrier = threading.Barrier(2)
    payloads = [move_payload(game_id, request_id=uuid4()) for _ in range(2)]

    def send(payload):
        barrier.wait(timeout=5)
        return TestClient(client.app).post("/game/move", json=payload).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(send, payloads))
    assert statuses == [200, 409]
    assert client.get("/game/" + game_id).json()["revision"] == 1
    assert client.get("/ai/stats").json()["training_samples"] == 1


def test_concurrent_human_moves_in_independent_games_both_train(harness):
    client, store = harness
    ids = [game_fixture(store), game_fixture(store)]
    barrier = threading.Barrier(2)
    def send(game_id):
        barrier.wait(timeout=5)
        return TestClient(client.app).post("/game/move", json=move_payload(game_id)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(send, ids))
    assert statuses == [200, 200]
    assert client.get("/ai/stats").json()["training_samples"] == 2


def test_human_only_training_ai_noop_hint_and_stats(harness):
    client, store = harness
    game_id = game_fixture(store)
    before = client.get("/ai/stats").json()
    initial_weights = store.snapshot()["weights"]
    hint = client.get(f"/game/{game_id}/hint").json()
    assert hint["direction"] in hint["legal_moves"]
    assert client.get("/ai/stats").json() == before
    noop_id = str(uuid4())
    full_board = [[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]]
    terminal_game = game_fixture(store, full_board)
    noop = client.post("/game/move", json=move_payload(terminal_game, direction="left", request_id=noop_id))
    assert noop.status_code == 200 and noop.json()["moved"] is False
    assert client.get("/ai/stats").json() == before
    assert client.get(f"/game/{terminal_game}/hint").json()["direction"] is None
    ai = client.post("/game/move", json=move_payload(game_id, source="ai"))
    assert ai.status_code == 200
    stats = client.get("/ai/stats").json()
    assert stats["training_samples"] == 0 and stats["model_version"] == 0 and stats["ai_moves"] == 1
    assert store.snapshot()["weights"] == initial_weights
    human_game = game_fixture(store)
    human_payload = move_payload(human_game)
    human = client.post("/game/move", json=human_payload)
    assert human.status_code == 200
    learned_weights = store.snapshot()["weights"]
    assert learned_weights != initial_weights
    replay = client.post("/game/move", json=human_payload)
    assert replay.json() == human.json() and store.snapshot()["weights"] == learned_weights
    stats = client.get("/ai/stats").json()
    assert stats["training_samples"] == stats["human_moves"] == stats["model_version"] == 1
    assert set(stats) == {"algorithm", "model_version", "training_samples", "human_moves", "ai_moves", "games_started", "completed_games", "updated_at"}


def test_terminal_human_transition_trains_once_with_terminal_counter(harness, monkeypatch):
    client, store = harness
    board = [[32, 16, 4, 2], [16, 32, 8, 8], [8, 4, 8, 4], [2, 8, 2, 2]]
    game_id = game_fixture(store, board)
    monkeypatch.setattr("random.choice", lambda _items: (3, 2))
    monkeypatch.setattr("random.random", lambda: 0.95)
    response = client.post("/game/move", json=move_payload(game_id, direction="up"))
    assert response.status_code == 200 and response.json()["game_over"] is True
    stats = client.get("/ai/stats").json()
    assert stats["completed_games"] == 1 and stats["training_samples"] == 1


def test_rollback_after_game_write_restores_everything(tmp_path):
    triggered = {"done": False}
    def fail(label):
        if label == "receipt" and not triggered["done"]:
            triggered["done"] = True
            raise RuntimeError("injected failure")
    store = Store(db_path=str(tmp_path / "rollback.sqlite3"), failure_injector=fail)
    store.initialize()
    game_id = game_fixture(store)
    with pytest.raises(RuntimeError, match="injected failure"):
        store.move(game_id, "left", 0, str(uuid4()), "human")
    clean_store = Store(db_path=str(tmp_path / "rollback.sqlite3"))
    assert clean_store.get_game(game_id)["revision"] == 0
    assert clean_store.stats()["training_samples"] == 0


def test_reopen_preserves_game_receipt_and_model(tmp_path):
    path = str(tmp_path / "reopen.sqlite3")
    store = Store(db_path=path)
    store.initialize()
    game_id = game_fixture(store)
    request_id = str(uuid4())
    result = store.move(game_id, "left", 0, request_id, "human")
    reopened = Store(db_path=path)
    reopened.initialize()
    assert reopened.get_game(game_id)["revision"] == 1
    assert reopened.move(game_id, "left", 0, request_id, "human") == result
    assert reopened.stats()["training_samples"] == 1


@pytest.mark.parametrize("payload", [
    {}, {"direction": "diagonal"}, {"direction": "left", "extra": 1},
    {"direction": "left", "expected_revision": True},
])
def test_invalid_move_requests_are_rejected_without_training(harness, payload):
    client, store = harness
    game_id = game_fixture(store)
    valid = {} if not payload else move_payload(game_id)
    valid.update(payload)
    response = client.post("/game/move", json=valid)
    assert response.status_code == 422
    assert client.get("/ai/stats").json()["training_samples"] == 0


def test_vercel_without_database_refuses_ephemeral_storage():
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app(config={"env": {"VERCEL": "1"}})


def test_wildcard_cors_is_rejected():
    with pytest.raises(ValueError, match="wildcard"):
        create_app(store=Store(db_path=":memory:"), config={"cors_origins": "*"})
