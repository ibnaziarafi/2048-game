import base64
import copy
import json
from uuid import uuid4

import httpx
import pytest

from app.game import legal_moves, slide
from app.learner import ALGORITHM, FEATURE_ORDER, INITIAL_WEIGHTS
from app.store import GameNotFound, IdempotencyConflict, StaleRevision, Store, StoreBusy, StoreUnavailable
from app.supabase_store import RPC_PATH, SupabaseStore

PROJECT_URL = "https://project-ref.supabase.co"
MODERN_KEY = "sb_secret_test_only_key"


def model():
    return {"singleton": 1, "algorithm": ALGORITHM, "feature_order": list(FEATURE_ORDER),
            "weights": list(INITIAL_WEIGHTS), "model_version": 0, "training_samples": 0,
            "human_moves": 0, "ai_moves": 0, "games_started": 0,
            "completed_games": 0, "updated_at": None}


def raw_game(board=None):
    return {"game_id": str(uuid4()), "board": board or [[2, 2, 4, 4], [0] * 4, [0] * 4, [0] * 4],
            "score": 0, "revision": 0, "game_over": 0,
            "created_at": "now", "updated_at": "now"}


class RPCFake:
    def __init__(self, game=None):
        self.model = model()
        self.game = game
        self.receipts = {}
        self.calls = []
        self.model_conflicts = 0
        self.http_error = None
        self.malformed_game = False
        self.lose_next_move_response = False
        self.race_create_after_snapshot = False
        self.race_move_after_snapshot = False

    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, request):
        self.calls.append(request)
        assert request.url.path == RPC_PATH
        body = json.loads(request.content)
        action, payload = body["p_action"], body["p_payload"]
        if self.http_error:
            status, code = self.http_error
            return httpx.Response(status, json={"code": code})
        if action == "snapshot":
            game_id = payload.get("game_id")
            request_id = payload.get("request_id")
            scope = game_id or "create"
            receipt = self.receipts.get((scope, request_id)) if request_id else None
            return httpx.Response(200, json={
                "status": "ok", "schema_version": 1, "model": self.model,
                "game": ("malformed" if self.malformed_game else self.game) if game_id else None,
                "receipt": copy.deepcopy(receipt),
            })
        if action == "commit_create":
            request_record = payload["request"]
            key = ("create", request_record["request_id"])
            if key in self.receipts:
                saved = self.receipts[key]
                if saved["request"] != request_record:
                    return httpx.Response(200, json={"status": "error", "code": "idempotency_conflict"})
                return httpx.Response(200, json={"status": "ok", "result": saved["response"]})
            game = payload["game"]
            if self.race_create_after_snapshot:
                self.race_create_after_snapshot = False
                game = {**game, "game_id": str(uuid4())}
            self.game = {**game, "score": 0, "revision": 0, "game_over": 0}
            self.model["games_started"] += 1
            result = {"game_id": game["game_id"], "board": game["board"], "score": 0,
                      "revision": 0, "game_over": False,
                      "legal_moves": payload["legal_moves"], "model_version": self.model["model_version"]}
            self.receipts[key] = {"request": request_record, "response": result}
            return httpx.Response(200, json={"status": "ok", "result": result})
        if action != "commit_move":
            return httpx.Response(200, json={"status": "error", "code": "invalid_payload"})
        req = payload["request"]
        key = (req["game_id"], req["request_id"])
        if key in self.receipts:
            saved = self.receipts[key]
            if saved["request"] != req:
                return httpx.Response(200, json={"status": "error", "code": "idempotency_conflict"})
            return httpx.Response(200, json={"status": "ok", "result": saved["response"]})
        if self.game is None or req["game_id"] != self.game["game_id"]:
            return httpx.Response(200, json={"status": "error", "code": "game_not_found"})
        if req["expected_revision"] != self.game["revision"]:
            return httpx.Response(200, json={"status": "error", "code": "stale_revision",
                                             "game": self.game, "model": self.model})
        if payload["moved"] and req["source"] == "human" and self.model_conflicts:
            self.model_conflicts -= 1
            self.model["model_version"] += 1
            self.model["training_samples"] += 1
            self.model["human_moves"] += 1
            self.model["weights"][1] += 0.01
            return httpx.Response(200, json={"status": "retry", "code": "model_conflict"})
        if payload["moved"] and self.race_move_after_snapshot:
            self.race_move_after_snapshot = False
            alternate = [row[:] for row in payload["game"]["board"]]
            if alternate[0][2]:
                alternate[0][3], alternate[0][2] = alternate[0][2], 0
            else:
                alternate[0][2], alternate[0][3] = alternate[0][3], 0
            self.game = {**self.game, **payload["game"], "board": alternate,
                         "game_over": int(payload["game"]["game_over"])}
            self.model["weights"] = payload["weights"]
            self.model["model_version"] += 1
            self.model["training_samples"] += 1
            self.model["human_moves"] += 1
            self.model["updated_at"] = "now"
            result = {"game_id": self.game["game_id"], "board": alternate,
                      "score": self.game["score"], "revision": self.game["revision"],
                      "game_over": bool(self.game["game_over"]), "legal_moves": legal_moves(alternate),
                      "model_version": self.model["model_version"], "moved": True,
                      "score_gained": payload["score_gained"]}
            self.receipts[key] = {"request": req, "response": result}
            return httpx.Response(200, json={"status": "ok", "result": result})
        if payload["moved"] and req["source"] == "human" and payload["model_version"] != self.model["model_version"]:
            return httpx.Response(200, json={"status": "retry", "code": "model_conflict"})
        self.game = {**self.game, **payload["game"], "game_over": int(payload["game"]["game_over"])}
        if payload["moved"] and req["source"] == "human":
            self.model["weights"] = payload["weights"]
            self.model["model_version"] += 1
            self.model["training_samples"] += 1
            self.model["human_moves"] += 1
            self.model["completed_games"] += int(payload["game"]["game_over"])
            self.model["updated_at"] = "now"
        elif payload["moved"]:
            self.model["ai_moves"] += 1
            self.model["completed_games"] += int(payload["game"]["game_over"])
        result = {"game_id": self.game["game_id"], "board": self.game["board"],
                  "score": self.game["score"], "revision": self.game["revision"],
                  "game_over": bool(self.game["game_over"]), "legal_moves": payload["legal_moves"],
                  "model_version": self.model["model_version"], "moved": payload["moved"],
                  "score_gained": payload["score_gained"]}
        self.receipts[key] = {"request": req, "response": result}
        if self.lose_next_move_response:
            self.lose_next_move_response = False
            raise httpx.ReadTimeout("response lost after commit; do not leak connection or key")
        return httpx.Response(200, json={"status": "ok", "result": result})


def make_store(fake, key=MODERN_KEY):
    return SupabaseStore(PROJECT_URL, key, transport=fake.transport())


def test_supabase_pair_precedes_postgres_and_partial_pair_fails_closed():
    env = {"SUPABASE_URL": PROJECT_URL, "SUPABASE_SECRET_KEY": MODERN_KEY,
           "DATABASE_URL": "postgresql://stale.invalid/unused", "VERCEL": "1"}
    selected = Store.from_environment(env)
    assert isinstance(selected, SupabaseStore)
    assert selected.database_url is None
    with pytest.raises(ValueError, match="must both be configured"):
        Store.from_environment({"SUPABASE_URL": PROJECT_URL, "DATABASE_URL": "postgresql://old/unused"})
    with pytest.raises(ValueError, match="must both be configured"):
        Store.from_environment({"SUPABASE_SECRET_KEY": MODERN_KEY, "DATABASE_URL": "postgresql://old/unused"})


@pytest.mark.parametrize("url", [
    "http://project-ref.supabase.co", "https://project-ref.supabase.co/rest/v1",
    "https://user:pass@project-ref.supabase.co", "https://project-ref.supabase.co?query=1",
    "https://@project-ref.supabase.co", "https://project-ref.supabase.co?", "https://project-ref.supabase.co#",
    "https://project-ref.supabase.co.evil.invalid", "https://nested.project-ref.supabase.co",
])
def test_supabase_url_rejects_non_origin_urls_without_echoing_input(url):
    with pytest.raises(ValueError) as failure:
        SupabaseStore(url, MODERN_KEY)
    assert url not in str(failure.value)
    assert "pass" not in str(failure.value)


def test_modern_secret_is_apikey_only_and_url_uses_rpc():
    fake = RPCFake()
    store = make_store(fake)
    assert store.health() is True
    request = fake.calls[0]
    assert request.url == f"{PROJECT_URL}{RPC_PATH}"
    assert request.headers["apikey"] == MODERN_KEY
    assert "authorization" not in request.headers
    assert json.loads(request.content) == {"p_action": "snapshot", "p_payload": {}}


def legacy_service_role_jwt(role="service_role"):
    payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{payload}.signature"


def test_legacy_service_role_jwt_uses_bearer_but_public_keys_are_rejected():
    jwt = legacy_service_role_jwt()
    fake = RPCFake()
    make_store(fake, jwt).health()
    headers = fake.calls[0].headers
    assert headers["apikey"] == jwt and headers["authorization"] == f"Bearer {jwt}"
    for key in ("sb_publishable_public", legacy_service_role_jwt("anon"), "not-a-key",
                jwt + "\n", jwt.replace("signature", "signa!ture"), "eyJ.."):
        with pytest.raises(ValueError, match="service_role JWT"):
            SupabaseStore(PROJECT_URL, key)


def test_model_conflict_resnapshots_recomputes_and_keeps_sampled_spawn_and_request_id(monkeypatch):
    fake = RPCFake(raw_game())
    fake.model_conflicts = 1
    store = make_store(fake)
    monkeypatch.setattr("app.supabase_store.random.choice", lambda cells: cells[0])
    monkeypatch.setattr("app.supabase_store.random.random", lambda: 0.5)
    request_id = str(uuid4())
    result = store.move(fake.game["game_id"], "left", 0, request_id, "human")
    commits = [json.loads(request.content)["p_payload"] for request in fake.calls
               if json.loads(request.content)["p_action"] == "commit_move"]
    assert len(commits) == 2
    assert commits[0]["request"] == commits[1]["request"]
    assert commits[0]["game"] == commits[1]["game"]  # same sampled spawn on model-only conflict
    assert commits[0]["weights"] != commits[1]["weights"]
    assert result["revision"] == 1 and result["model_version"] == 2
    assert fake.model["training_samples"] == 2  # one competing update plus this committed move


def test_duplicate_lost_response_replays_same_request_without_second_move(monkeypatch):
    fake = RPCFake(raw_game())
    fake.lose_next_move_response = True
    store = make_store(fake)
    monkeypatch.setattr("app.supabase_store.random.choice", lambda cells: cells[0])
    payload = {"game_id": fake.game["game_id"], "direction": "left", "expected_revision": 0,
               "request_id": str(uuid4()), "source": "human"}
    with pytest.raises(StoreUnavailable) as uncertain:
        store.move(**payload)
    assert uncertain.value.__cause__ is None
    first_commit_count = sum(json.loads(req.content)["p_action"] == "commit_move" for req in fake.calls)
    state_after_commit = copy.deepcopy(fake.game)
    replay = store.move(**payload)
    assert replay["revision"] == state_after_commit["revision"] == 1
    assert fake.game == state_after_commit
    assert fake.model["training_samples"] == 1
    assert sum(json.loads(req.content)["p_action"] == "commit_move" for req in fake.calls) == first_commit_count


def test_same_request_id_with_changed_fields_conflicts_before_any_new_commit():
    fake = RPCFake(raw_game())
    store = make_store(fake)
    game_id, request_id = fake.game["game_id"], str(uuid4())
    first = store.move(game_id, "left", 0, request_id, "human")
    commits_before = sum(json.loads(req.content)["p_action"] == "commit_move" for req in fake.calls)
    with pytest.raises(IdempotencyConflict):
        store.move(game_id, "up", 0, request_id, "human")
    assert first["revision"] == fake.game["revision"] == 1
    assert fake.model["training_samples"] == 1
    assert sum(json.loads(req.content)["p_action"] == "commit_move" for req in fake.calls) == commits_before


def test_noop_and_ai_payloads_never_send_weights_or_train():
    terminal = [[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]]
    fake = RPCFake(raw_game(terminal))
    store = make_store(fake)
    noop = store.move(fake.game["game_id"], "left", 0, str(uuid4()), "human")
    noop_commit = next(json.loads(req.content)["p_payload"] for req in fake.calls
                       if json.loads(req.content)["p_action"] == "commit_move")
    assert noop["moved"] is False and noop_commit["weights"] is None
    assert fake.model["training_samples"] == fake.model["model_version"] == 0

    fake = RPCFake(raw_game())
    store = make_store(fake)
    ai = store.move(fake.game["game_id"], "left", 0, str(uuid4()), "ai")
    ai_commit = next(json.loads(req.content)["p_payload"] for req in fake.calls
                     if json.loads(req.content)["p_action"] == "commit_move")
    assert ai["moved"] is True and ai_commit["weights"] is None
    assert fake.model["training_samples"] == fake.model["model_version"] == 0
    assert fake.model["ai_moves"] == 1


def test_stale_revision_and_domain_error_mapping():
    game = raw_game()
    fake = RPCFake(game)
    store = make_store(fake)
    with pytest.raises(StaleRevision) as stale:
        store.move(game["game_id"], "left", 4, str(uuid4()), "human")
    assert stale.value.state["revision"] == 0

    for code, expected in (("game_not_found", GameNotFound),
                           ("idempotency_conflict", IdempotencyConflict),
                           ("invalid_payload", StoreUnavailable)):
        fake = RPCFake(game)
        fake.handle = lambda _request, code=code: httpx.Response(200, json={"status": "error", "code": code})
        store = make_store(fake)
        with pytest.raises(expected):
            store.get_game(game["game_id"])


@pytest.mark.parametrize("sqlstate", ["55P03", "57014"])
def test_http_sql_lock_errors_map_to_storage_busy(sqlstate):
    fake = RPCFake()
    fake.http_error = (500, sqlstate)
    with pytest.raises(StoreBusy):
        make_store(fake).health()


def test_permission_and_transport_errors_stay_generic_and_secret_safe():
    fake = RPCFake()
    fake.http_error = (403, "42501")
    with pytest.raises(StoreUnavailable):
        make_store(fake).health()

    def timeout(request):
        raise httpx.ConnectError("request failed with key=" + MODERN_KEY + " at " + str(request.url))
    store = SupabaseStore(PROJECT_URL, MODERN_KEY, transport=httpx.MockTransport(timeout))
    with pytest.raises(StoreUnavailable) as failure:
        store.health()
    assert str(failure.value) == "" and failure.value.__cause__ is None
    assert MODERN_KEY not in repr(failure.value)


def test_malformed_game_snapshot_maps_to_store_unavailable():
    fake = RPCFake(raw_game())
    fake.malformed_game = True
    with pytest.raises(StoreUnavailable):
        make_store(fake).get_game(fake.game["game_id"])


def test_malformed_stale_envelope_and_success_receipt_fail_closed():
    fake = RPCFake(raw_game())
    original_handler = fake.handle
    def malformed_stale(request):
        body = json.loads(request.content)
        if body["p_action"] == "commit_move":
            return httpx.Response(200, json={"status": "error", "code": "stale_revision",
                                             "game": {}, "model": fake.model})
        return original_handler(request)
    fake.handle = malformed_stale
    store = make_store(fake)
    with pytest.raises(StoreUnavailable):
        store.move(fake.game["game_id"], "left", 0, str(uuid4()), "human")

    fake = RPCFake()
    original_handler = fake.handle
    def malformed_create(request):
        body = json.loads(request.content)
        if body["p_action"] == "commit_create":
            return httpx.Response(200, json={"status": "ok", "result": {}})
        return original_handler(request)
    fake.handle = malformed_create
    with pytest.raises(StoreUnavailable):
        make_store(fake).create_game(str(uuid4()))


def test_create_uses_receipt_scope_and_returns_saved_state():
    fake = RPCFake()
    store = make_store(fake)
    request_id = str(uuid4())
    first = store.create_game(request_id)
    replay = store.create_game(request_id)
    assert first == replay and fake.model["games_started"] == 1
    actions = [json.loads(req.content)["p_action"] for req in fake.calls]
    assert actions == ["snapshot", "commit_create", "snapshot"]


def test_create_race_accepts_winning_receipt_with_different_proposed_game_id():
    fake = RPCFake()
    fake.race_create_after_snapshot = True
    result = make_store(fake).create_game(str(uuid4()))
    assert result["game_id"] == fake.game["game_id"]
    assert result["game_id"] != json.loads(fake.calls[1].content)["p_payload"]["game"]["game_id"]
    assert fake.model["games_started"] == 1


def test_move_race_returns_winning_receipt_even_if_spawn_differs(monkeypatch):
    fake = RPCFake(raw_game())
    fake.race_move_after_snapshot = True
    store = make_store(fake)
    monkeypatch.setattr("app.supabase_store.random.choice", lambda cells: cells[0])
    payload = {"game_id": fake.game["game_id"], "direction": "left", "expected_revision": 0,
               "request_id": str(uuid4()), "source": "human"}
    result = store.move(**payload)
    proposed = json.loads(fake.calls[1].content)["p_payload"]["game"]["board"]
    assert result["board"] == fake.game["board"] and result["board"] != proposed
    assert result["revision"] == 1 and fake.model["training_samples"] == 1
