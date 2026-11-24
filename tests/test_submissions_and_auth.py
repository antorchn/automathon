"""Tests de validation des zips, auth dev/OAuth, CSRF, limite IP et suppression de compte."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arene.app import create_app  # noqa: E402
from arene.config import Settings  # noqa: E402
from arene.zip_validator import ZipValidationError, validate_agent_zip  # noqa: E402


def _make_zip(entries: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _build_test_app(tmp_path: Path, **overrides):
    ref_dir = tmp_path / "refs"
    ref_dir.mkdir(parents=True, exist_ok=True)
    for name in ("rush_bot", "dummy_bot"):
        (ref_dir / f"{name}.zip").write_bytes(
            _make_zip({f"{name}/agent.py": "class ParticipantAgent:\n    def get_action(self, s): pass\n"})
        )
    defaults = dict(
        PUBLIC_URL="http://localhost:8001/arene",
        ARENE_BASE_PATH="/arene",
        DATABASE_URL=f"sqlite:///{(tmp_path / 'arene.db').as_posix()}",
        DATA_DIR=tmp_path / "data",
        ARENE_REF_AGENTS_DIR=ref_dir,
        ARENE_WEBGL_DIR=tmp_path / "webgl",
        SECRET_KEY="test-secret-key",
        WORKER_TOKEN="test-worker-token",
        AUTH_DEV_MODE=True,
        ADMIN_GITHUB_LOGINS="admin_user",
        ACCOUNTS_PER_IP_PER_DAY=3,
        COOLDOWN_MINUTES=0,
    )
    defaults.update(overrides)
    return create_app(Settings(**defaults))


def test_zip_validator_rules():
    ok = _make_zip({"my_bot/agent.py": "class ParticipantAgent: pass", "my_bot/utils.py": "x = 1"})
    name, sha = validate_agent_zip(ok, max_mb=50)
    assert name == "my_bot"
    assert len(sha) == 64

    # Mauvais zip / vide
    with pytest.raises(ZipValidationError):
        validate_agent_zip(b"not a zip")

    # Chemin absolu
    with pytest.raises(ZipValidationError):
        validate_agent_zip(_make_zip({"/etc/passwd": "x"}))

    # Traversée '..'
    with pytest.raises(ZipValidationError):
        validate_agent_zip(_make_zip({"my_bot/../../evil.py": "x", "my_bot/agent.py": "pass"}))

    # Deux dossiers à la racine
    with pytest.raises(ZipValidationError):
        validate_agent_zip(_make_zip({"a/agent.py": "pass", "b/other.py": "pass"}))

    # Pas d'agent.py
    with pytest.raises(ZipValidationError):
        validate_agent_zip(_make_zip({"my_bot/main.py": "pass"}))

    # Taille > limite
    with pytest.raises(ZipValidationError):
        validate_agent_zip(b"x" * (2 * 1024 * 1024), max_mb=1)


def test_auth_dev_mode_refuses_https(tmp_path: Path):
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(PUBLIC_URL="https://arene.example.org/arene", AUTH_DEV_MODE=True)


def test_oauth_rejects_mismatched_state(tmp_path: Path):
    app = _build_test_app(
        tmp_path,
        AUTH_DEV_MODE=False,
        GITHUB_CLIENT_ID="cid",
        GITHUB_CLIENT_SECRET="csec",
    )
    client = TestClient(app, follow_redirects=False)
    r = client.get("/arene/auth/github/login")
    assert r.status_code == 302
    loc = r.headers["location"]
    assert "github.com/login/oauth/authorize" in loc
    assert "scope=" not in loc

    # Callback avec mauvais state
    cb = client.get("/arene/auth/github/callback?code=abc&state=forged_state")
    assert cb.status_code == 400


def test_dev_login_csrf_submission_ip_limit_and_account_deletion(tmp_path: Path):
    app = _build_test_app(tmp_path, ACCOUNTS_PER_IP_PER_DAY=2)
    client = TestClient(app)

    # 1. Connexion compte 1 (alice)
    r = client.post("/arene/auth/dev/login", data={"login": "alice"})
    assert r.status_code == 200
    status_json = client.get("/arene/api/my-status").json()
    assert status_json["account"]["github_login"] == "alice"

    # Extraire CSRF depuis la page /submit
    html = client.get("/arene/submit").text
    assert 'name="csrf_token"' in html
    csrf = html.split('name="csrf_token" value="')[1].split('"')[0]

    # Envoi sans CSRF -> 403
    bad = client.post(
        "/arene/submit",
        data={"csrf_token": "wrong"},
        files={"agent_zip": ("bot.zip", _make_zip({"bot1/agent.py": "pass"}), "application/zip")},
    )
    assert bad.status_code == 403

    # Envoi valide -> 200
    good = client.post(
        "/arene/submit",
        data={"csrf_token": csrf},
        files={"agent_zip": ("bot.zip", _make_zip({"bot1/agent.py": "pass"}), "application/zip")},
    )
    assert good.status_code == 200
    assert "bot1" in good.text

    # Suppression du compte -> cookie effacé, données supprimées
    del_resp = client.post("/arene/account/delete", data={"csrf_token": csrf})
    assert del_resp.status_code == 200
    assert client.get("/arene/api/my-status").status_code == 401

    # Limite ACCOUNTS_PER_IP_PER_DAY = 2 : 2e compte passe, 3e bloqué (même après suppression d'alice)
    client2 = TestClient(app)
    assert client2.post("/arene/auth/dev/login", data={"login": "bob"}).status_code == 200
    client3 = TestClient(app)
    assert client3.post("/arene/auth/dev/login", data={"login": "charlie"}).status_code == 429

    # Avec TRUSTED_PROXY="127.0.0.1" (ou "testclient"), une nouvelle IP via X-Forwarded-For passe
    app_proxy = _build_test_app(tmp_path / "proxy", ACCOUNTS_PER_IP_PER_DAY=1, TRUSTED_PROXY="testclient")
    pc1 = TestClient(app_proxy)
    assert pc1.post("/arene/auth/dev/login", data={"login": "p1"}, headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 200
    pc2 = TestClient(app_proxy)
    assert pc2.post("/arene/auth/dev/login", data={"login": "p2"}, headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 429
    assert pc2.post("/arene/auth/dev/login", data={"login": "p3"}, headers={"X-Forwarded-For": "5.6.7.8"}).status_code == 200
