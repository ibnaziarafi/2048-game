import pytest
from fastapi.testclient import TestClient
from app.main import app, games


client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_game_api():
    response = client.post("/game")
    assert response.status_code == 200
    data = response.json()
    assert "game_id" in data
    assert len(data["board"]) == 4
    assert len(data["board"][0]) == 4
    assert data["score"] == 0
    assert data["game_over"] is False


def test_make_move_api():
    create_res = client.post("/game")
    game_id = create_res.json()["game_id"]

    move_res = client.post("/game/move", json={"direction": "left", "game_id": game_id})
    assert move_res.status_code == 200
    data = move_res.json()
    assert data["game_id"] == game_id
    assert "board" in data
    assert "score" in data
    assert "game_over" in data


def test_invalid_direction_api():
    create_res = client.post("/game")
    game_id = create_res.json()["game_id"]

    move_res = client.post("/game/move", json={"direction": "diagonal", "game_id": game_id})
    assert move_res.status_code == 400
    assert "Invalid direction" in move_res.json()["detail"]


def test_game_not_found_api():
    move_res = client.post("/game/move", json={"direction": "left", "game_id": "non-existent-id"})
    assert move_res.status_code == 404
