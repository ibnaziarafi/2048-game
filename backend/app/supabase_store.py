"""Supabase PostgREST adapter for the narrow game2048_storage RPC."""

from __future__ import annotations

import base64
import json
import math
import random
import re
import uuid
from urllib.parse import urlsplit

import httpx

from app.game import Game, legal_moves, slide
from app.learner import ALGORITHM, FEATURE_ORDER, hint as choose_hint, reward, update
from app.store import (
    GameNotFound,
    IdempotencyConflict,
    StaleRevision,
    Store,
    StoreBusy,
    StoreUnavailable,
)

MAX_MODEL_CONFLICT_RETRIES = 4
RPC_PATH = "/rest/v1/rpc/game2048_storage"
_SECRET_KEY_RE = re.compile(r"^sb_secret_[A-Za-z0-9_-]+$")


def _legacy_service_role_key(value: str) -> bool:
    pieces = value.split(".")
    if (len(pieces) != 3 or not value.startswith("eyJ")
            or any(not re.fullmatch(r"[A-Za-z0-9_-]+", piece) for piece in pieces)):
        return False
    try:
        header_segment = pieces[0]
        encoded = pieces[1]
        header = json.loads(base64.urlsafe_b64decode(header_segment + "=" * (-len(header_segment) % 4)))
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return (isinstance(header, dict) and isinstance(payload, dict)
            and payload.get("role") == "service_role")


def validate_supabase_url(value: str) -> str:
    """Accept only a project HTTPS origin; never preserve a path/query/credential."""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        port = parsed.port
    except (TypeError, ValueError):
        raise ValueError("SUPABASE_URL must be an HTTPS Supabase project origin.") from None
    labels = hostname.split(".")
    project_ref = labels[0] if len(labels) == 3 and labels[1:] == ["supabase", "co"] else ""
    valid_ref = bool(project_ref and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", project_ref))
    if (parsed.scheme != "https" or not valid_ref or "@" in parsed.netloc or parsed.username or parsed.password
            or port not in (None, 443) or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment or "?" in value or "#" in value):
        raise ValueError("SUPABASE_URL must be an HTTPS Supabase project origin.")
    return f"https://{hostname}"


def validate_supabase_secret_key(value: str) -> bool:
    """Validate key family without exposing or logging key material."""
    if not isinstance(value, str):
        raise ValueError("SUPABASE_SECRET_KEY must be a Supabase secret key or legacy service_role JWT.")
    if _SECRET_KEY_RE.fullmatch(value):
        return False
    if _legacy_service_role_key(value):
        return True
    raise ValueError("SUPABASE_SECRET_KEY must be a Supabase secret key or legacy service_role JWT.")


class SupabaseStore(Store):
    """Store-compatible interface backed by one atomic server-only Postgres RPC."""

    def __init__(self, supabase_url: str, secret_key: str, *, transport=None,
                 timeout: float = 10.0):
        self.supabase_url = validate_supabase_url(supabase_url)
        self.secret_key = secret_key
        self._legacy_jwt = validate_supabase_secret_key(secret_key)
        self._transport = transport
        self._timeout = timeout
        self.database_url = None
        self.db_path = None
        self.schema = None
        self.readonly = False
        self.failure_injector = None

    @property
    def is_postgres(self):
        return True

    @classmethod
    def from_environment(cls, env, *, transport=None):
        return cls(env["SUPABASE_URL"], env["SUPABASE_SECRET_KEY"], transport=transport)

    def _rpc(self, action: str, payload: dict) -> dict:
        headers = {"apikey": self.secret_key, "Content-Type": "application/json"}
        if self._legacy_jwt:
            headers["Authorization"] = f"Bearer {self.secret_key}"
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.post(
                    f"{self.supabase_url}{RPC_PATH}",
                    headers=headers,
                    json={"p_action": action, "p_payload": payload},
                )
        except httpx.RequestError:
            # The caller can retry this exact request ID to discover a committed receipt.
            raise StoreUnavailable() from None
        if response.status_code != 200:
            code = None
            try:
                body = response.json()
                if isinstance(body, dict) and isinstance(body.get("code"), str):
                    code = body["code"]
            except (ValueError, json.JSONDecodeError):
                pass
            if code in {"55P03", "57014"}:
                raise StoreBusy() from None
            raise StoreUnavailable() from None
        try:
            result = response.json()
        except (ValueError, json.JSONDecodeError):
            raise StoreUnavailable() from None
        if not isinstance(result, dict):
            raise StoreUnavailable() from None
        if result.get("status") == "ok":
            return result
        if result.get("status") == "retry" and result.get("code") == "model_conflict":
            return result
        if result.get("status") != "error":
            raise StoreUnavailable() from None
        code = result.get("code")
        if code == "game_not_found":
            raise GameNotFound()
        if code == "idempotency_conflict":
            raise IdempotencyConflict()
        if code == "stale_revision":
            game = result.get("game")
            model = self._validate_model(result.get("model"))
            if not isinstance(game, dict):
                raise StoreUnavailable() from None
            self._validate_game(game)
            raise StaleRevision(self._state(game, model))
        # invalid_payload, permission errors and unknown RPC envelopes remain generic.
        raise StoreUnavailable() from None

    def _validate_model(self, model):
        if not isinstance(model, dict):
            raise StoreUnavailable() from None
        weights = model.get("weights")
        feature_order = model.get("feature_order")
        if (model.get("algorithm") != ALGORITHM or feature_order != list(FEATURE_ORDER)
                or not isinstance(weights, list) or len(weights) != len(FEATURE_ORDER)
                or any(type(weight) not in (int, float) or not math.isfinite(weight) or not -10 <= weight <= 10
                       for weight in weights)):
            raise StoreUnavailable() from None
        counters = ("model_version", "training_samples", "human_moves", "ai_moves",
                    "games_started", "completed_games")
        if any(type(model.get(counter)) is not int or model[counter] < 0 for counter in counters):
            raise StoreUnavailable() from None
        if model["model_version"] != model["training_samples"] or model["training_samples"] != model["human_moves"]:
            raise StoreUnavailable() from None
        return model

    def _snapshot(self, game_id=None, request_id=None):
        payload = {}
        if game_id is not None:
            payload["game_id"] = str(game_id)
        if request_id is not None:
            payload["request_id"] = str(request_id)
        result = self._rpc("snapshot", payload)
        if type(result.get("schema_version")) is not int or result["schema_version"] != 1:
            raise StoreUnavailable() from None
        model = self._validate_model(result.get("model"))
        game = result.get("game")
        if game is not None:
            if not isinstance(game, dict):
                raise StoreUnavailable() from None
            self._validate_game(game)
            if game_id is not None and game["game_id"] != str(game_id):
                raise StoreUnavailable() from None
        receipt = result.get("receipt")
        if receipt is not None and not isinstance(receipt, dict):
            raise StoreUnavailable() from None
        return {"game": game, "model": model, "receipt": receipt}

    @staticmethod
    def _validate_game(game):
        if not isinstance(game, dict):
            raise StoreUnavailable() from None
        board = game.get("board")
        try:
            if str(uuid.UUID(game.get("game_id"))) != game.get("game_id"):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise StoreUnavailable() from None
        if (not isinstance(board, list) or len(board) != 4
                or any(not isinstance(row, list) or len(row) != 4 for row in board)
                or any(type(tile) is not int or tile < 0 or tile == 1 or (tile and tile & (tile - 1)) for row in board for tile in row)
                or type(game.get("score")) is not int or game["score"] < 0
                or type(game.get("revision")) is not int or game["revision"] < 0
                or type(game.get("game_over")) is not int or game["game_over"] not in (0, 1)):
            raise StoreUnavailable() from None

    @classmethod
    def _validate_public_result(cls, result, expected_game_id=None, expected_move=None):
        if not isinstance(result, dict):
            raise StoreUnavailable() from None
        is_move = "moved" in result or "score_gained" in result
        if expected_move is not None and is_move is not expected_move:
            raise StoreUnavailable() from None
        required = {"game_id", "board", "score", "game_over", "revision", "legal_moves", "model_version"}
        if is_move:
            required |= {"moved", "score_gained"}
        if set(result) != required:
            raise StoreUnavailable() from None
        game = {"game_id": result["game_id"], "board": result["board"], "score": result["score"],
                "revision": result["revision"], "game_over": int(result["game_over"])
                if type(result["game_over"]) is bool else result["game_over"]}
        cls._validate_game(game)
        moves = legal_moves(result["board"])
        if (type(result["game_over"]) is not bool or result["game_over"] != (not moves)
                or result["legal_moves"] != moves
                or type(result["model_version"]) is not int or result["model_version"] < 0):
            raise StoreUnavailable() from None
        if is_move and (type(result["moved"]) is not bool
                        or type(result["score_gained"]) is not int or result["score_gained"] < 0
                        or (not result["moved"] and result["score_gained"] != 0)):
            raise StoreUnavailable() from None
        if expected_game_id is not None and result["game_id"] != str(expected_game_id):
            raise StoreUnavailable() from None
        return result

    @staticmethod
    def _state(game, model):
        board = game["board"]
        moves = legal_moves(board)
        return {"game_id": str(game["game_id"]), "board": board, "score": game["score"],
                "game_over": not moves, "revision": game["revision"], "legal_moves": moves,
                "model_version": model["model_version"]}

    def initialize(self):
        self._snapshot()

    def health(self):
        self._snapshot()
        return True

    def get_game(self, game_id):
        snapshot = self._snapshot(game_id=game_id)
        if snapshot["game"] is None:
            raise GameNotFound()
        return self._state(snapshot["game"], snapshot["model"])

    @staticmethod
    def _receipt_result(receipt, request):
        saved_request = receipt.get("request")
        if not isinstance(saved_request, dict) or saved_request != request:
            raise IdempotencyConflict()
        response = receipt.get("response")
        return SupabaseStore._validate_public_result(
            response, request.get("game_id"), expected_move=("game_id" in request),
        )

    def create_game(self, request_id):
        request = {"request_id": str(request_id)}
        snapshot = self._snapshot(request_id=request["request_id"])
        if snapshot["receipt"] is not None:
            return self._receipt_result(snapshot["receipt"], request)
        proposed = Game()
        game = {"game_id": str(uuid.uuid4()), "board": proposed.board}
        result = self._rpc("commit_create", {
            "request": request, "game": game, "legal_moves": legal_moves(proposed.board),
        })
        if result.get("status") != "ok":
            raise StoreUnavailable() from None
        # An identical concurrent create can win with a different proposed game UUID.
        return self._validate_public_result(result.get("result"), expected_move=False)

    @staticmethod
    def _same_request(receipt, request):
        if receipt is None:
            return None
        return SupabaseStore._receipt_result(receipt, request)

    def move(self, game_id, direction, expected_revision, request_id, source):
        request = {"game_id": str(game_id), "request_id": str(request_id),
                   "direction": direction, "expected_revision": expected_revision, "source": source}
        snapshot = self._snapshot(game_id=game_id, request_id=request_id)
        replay = self._same_request(snapshot["receipt"], request)
        if replay is not None:
            return replay
        game, model = snapshot["game"], snapshot["model"]
        if game is None:
            raise GameNotFound()
        if expected_revision != game["revision"]:
            raise StaleRevision(self._state(game, model))
        before = game["board"]
        slid, score_gained = slide(before, direction)
        moved = slid != before
        proposed_board = [row[:] for row in slid]
        if moved:
            empty = [(r, c) for r in range(4) for c in range(4) if proposed_board[r][c] == 0]
            if not empty:
                raise StoreUnavailable() from None
            row, column = random.choice(empty)
            proposed_board[row][column] = 2 if random.random() < 0.9 else 4

        for attempt in range(MAX_MODEL_CONFLICT_RETRIES + 1):
            terminal = not legal_moves(proposed_board)
            next_revision = game["revision"] + int(moved)
            next_score = game["score"] + (score_gained if moved else 0)
            weights = None
            current_model_version = model["model_version"]
            shaped_reward = reward(score_gained, terminal) if moved else 0.0
            if moved and source == "human":
                weights, _delta, shaped_reward = update(
                    model["weights"], model["training_samples"], before,
                    proposed_board, score_gained, terminal,
                )
            payload = {
                "request": request,
                "game": {"board": proposed_board, "score": next_score,
                         "revision": next_revision, "game_over": terminal},
                "legal_moves": legal_moves(proposed_board),
                "moved": moved,
                "score_gained": score_gained if moved else 0,
                "reward": shaped_reward,
                "model_version": current_model_version,
                "weights": weights,
            }
            result = self._rpc("commit_move", payload)
            if result.get("status") == "ok":
                return self._validate_public_result(
                    result.get("result"), game_id, expected_move=True,
                )
            if result.get("status") != "retry":
                raise StoreUnavailable() from None
            if attempt == MAX_MODEL_CONFLICT_RETRIES:
                raise StoreBusy() from None
            snapshot = self._snapshot(game_id=game_id, request_id=request_id)
            replay = self._same_request(snapshot["receipt"], request)
            if replay is not None:
                return replay
            game, model = snapshot["game"], snapshot["model"]
            if game is None:
                raise GameNotFound()
            if expected_revision != game["revision"]:
                raise StaleRevision(self._state(game, model))
        raise StoreBusy() from None

    def get_hint(self, game_id):
        snapshot = self._snapshot(game_id=game_id)
        if snapshot["game"] is None:
            raise GameNotFound()
        game, model = snapshot["game"], snapshot["model"]
        state = self._state(game, model)
        return {"game_id": state["game_id"], "revision": state["revision"],
                "direction": choose_hint(state["board"], model["weights"]),
                "legal_moves": state["legal_moves"], "model_version": state["model_version"]}

    def stats(self):
        model = self._snapshot()["model"]
        return {"algorithm": model["algorithm"], "model_version": model["model_version"],
                "training_samples": model["training_samples"], "human_moves": model["human_moves"],
                "ai_moves": model["ai_moves"], "games_started": model["games_started"],
                "completed_games": model["completed_games"], "updated_at": model.get("updated_at")}

    def snapshot(self):
        model = self._snapshot()["model"]
        return {"algorithm": model["algorithm"], "weights": model["weights"],
                "model_version": model["model_version"], "training_samples": model["training_samples"]}
