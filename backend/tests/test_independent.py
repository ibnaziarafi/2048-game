"""Independent adversarial integration checks; all database files are temporary."""

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import Store


def command(state, request_id=None, source="human"):
    return {"game_id": state["game_id"], "direction": state["legal_moves"][0],
            "expected_revision": state["revision"], "request_id": request_id or str(uuid4()),
            "source": source}


def test_two_app_instances_replay_once_then_preserve_newer_state_and_ai_exclusion(tmp_path):
    """Lost-response replay may be older than current state but cannot undo it."""
    path = str(tmp_path / "independent.sqlite3")
    first_store, second_store = Store(db_path=path), Store(db_path=path)
    with TestClient(create_app(store=first_store)) as first, TestClient(create_app(store=second_store)) as second:
        initial = first.post("/game", json={"request_id": str(uuid4())}).json()
        payload = command(initial)
        barrier = threading.Barrier(2)

        def send(client):
            barrier.wait(timeout=5)
            return client.post("/game/move", json=payload)

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(send, (first, second)))
        assert [response.status_code for response in responses] == [200, 200]
        human_result = responses[0].json()
        assert human_result == responses[1].json()
        assert human_result["revision"] == 1
        learned = first_store.snapshot()
        assert learned["training_samples"] == 1
        ai_result = second.post("/game/move", json=command(human_result, source="ai")).json()
        assert ai_result["revision"] == 2
        assert second_store.snapshot() == learned

        assert first.post("/game/move", json=payload).json() == human_result
        current = second.get("/game/" + initial["game_id"]).json()
        assert current["revision"] == 2 and current["board"] == ai_result["board"]
        stats = second.get("/ai/stats").json()
        assert stats["training_samples"] == stats["human_moves"] == stats["model_version"] == 1
        assert stats["ai_moves"] == 1

        # Attribution is part of request identity; it cannot be altered on replay.
        conflict = first.post("/game/move", json={**payload, "source": "ai"})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "idempotency_conflict"
        assert second.get("/ai/stats").json() == stats

    reopened = Store(db_path=path)
    reopened.initialize()
    assert reopened.get_game(initial["game_id"]) == current
    assert reopened.snapshot() == learned


@pytest.mark.parametrize("failure_point", ["game", "model", "transition", "receipt"])
def test_every_partial_move_write_rolls_back_and_same_identity_recovers(tmp_path, failure_point):
    """A failed transaction must leave neither learning nor a false replay receipt."""
    store = Store(db_path=str(tmp_path / "rollback.sqlite3"))
    with TestClient(create_app(store=store), raise_server_exceptions=False) as client:
        initial = client.post("/game", json={"request_id": str(uuid4())}).json()
        snapshot, stats = store.snapshot(), store.stats()
        payload = command(initial)

        def injected(label):
            if label == failure_point:
                raise RuntimeError("independent injected failure")

        store.failure_injector = injected
        assert client.post("/game/move", json=payload).status_code == 500
        store.failure_injector = None
        assert store.get_game(initial["game_id"]) == initial
        assert store.snapshot() == snapshot
        assert store.stats() == stats
        with store._transaction() as connection:
            assert connection.execute("SELECT COUNT(*) FROM transitions").fetchone()[0] == 0

        retry = client.post("/game/move", json=payload)
        assert retry.status_code == 200 and retry.json()["revision"] == 1
        assert client.post("/game/move", json=payload).json() == retry.json()
        assert store.stats()["training_samples"] == 1
        assert store.stats()["human_moves"] == 1


def test_client_cannot_supply_training_data_or_coerce_revision(tmp_path):
    store = Store(db_path=str(tmp_path / "validation.sqlite3"))
    with TestClient(create_app(store=store)) as client:
        initial = client.post("/game", json={"request_id": str(uuid4())}).json()
        before = store.snapshot(), store.stats()
        valid = command(initial)
        invalid = [
            {**valid, "board": initial["board"]},
            {**valid, "reward": 1000000},
            {**valid, "weights": [10] * 8},
            *({**valid, "expected_revision": value} for value in (False, -1, 0.0, "0")),
            {**valid, "source": "Human"},
        ]
        for payload in invalid:
            assert client.post("/game/move", json=payload).status_code == 422
            assert store.get_game(initial["game_id"]) == initial
            assert (store.snapshot(), store.stats()) == before
