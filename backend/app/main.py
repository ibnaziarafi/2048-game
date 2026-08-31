import uuid
from typing import Dict, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.game import Game
from app.models import MoveRequest, GameStateResponse

app = FastAPI(title="2048 Game API", version="1.0.0")

# Enable CORS for React frontend (Vite default port 5173 / any origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple in-memory game state store
games: Dict[str, Game] = {}
latest_game_id: Optional[str] = None


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/game", response_model=GameStateResponse)
def create_game():
    """Starts a new game of 2048."""
    global latest_game_id
    game_id = str(uuid.uuid4())
    new_game = Game()
    games[game_id] = new_game
    latest_game_id = game_id

    return GameStateResponse(
        game_id=game_id,
        board=new_game.board,
        score=new_game.score,
        game_over=new_game.game_over,
    )


@app.get("/game", response_model=GameStateResponse)
@app.get("/game/{game_id}", response_model=GameStateResponse)
def get_game(game_id: Optional[str] = None):
    """Retrieves current state of a game."""
    target_id = game_id or latest_game_id
    if not target_id or target_id not in games:
        raise HTTPException(status_code=404, detail="Game not found. Please start a new game.")

    game = games[target_id]
    return GameStateResponse(
        game_id=target_id,
        board=game.board,
        score=game.score,
        game_over=game.game_over,
    )


@app.post("/game/move", response_model=GameStateResponse)
def make_move(request: MoveRequest):
    """Executes a move in direction: 'left', 'right', 'up', or 'down'."""
    target_id = request.game_id or latest_game_id
    if not target_id or target_id not in games:
        raise HTTPException(status_code=404, detail="Game not found. Please start a new game.")

    direction = request.direction.lower().strip()
    valid_directions = {"left", "right", "up", "down"}
    if direction not in valid_directions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid direction '{request.direction}'. Must be one of: {', '.join(valid_directions)}"
        )

    game = games[target_id]
    if game.game_over:
        return GameStateResponse(
            game_id=target_id,
            board=game.board,
            score=game.score,
            game_over=True,
        )

    # Perform move (updates board, score, random tile, game_over if valid move)
    game.move(direction)

    return GameStateResponse(
        game_id=target_id,
        board=game.board,
        score=game.score,
        game_over=game.game_over,
    )
