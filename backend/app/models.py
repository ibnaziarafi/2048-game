from typing import List, Optional
from pydantic import BaseModel, Field


class MoveRequest(BaseModel):
    direction: str = Field(..., description="Move direction: left, right, up, or down")
    game_id: Optional[str] = Field(None, description="Optional game session ID")


class GameStateResponse(BaseModel):
    game_id: str
    board: List[List[int]]
    score: int
    game_over: bool
