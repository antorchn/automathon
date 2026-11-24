"""Tests de l'intégration derrière le site de l'association : sous-chemin, proxys de confiance, adresse du worker."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arene.app import create_app  # noqa: E402
from arene.config import Settings  # noqa: E402
from arene.middleware import TrustedProxies, client_from_forwarded_for  # noqa: E402
from worker.config import WorkerConfig  # noqa: E402

AGENT_PY = "class ParticipantAgent:\n    def get_action(self, s): pass\n"


def _make_zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _app(tmp_path: Path, **overrides):
    refs = tmp_path / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    for name in ("rush_bot", "dummy_bot"):
        (refs / f"{name}.zip").write_bytes(_make_zip({f"{name}/agent.py": AGENT_PY}))
    values = dict(
        PUBLIC_URL="http://localhost:8001/automathon-rlcs",
        ARENE_BASE_PATH="/automathon-rlcs",
        DATABASE_URL=f"sqlite:///{(tmp_path / 'arene.db').as_posix()}",
        DATA_DIR=str(tmp_path / "data"),
        ARENE_REF_AGENTS_DIR=str(refs),
        ARENE_WEBGL_DIR=str(tmp_path / "webgl"),
        SECRET_KEY="k",
        WORKER_TOKEN="t",
        AUTH_DEV_MODE="true",
    )
    values.update(overrides)
    return create_app(Settings(**values))


def test_forwarded_for_keeps_the_real_visitor_and_ignores_spoofing():
    trusted = TrustedProxies({"127.0.0.1", "172.16.0.0/12", "10.0.0.2"})
    assert "172.18.0.1" in trusted and "10.0.0.2" in trusted and "8.8.8.8" not in trusted
    # Visiteur 1.2.3.4 -> proxy du site 10.0.0.2 -> Caddy
    assert client_from_forwarded_for("1.2.3.4, 10.0.0.2", trusted) == "1.2.3.4"
    # Un visiteur qui écrit lui-même un faux X-Forwarded-For ne remplace pas sa vraie adresse
    assert client_from_forwarded_for("6.6.6.6, 1.2.3.4, 10.0.0.2", trusted) == "1.2.3.4"
    assert client_from_forwarded_for("1.2.3.4", trusted) == "1.2.3.4"
    assert client_from_forwarded_for("10.0.0.2", trusted) == "10.0.0.2"


def test_public_url_must_end_with_the_base_path():
    with pytest.raises(ValueError, match="ARENE_BASE_PATH"):
        Settings(PUBLIC_URL="https://automatants.cs-campus.fr/automathon-rlcs", ARENE_BASE_PATH="/arene")
    ok = Settings(PUBLIC_URL="https://automatants.cs-campus.fr/automathon-rlcs/", ARENE_BASE_PATH="/automathon-rlcs")
    assert ok.cookie_secure and ok.cookie_path == "/automathon-rlcs"
    sub = Settings(PUBLIC_URL="https://arene.exemple.fr", ARENE_BASE_PATH="/")
    assert sub.base_path == "" and sub.cookie_path == "/"


def test_site_runs_under_the_association_sub_path_and_records_the_visitor_ip(tmp_path: Path):
    app = _app(tmp_path, TRUSTED_PROXY="testclient,10.0.0.2")
    client = TestClient(app)
    assert client.get("/automathon-rlcs/health").json()["status"] == "ok"
    page = client.get("/automathon-rlcs/")
    assert page.status_code == 200
    assert "/automathon-rlcs/static/style.css" in page.text
    assert client.get("/automathon-rlcs/static/fonts/Poppins-400-latin.woff2").status_code == 200

    headers = {"X-Forwarded-For": "6.6.6.6, 1.2.3.4, 10.0.0.2", "X-Forwarded-Proto": "https"}
    assert client.post("/automathon-rlcs/auth/dev/login", data={"login": "alice"}, headers=headers).status_code == 200
    with app.state.session_factory() as db:
        ips = [r[0] for r in db.execute(text("SELECT ip_address FROM ip_events")).fetchall()]
    assert ips == ["1.2.3.4"]


def test_worker_site_url_follows_the_base_path(monkeypatch):
    monkeypatch.setenv("WORKER_TOKEN", "t")
    monkeypatch.delenv("SITE_URL", raising=False)
    monkeypatch.setenv("ARENE_BASE_PATH", "/automathon-rlcs")
    assert WorkerConfig.from_env().site_url == "http://arene-web:8000/automathon-rlcs"
    monkeypatch.setenv("ARENE_BASE_PATH", "/")
    assert WorkerConfig.from_env().site_url == "http://arene-web:8000"
    monkeypatch.setenv("SITE_URL", "https://automatants.cs-campus.fr/automathon-rlcs/")
    assert WorkerConfig.from_env().site_url == "https://automatants.cs-campus.fr/automathon-rlcs"
