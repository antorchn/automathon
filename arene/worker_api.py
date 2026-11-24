"""API HTTP du worker (authentifiée par Bearer WORKER_TOKEN)."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from arene import scheduler
from arene.config import Settings


class ClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)


class HeartbeatRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)


class FailRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="worker_aborted", max_length=2000)


class GameResultPayload(BaseModel):
    index: int
    outcome: str = Field(pattern="^(left|right|draw)$")
    end_frame: int = Field(ge=0)
    left_health: float
    right_health: float
    left_mean_ms: float = 0.0
    left_max_ms: float = 0.0
    right_mean_ms: float = 0.0
    right_max_ms: float = 0.0
    left_exceptions: int = 0
    right_exceptions: int = 0
    failure_side: str = ""
    failure_reason: str = ""


class JobResultRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    validation_ok: bool | None = None
    validation_error: str | None = None
    games: list[GameResultPayload] = Field(default_factory=list)


def require_worker_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    settings: Settings = request.app.state.settings
    expected = f"Bearer {settings.worker_token}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Jeton worker invalide")


def create_worker_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix=settings.url("/api/worker"), dependencies=[Depends(require_worker_token)])

    @router.post("/claim")
    def claim(body: ClaimRequest, request: Request):
        with request.app.state.session_factory() as session:
            job = scheduler.claim_job(session, request.app.state.settings, body.worker_id)
        if job is None:
            return Response(status_code=204)
        return job

    @router.get("/agents/{agent_id}/zip")
    def download_agent_zip(agent_id: str, request: Request):
        from sqlalchemy import text

        with request.app.state.session_factory() as session:
            row = session.execute(
                text("SELECT zip_key FROM agents WHERE id = :id"),
                {"id": agent_id},
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Agent introuvable")
        try:
            data = request.app.state.store.get_bytes(row[0])
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Archive zip introuvable")
        return Response(content=data, media_type="application/zip")

    @router.post("/jobs/{job_id}/heartbeat")
    def heartbeat(job_id: str, body: HeartbeatRequest, request: Request):
        with request.app.state.session_factory() as session:
            try:
                exp = scheduler.heartbeat_job(session, request.app.state.settings, job_id, body.worker_id)
            except LookupError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "lease_expires_at": exp}

    @router.post("/jobs/{job_id}/replays/{game_index}")
    async def upload_replay(job_id: str, game_index: int, request: Request):
        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="Replay vide")
        key = scheduler.store_job_replay(request.app.state.store, job_id, game_index, data)
        return {"ok": True, "key": key}

    @router.post("/jobs/{job_id}/result")
    def submit_result(job_id: str, body: JobResultRequest, request: Request):
        with request.app.state.session_factory() as session:
            try:
                res: dict[str, Any] = scheduler.complete_job(
                    session,
                    request.app.state.store,
                    request.app.state.settings,
                    job_id,
                    body.worker_id,
                    body.model_dump(),
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        return res

    @router.post("/jobs/{job_id}/fail")
    def fail_job(job_id: str, body: FailRequest, request: Request):
        with request.app.state.session_factory() as session:
            try:
                new_status = scheduler.fail_job(
                    session, request.app.state.settings, job_id, body.worker_id, body.reason
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, "status": new_status}

    return router
