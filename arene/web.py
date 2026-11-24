"""Routes web (les 7 pages de la spec), API JSON publique, WebGL et replays gzip."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from arene import scheduler
from arene.auth import (
    clear_session_cookie,
    get_client_ip,
    read_session,
    verify_csrf,
)
from arene.config import Settings
from arene.zip_validator import ZipValidationError

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"


class UnityStaticFiles(StaticFiles):
    """Sert le build WebGL avec Content-Encoding: gzip et application/wasm."""

    TYPES = {
        ".wasm": "application/wasm",
        ".js": "application/javascript",
        ".data": "application/octet-stream",
    }

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        name = str(full_path)
        for suffix in (".unityweb", ".gz"):
            if name.endswith(suffix):
                response.headers["Content-Encoding"] = "gzip"
                inner = os.path.splitext(name[: -len(suffix)])[1]
                response.headers["Content-Type"] = self.TYPES.get(inner, "application/octet-stream")
                break
        return response


def _status_label(status: str, done: int = 0, total: int = 5) -> str:
    return {
        "queued": "En file",
        "validating": "Validation",
        "placing": f"Placement ({done}/{total})",
        "ranked": "Classé",
        "rejected": "Refusé",
        "replaced": "Remplacé",
        "disabled": "Désactivé",
    }.get(status, status)


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s} s"
    m, rem = divmod(s, 60)
    if m < 60:
        return f"{m} min {rem:02d} s"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


def _leaderboard_rows(session, settings: Settings) -> tuple[list[dict[str, Any]], str]:
    rows = session.execute(
        text(
            """
            SELECT a.id, a.name, a.elo, a.margin, a.provisional, a.games_played,
                   a.wins, a.draws, a.losses, a.is_reference, a.is_anchor,
                   a.updated_at, acc.github_login, acc.avatar_url
            FROM agents a
            JOIN accounts acc ON acc.id = a.account_id
            WHERE a.is_active = 1 AND a.status = 'ranked'
            ORDER BY a.elo DESC, a.games_played DESC, a.name ASC
            """
        )
    ).fetchall()

    entries: list[dict[str, Any]] = []
    latest_update = ""
    for idx, r in enumerate(rows, 1):
        upd = str(r[11] or "")
        if upd > latest_update:
            latest_update = upd
        entries.append({
            "rank": idx,
            "id": r[0],
            "name": r[1],
            "elo": round(float(r[2]), 1),
            "margin": round(float(r[3]), 1),
            "provisional": bool(r[4]),
            "games_played": int(r[5]),
            "wins": int(r[6]),
            "draws": int(r[7]),
            "losses": int(r[8]),
            "is_reference": bool(r[9]),
            "is_anchor": bool(r[10]),
            "updated_at": upd[:19].replace("T", " "),
            "github_login": r[12],
            "avatar_url": r[13],
        })
    return entries, latest_update[:19].replace("T", " ") if latest_update else "—"


def _agent_detail_data(session, settings: Settings, agent_id: str) -> dict[str, Any] | None:
    row = session.execute(
        text(
            """
            SELECT a.id, a.name, a.status, a.is_active, a.is_reference, a.is_anchor,
                   a.elo, a.margin, a.provisional, a.games_played, a.wins, a.draws, a.losses,
                   a.placement_done, a.placement_total, a.reject_reason, a.submitted_at,
                   a.ranked_at, a.updated_at, acc.github_login, acc.avatar_url
            FROM agents a
            JOIN accounts acc ON acc.id = a.account_id
            WHERE a.id = :id
            """
        ),
        {"id": agent_id},
    ).fetchone()
    if row is None:
        return None

    hist_rows = session.execute(
        text(
            """
            SELECT elo, margin, games_played, recorded_at
            FROM elo_history
            WHERE agent_id = :id
            ORDER BY id ASC
            LIMIT 200
            """
        ),
        {"id": agent_id},
    ).fetchall()
    history = [
        {"elo": round(float(h[0]), 1), "margin": round(float(h[1]), 1), "games": int(h[2]), "at": str(h[3])[:19]}
        for h in hist_rows
    ]

    match_rows = session.execute(
        text(
            """
            SELECT m.id, m.seed, m.left_agent_id, m.right_agent_id, m.outcome,
                   m.end_frame, m.left_health, m.right_health, m.left_mean_ms, m.right_mean_ms,
                   m.failure_side, m.failure_reason, m.replay_key, m.replay_deleted, m.created_at,
                   m.job_type, al.name, ar.name
            FROM matches m
            JOIN agents al ON al.id = m.left_agent_id
            JOIN agents ar ON ar.id = m.right_agent_id
            WHERE m.left_agent_id = :id OR m.right_agent_id = :id
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT 200
            """
        ),
        {"id": agent_id},
    ).fetchall()

    confrontations: dict[str, dict[str, Any]] = {}
    matches_list: list[dict[str, Any]] = []
    for m in match_rows:
        mid, seed, lid, rid, outcome, ef, lhp, rhp, lms, rms, fside, freason, rkey, rdel, cat, jtype, lname, rname = m
        is_left = lid == agent_id
        opp_id = rid if is_left else lid
        opp_name = rname if is_left else lname
        if outcome == "draw":
            pts = 0.5
            res_label = "Nul"
        elif (is_left and outcome == "left") or (not is_left and outcome == "right"):
            pts = 1.0
            res_label = "Victoire"
        else:
            pts = 0.0
            res_label = "Défaite"

        conf = confrontations.setdefault(
            opp_id,
            {"opponent_id": opp_id, "opponent_name": opp_name, "games": 0, "score": 0.0, "wins": 0, "draws": 0, "losses": 0},
        )
        conf["games"] += 1
        conf["score"] += pts
        if pts == 1.0:
            conf["wins"] += 1
        elif pts == 0.5:
            conf["draws"] += 1
        else:
            conf["losses"] += 1

        has_replay = bool(rkey) and not bool(rdel)
        matches_list.append({
            "id": mid,
            "seed": seed,
            "job_type": jtype,
            "left_id": lid,
            "right_id": rid,
            "left_name": lname,
            "right_name": rname,
            "side": "Gauche" if is_left else "Droite",
            "opponent_id": opp_id,
            "opponent_name": opp_name,
            "result_label": res_label,
            "outcome": outcome,
            "end_frame": ef,
            "left_health": round(float(lhp), 0),
            "right_health": round(float(rhp), 0),
            "mean_ms": round(float(lms if is_left else rms), 2),
            "failure_reason": freason,
            "has_replay": has_replay,
            "replay_url": settings.url(f"/replays/{mid}") if has_replay else "",
            "created_at": str(cat)[:19].replace("T", " "),
        })

    conf_list = sorted(confrontations.values(), key=lambda c: (-c["games"], c["opponent_name"]))
    return {
        "agent": {
            "id": row[0],
            "name": row[1],
            "status": row[2],
            "status_label": _status_label(row[2], int(row[13]), int(row[14])),
            "is_active": bool(row[3]),
            "is_reference": bool(row[4]),
            "is_anchor": bool(row[5]),
            "elo": round(float(row[6]), 1),
            "margin": round(float(row[7]), 1),
            "provisional": bool(row[8]),
            "games_played": int(row[9]),
            "wins": int(row[10]),
            "draws": int(row[11]),
            "losses": int(row[12]),
            "placement_done": int(row[13]),
            "placement_total": int(row[14]),
            "reject_reason": row[15],
            "submitted_at": str(row[16] or "")[:19].replace("T", " "),
            "ranked_at": str(row[17] or "")[:19].replace("T", " "),
            "updated_at": str(row[18] or "")[:19].replace("T", " "),
            "github_login": row[19],
            "avatar_url": row[20],
        },
        "history": history,
        "confrontations": conf_list,
        "matches": matches_list,
    }


def create_web_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix=settings.base_path)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    def _ctx(request: Request, user: dict | None, **extra: Any) -> dict[str, Any]:
        s: Settings = request.app.state.settings
        base = {
            "request": request,
            "settings": s,
            "base_path": s.base_path,
            "u": s.url,
            "user": user,
            "auth_dev_mode": s.auth_dev_mode,
        }
        base.update(extra)
        return base

    @router.get("/health")
    def health(request: Request):
        s: Settings = request.app.state.settings
        return {"status": "ok", "game_version": s.game_version}

    # --- 1. Classement ---
    @router.get("/", response_class=HTMLResponse)
    def page_leaderboard(request: Request):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            user = read_session(request, db, s)
            entries, updated_at = _leaderboard_rows(db, s)
            queue_count = db.execute(
                text("SELECT COUNT(*) FROM agents WHERE status IN ('queued', 'validating', 'placing')")
            ).scalar_one()
        return templates.TemplateResponse(
            request,
            "leaderboard.html",
            _ctx(request, user, entries=entries, updated_at=updated_at, queue_count=queue_count),
        )

    @router.get("/api/leaderboard")
    def api_leaderboard(request: Request):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            entries, updated_at = _leaderboard_rows(db, s)
        return {"game_version": s.game_version, "updated_at": updated_at, "entries": entries}

    # --- 2. Page Agent ---
    @router.get("/agents/{agent_id}", response_class=HTMLResponse)
    def page_agent(agent_id: str, request: Request):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            user = read_session(request, db, s)
            data = _agent_detail_data(db, s, agent_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Agent introuvable")
        return templates.TemplateResponse(request, "agent.html", _ctx(request, user, **data))

    @router.get("/api/agents/{agent_id}")
    def api_agent(agent_id: str, request: Request):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            data = _agent_detail_data(db, s, agent_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Agent introuvable")
        return data

    # --- 3. Envoyer (connecté) ---
    def _submit_context(db, s: Settings, user: dict | None, error: str = "", message: str = "") -> dict[str, Any]:
        submissions: list[dict[str, Any]] = []
        cooldown_s = 0
        if user:
            cooldown_s = scheduler.get_cooldown_remaining_seconds(db, user["account_id"], s)
            rows = db.execute(
                text(
                    """
                    SELECT id, name, status, placement_done, placement_total,
                           elo, margin, reject_reason, submitted_at, is_active
                    FROM agents
                    WHERE account_id = :acc AND is_reference = 0
                    ORDER BY submitted_at DESC
                    LIMIT 10
                    """
                ),
                {"acc": user["account_id"]},
            ).fetchall()
            for r in rows:
                pos, eta = scheduler.estimate_queue_wait(db, r[0]) if r[2] in ("queued", "validating", "placing") else (None, None)
                submissions.append({
                    "id": r[0],
                    "name": r[1],
                    "status": r[2],
                    "status_label": _status_label(r[2], int(r[3]), int(r[4])),
                    "placement_done": int(r[3]),
                    "placement_total": int(r[4]),
                    "elo": round(float(r[5]), 1),
                    "margin": round(float(r[6]), 1),
                    "reject_reason": r[7],
                    "submitted_at": str(r[8])[:19].replace("T", " "),
                    "is_active": bool(r[9]),
                    "queue_position": pos,
                    "eta_text": _format_duration(eta),
                })
        return {
            "submissions": submissions,
            "cooldown_s": cooldown_s,
            "cooldown_text": _format_duration(cooldown_s),
            "error": error,
            "message": message,
        }

    @router.get("/submit", response_class=HTMLResponse)
    def page_submit(request: Request):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            user = read_session(request, db, s)
            extra = _submit_context(db, s, user)
        return templates.TemplateResponse(request, "submit.html", _ctx(request, user, **extra))

    @router.post("/submit", response_class=HTMLResponse)
    async def post_submit(
        request: Request,
        csrf_token: str = Form(""),
        agent_zip: UploadFile = File(...),
    ):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            user = read_session(request, db, s)
            if user is None:
                raise HTTPException(status_code=401, detail="Connexion requise")
            verify_csrf(user, csrf_token)

            raw = await agent_zip.read()
            error = ""
            message = ""
            try:
                agent_id = scheduler.submit_agent(
                    db,
                    request.app.state.store,
                    s,
                    user["account_id"],
                    raw,
                    get_client_ip(request),
                )
                message = f"Agent envoyé ({agent_id}). Il va passer en validation."
            except (ZipValidationError, scheduler.SubmissionRejectedError) as exc:
                error = str(exc)

            extra = _submit_context(db, s, user, error=error, message=message)
        status_code = 400 if error else 200
        return templates.TemplateResponse(
            request,
            "submit.html",
            _ctx(request, user, **extra),
            status_code=status_code,
        )

    @router.get("/api/my-status")
    def api_my_status(request: Request):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            user = read_session(request, db, s)
            if user is None:
                raise HTTPException(status_code=401, detail="Non connecté")
            extra = _submit_context(db, s, user)
        return {
            "account": {"id": user["account_id"], "github_login": user["github_login"]},
            "cooldown_s": extra["cooldown_s"],
            "submissions": extra["submissions"],
        }

    # --- 4. Mon compte ---
    @router.get("/account", response_class=HTMLResponse)
    def page_account(request: Request):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            user = read_session(request, db, s)
        if user is None:
            return RedirectResponse(url=s.url("/submit"), status_code=302)
        return templates.TemplateResponse(request, "account.html", _ctx(request, user))

    @router.post("/account/delete")
    def delete_my_account(request: Request, csrf_token: str = Form("")):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            user = read_session(request, db, s)
            if user is None:
                raise HTTPException(status_code=401, detail="Connexion requise")
            verify_csrf(user, csrf_token)
            scheduler.delete_account(db, request.app.state.store, s, user["account_id"])
        resp = RedirectResponse(url=s.url("/"), status_code=303)
        clear_session_cookie(resp, s)
        return resp

    # --- 5. Replay ---
    @router.get("/replays/{match_id}.json")
    def get_replay_gzip(match_id: str, request: Request):
        with request.app.state.session_factory() as db:
            row = db.execute(
                text("SELECT replay_key, replay_deleted FROM matches WHERE id = :id"),
                {"id": match_id},
            ).fetchone()
        if row is None or row[1] or not row[0]:
            raise HTTPException(status_code=404, detail="Replay introuvable")
        try:
            gz_bytes = request.app.state.store.get_bytes(row[0])
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Fichier replay introuvable")
        return Response(
            content=gz_bytes,
            media_type="application/json",
            headers={
                "Content-Encoding": "gzip",
                "Cache-Control": "public, max-age=3600",
            },
        )

    @router.get("/replays/{match_id}", response_class=HTMLResponse)
    def page_replay(match_id: str, request: Request):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            user = read_session(request, db, s)
            row = db.execute(
                text(
                    """
                    SELECT m.id, m.seed, m.outcome, m.end_frame, m.left_health, m.right_health,
                           m.game_version, m.replay_key, m.replay_deleted, m.failure_reason,
                           al.id, al.name, ar.id, ar.name
                    FROM matches m
                    JOIN agents al ON al.id = m.left_agent_id
                    JOIN agents ar ON ar.id = m.right_agent_id
                    WHERE m.id = :id
                    """
                ),
                {"id": match_id},
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Partie introuvable")
        has_replay = bool(row[7]) and not bool(row[8])
        replay_json_url = s.url(f"/replays/{match_id}.json")
        webgl_available = (s.webgl_dir / "index.html").is_file()
        match_info = {
            "id": row[0],
            "seed": row[1],
            "outcome": row[2],
            "end_frame": row[3],
            "left_health": round(float(row[4]), 0),
            "right_health": round(float(row[5]), 0),
            "game_version": row[6],
            "has_replay": has_replay,
            "failure_reason": row[9],
            "left_id": row[10],
            "left_name": row[11],
            "right_id": row[12],
            "right_name": row[13],
            "replay_json_url": replay_json_url,
            "webgl_url": f"{s.url('/webgl/index.html')}?replay={replay_json_url}",
            "webgl_available": webgl_available,
        }
        return templates.TemplateResponse(
            request, "replay.html", _ctx(request, user, match=match_info)
        )

    # --- 6. Règles ---
    @router.get("/rules", response_class=HTMLResponse)
    def page_rules(request: Request):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            user = read_session(request, db, s)
        return templates.TemplateResponse(request, "rules.html", _ctx(request, user))

    # --- 7. Admin ---
    def _require_admin(request: Request, db, s: Settings) -> dict:
        user = read_session(request, db, s)
        if user is None or not user["is_admin"]:
            raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
        return user

    def _admin_context(db, s: Settings) -> dict:
        bg_enabled = scheduler.is_background_enabled(db, s)
        active_agents = db.execute(
            text(
                """
                SELECT a.id, a.name, a.status, a.elo, a.margin, a.is_reference, acc.github_login
                FROM agents a
                JOIN accounts acc ON acc.id = a.account_id
                WHERE a.is_active = 1 OR a.status IN ('queued', 'validating', 'placing')
                ORDER BY a.status, a.elo DESC
                """
            )
        ).fetchall()
        error_jobs = db.execute(
            text(
                """
                SELECT id, type, attempts, last_error, created_at, finished_at
                FROM jobs
                WHERE status = 'error'
                ORDER BY created_at DESC
                LIMIT 50
                """
            )
        ).fetchall()
        recent_jobs = db.execute(
            text(
                """
                SELECT id, type, status, attempts, lease_worker, duration_s, created_at
                FROM jobs
                ORDER BY created_at DESC
                LIMIT 30
                """
            )
        ).fetchall()
        return dict(bg_enabled=bg_enabled, active_agents=active_agents, error_jobs=error_jobs, recent_jobs=recent_jobs)

    @router.get("/admin", response_class=HTMLResponse)
    def page_admin(request: Request):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            user = _require_admin(request, db, s)
            extra = _admin_context(db, s)
        return templates.TemplateResponse(request, "admin.html", _ctx(request, user, **extra))

    @router.post("/admin/import-agents", response_class=HTMLResponse)
    async def admin_import_agents(
        request: Request,
        csrf_token: str = Form(""),
        display_name: str = Form(""),
        agent_zips: list[UploadFile] = File(...),
    ):
        """Ajoute des agents invités (sans compte GitHub) : un compte invité par nom de dossier de zip."""
        s: Settings = request.app.state.settings
        import_results = []
        with request.app.state.session_factory() as db:
            user = _require_admin(request, db, s)
            verify_csrf(user, csrf_token)
            single = len(agent_zips) == 1
            for upload in agent_zips:
                raw = await upload.read()
                try:
                    agent_id = scheduler.import_guest_agent(
                        db,
                        request.app.state.store,
                        s,
                        raw,
                        display_name if single else None,
                        get_client_ip(request),
                    )
                    import_results.append({"file": upload.filename, "ok": True, "detail": agent_id})
                except (ZipValidationError, scheduler.SubmissionRejectedError) as exc:
                    import_results.append({"file": upload.filename, "ok": False, "detail": str(exc)})
            extra = _admin_context(db, s)
        status_code = 200 if all(r["ok"] for r in import_results) else 400
        return templates.TemplateResponse(
            request,
            "admin.html",
            _ctx(request, user, import_results=import_results, **extra),
            status_code=status_code,
        )

    @router.post("/admin/disable-agent")
    def admin_disable_agent(request: Request, agent_id: str = Form(...), csrf_token: str = Form("")):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            user = _require_admin(request, db, s)
            verify_csrf(user, csrf_token)
            scheduler.disable_agent(db, s, agent_id)
        return RedirectResponse(url=s.url("/admin"), status_code=303)

    @router.post("/admin/clear-queue")
    def admin_clear_queue(request: Request, csrf_token: str = Form("")):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            user = _require_admin(request, db, s)
            verify_csrf(user, csrf_token)
            scheduler.clear_queue(db)
        return RedirectResponse(url=s.url("/admin"), status_code=303)

    @router.post("/admin/toggle-background")
    def admin_toggle_background(request: Request, csrf_token: str = Form("")):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            user = _require_admin(request, db, s)
            verify_csrf(user, csrf_token)
            cur = scheduler.is_background_enabled(db, s)
            scheduler.set_runtime_flag(db, "background_enabled", "false" if cur else "true")
        return RedirectResponse(url=s.url("/admin"), status_code=303)

    @router.post("/admin/retry-job")
    def admin_retry_job(request: Request, job_id: str = Form(...), csrf_token: str = Form("")):
        s: Settings = request.app.state.settings
        with request.app.state.session_factory() as db:
            user = _require_admin(request, db, s)
            verify_csrf(user, csrf_token)
            scheduler.retry_error_job(db, s, job_id)
        return RedirectResponse(url=s.url("/admin"), status_code=303)

    return router
