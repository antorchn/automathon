"""Authentification GitHub OAuth (sans scope), mode dev, cookies signés et CSRF."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.orm import Session

from arene.config import Settings
from arene.db import write_tx

SESSION_COOKIE = "arene_session"
OAUTH_STATE_COOKIE = "arene_oauth_state"
SESSION_MAX_AGE_S = 7 * 24 * 3600


class AccountCreationLimitError(ValueError):
    """Limite de création de comptes par IP et par jour dépassée."""


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="arene-auth-v1")


def get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def create_session_cookie(response: Response, settings: Settings, account_id: str, github_login: str, avatar_url: str) -> str:
    csrf = secrets.token_urlsafe(24)
    token = _serializer(settings).dumps({
        "account_id": account_id,
        "github_login": github_login,
        "avatar_url": avatar_url,
        "csrf": csrf,
    })
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_MAX_AGE_S,
        path=settings.cookie_path,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return csrf


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(key=SESSION_COOKIE, path=settings.cookie_path)


def read_session(request: Request, session: Session, settings: Settings) -> dict | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        data = _serializer(settings).loads(raw, max_age=SESSION_MAX_AGE_S)
    except (BadSignature, SignatureExpired):
        return None
    account_id = data.get("account_id")
    if not account_id:
        return None
    row = session.execute(
        text("SELECT id, github_id, github_login, avatar_url, created_at FROM accounts WHERE id = :id AND is_system = 0"),
        {"id": account_id},
    ).fetchone()
    if row is None:
        return None
    return {
        "account_id": row[0],
        "github_id": row[1],
        "github_login": row[2],
        "avatar_url": row[3],
        "created_at": row[4],
        "csrf": data.get("csrf", ""),
        "is_admin": row[2].lower() in settings.admin_logins_set,
    }


def verify_csrf(session_data: dict | None, submitted_token: str | None) -> None:
    if not session_data or not submitted_token:
        raise HTTPException(status_code=403, detail="Jeton CSRF manquant")
    expected = session_data.get("csrf", "")
    if not expected or not hmac.compare_digest(expected, submitted_token):
        raise HTTPException(status_code=403, detail="Jeton CSRF invalide")


def _upsert_account(
    db: Session,
    settings: Settings,
    *,
    github_id: int,
    github_login: str,
    avatar_url: str,
    ip_address: str,
) -> tuple[str, str, str]:
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    with write_tx(db):
        existing = db.execute(
            text("SELECT id FROM accounts WHERE github_id = :gid"),
            {"gid": github_id},
        ).fetchone()
        if existing is not None:
            acc_id = existing[0]
            db.execute(
                text("UPDATE accounts SET github_login = :login, avatar_url = :av WHERE id = :id"),
                {"login": github_login, "av": avatar_url, "id": acc_id},
            )
            return acc_id, github_login, avatar_url

        # Garde-fou ACCOUNTS_PER_IP_PER_DAY pour un nouveau compte
        if settings.accounts_per_ip_per_day > 0:
            day_ago = (now_dt - timedelta(days=1)).isoformat()
            cnt = db.execute(
                text(
                    """
                    SELECT COUNT(*) FROM ip_events
                    WHERE ip_address = :ip AND event_type = 'account_create' AND created_at >= :since
                    """
                ),
                {"ip": ip_address, "since": day_ago},
            ).scalar_one()
            if cnt >= settings.accounts_per_ip_per_day:
                raise AccountCreationLimitError(
                    "Trop de comptes créés depuis cette adresse IP aujourd'hui. Réessayez demain."
                )

        acc_id = f"acc_{uuid.uuid4().hex[:16]}"
        db.execute(
            text(
                """
                INSERT INTO accounts (id, github_id, github_login, avatar_url, created_at, is_system)
                VALUES (:id, :gid, :login, :av, :now, 0)
                """
            ),
            {"id": acc_id, "gid": github_id, "login": github_login, "av": avatar_url, "now": now},
        )
        db.execute(
            text(
                """
                INSERT INTO ip_events (ip_address, event_type, account_id, created_at)
                VALUES (:ip, 'account_create', :acc, :now)
                """
            ),
            {"ip": ip_address, "acc": acc_id, "now": now},
        )
        return acc_id, github_login, avatar_url


def create_auth_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix=settings.url("/auth"))

    @router.get("/github/login")
    def github_login(request: Request):
        s: Settings = request.app.state.settings
        if not s.github_client_id:
            if s.auth_dev_mode:
                return RedirectResponse(url=s.url("/submit"), status_code=302)
            raise HTTPException(status_code=503, detail="GITHUB_CLIENT_ID non configuré.")

        state = secrets.token_urlsafe(24)
        callback_url = f"{s.public_url}/auth/github/callback"
        params = urlencode({
            "client_id": s.github_client_id,
            "redirect_uri": callback_url,
            "state": state,
        })
        # Aucune permission demandée (pas de scope) : profil public uniquement
        resp = RedirectResponse(url=f"https://github.com/login/oauth/authorize?{params}", status_code=302)
        signed_state = _serializer(s).dumps({"state": state})
        resp.set_cookie(
            key=OAUTH_STATE_COOKIE,
            value=signed_state,
            max_age=600,
            path=s.cookie_path,
            httponly=True,
            secure=s.cookie_secure,
            samesite="lax",
        )
        return resp

    @router.get("/github/callback")
    def github_callback(request: Request, code: str = "", state: str = ""):
        s: Settings = request.app.state.settings
        raw_state = request.cookies.get(OAUTH_STATE_COOKIE)
        if not raw_state or not code or not state:
            raise HTTPException(status_code=400, detail="Paramètres OAuth manquants")
        try:
            state_data = _serializer(s).loads(raw_state, max_age=600)
        except (BadSignature, SignatureExpired) as exc:
            raise HTTPException(status_code=400, detail="État OAuth expiré ou invalide") from exc
        if not hmac.compare_digest(str(state_data.get("state", "")), state):
            raise HTTPException(status_code=400, detail="État OAuth incohérent (CSRF bloqué)")

        callback_url = f"{s.public_url}/auth/github/callback"
        with httpx.Client(timeout=15.0) as client:
            tok_resp = client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": s.github_client_id,
                    "client_secret": s.github_client_secret,
                    "code": code,
                    "redirect_uri": callback_url,
                },
            )
            tok_resp.raise_for_status()
            access_token = tok_resp.json().get("access_token")
            if not access_token:
                raise HTTPException(status_code=400, detail="Échec d'échange du code GitHub")

            user_resp = client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            user_resp.raise_for_status()
            profile = user_resp.json()

        github_id = int(profile["id"])
        github_login = str(profile["login"])
        avatar_url = str(profile.get("avatar_url") or "")

        with request.app.state.session_factory() as db:
            try:
                acc_id, login, av = _upsert_account(
                    db, s,
                    github_id=github_id,
                    github_login=github_login,
                    avatar_url=avatar_url,
                    ip_address=get_client_ip(request),
                )
            except AccountCreationLimitError as exc:
                raise HTTPException(status_code=429, detail=str(exc)) from exc

        resp = RedirectResponse(url=s.url("/submit"), status_code=302)
        resp.delete_cookie(key=OAUTH_STATE_COOKIE, path=s.cookie_path)
        create_session_cookie(resp, s, acc_id, login, av)
        return resp

    @router.post("/dev/login")
    def dev_login(
        request: Request,
        login: str = Form(...),
        github_id: int | None = Form(default=None),
    ):
        s: Settings = request.app.state.settings
        if not s.auth_dev_mode:
            raise HTTPException(status_code=404, detail="Indisponible hors AUTH_DEV_MODE")
        clean_login = login.strip()
        if not clean_login:
            raise HTTPException(status_code=400, detail="Login requis")
        gid = github_id if github_id is not None else (10_000_000 + int(hashlib.sha256(clean_login.encode()).hexdigest()[:7], 16))
        with request.app.state.session_factory() as db:
            try:
                acc_id, glogin, av = _upsert_account(
                    db, s,
                    github_id=gid,
                    github_login=clean_login,
                    avatar_url="",
                    ip_address=get_client_ip(request),
                )
            except AccountCreationLimitError as exc:
                raise HTTPException(status_code=429, detail=str(exc)) from exc

        resp = RedirectResponse(url=s.url("/submit"), status_code=303)
        create_session_cookie(resp, s, acc_id, glogin, av)
        return resp

    @router.post("/logout")
    def logout(request: Request):
        s: Settings = request.app.state.settings
        resp = RedirectResponse(url=s.url("/"), status_code=303)
        clear_session_cookie(resp, s)
        return resp

    return router
