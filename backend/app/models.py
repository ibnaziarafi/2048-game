from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateGameRequest(StrictRequest):
    request_id: UUID


class MoveRequest(StrictRequest):
    game_id: UUID
    direction: Literal["left", "right", "up", "down"]
    expected_revision: StrictInt = Field(ge=0)
    request_id: UUID
    source: Literal["human", "ai"]
