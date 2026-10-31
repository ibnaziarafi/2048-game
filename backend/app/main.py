from __future__ import annotations

import os
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.models import CreateGameRequest, MoveRequest
from app.store import (GameNotFound, IdempotencyConflict, StaleRevision,
                       Store, StoreBusy, StoreError)


def _store_from_config(config):
    config = config or {}
    if "store" in config:
        return config["store"]
    env = config.get("env")
    if env is None:
        return Store.from_environment()
    return Store.from_environment(env)


def create_app(store=None, config=None):
    config = dict(config or {})
    configured_store = store or _store_from_config(config)
    env = config.get("env", os.environ)
    origins_setting = config.get("cors_origins", env.get("CORS_ORIGINS", "http://localhost:5173"))
    origins = [origin.strip() for origin in origins_setting.split(",") if origin.strip()]
    if "*" in origins:
        raise ValueError("CORS_ORIGINS cannot use wildcard origins")

    @asynccontextmanager
    async def lifespan(_app):
        configured_store.initialize()
        yield

    application = FastAPI(title="2048 Game API", version="2.0.0", lifespan=lifespan)
    application.state.store = configured_store
    application.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                               allow_methods=["GET", "POST", "OPTIONS"],
                               allow_headers=["Content-Type"])

    @application.middleware("http")
    async def no_store(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(GameNotFound)
    async def game_not_found(_request, _exc):
        return JSONResponse(status_code=404, content={"detail": {"code": "game_not_found", "message": "Game not found; start a new game."}})

    @application.exception_handler(IdempotencyConflict)
    async def idempotency_conflict(_request, _exc):
        return JSONResponse(status_code=409, content={"detail": {"code": "idempotency_conflict", "message": "Request ID was already used with different fields."}})

    @application.exception_handler(StaleRevision)
    async def stale_revision(_request, exc):
        return JSONResponse(status_code=409, content={"detail": {"code": "stale_revision", "message": "Game changed; use the current state.", "state": exc.state}})

    @application.exception_handler(StoreBusy)
    async def storage_busy(_request, _exc):
        return JSONResponse(status_code=503, headers={"Retry-After": "1"}, content={"detail": {"code": "storage_busy", "message": "Storage is busy; retry the same request."}})

    @application.exception_handler(StoreError)
    async def storage_unavailable(_request, _exc):
        return JSONResponse(status_code=503, content={"detail": {"code": "storage_unavailable", "message": "Game storage is unavailable."}})

    @application.get("/health")
    def health_check():
        configured_store.health()
        return {"status": "ok"}

    @application.post("/game")
    def create_game(payload: CreateGameRequest):
        return configured_store.create_game(payload.request_id)

    @application.get("/game")
    def get_game_without_path(game_id: UUID | None = Query(default=None)):
        if game_id is None:
            raise HTTPException(status_code=422, detail={"code": "game_id_required", "message": "Supply an explicit game_id."})
        return configured_store.get_game(game_id)

    @application.get("/game/{game_id}")
    def get_game(game_id: UUID):
        return configured_store.get_game(game_id)

    @application.post("/game/move")
    def make_move(payload: MoveRequest):
        return configured_store.move(payload.game_id, payload.direction, payload.expected_revision,
                                     payload.request_id, payload.source)

    @application.get("/game/{game_id}/hint")
    def get_hint(game_id: UUID):
        return configured_store.get_hint(game_id)

    @application.get("/ai/stats")
    def get_stats():
        return configured_store.stats()

    return application


app = create_app()
