"""Tests des 7 pages du site, du sous-chemin ARENE_BASE_PATH, WebGL, replays gzip et Admin."""

from __future__ import annotations

import gzip
import io
import json
import sys
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arene.app import create_app  # noqa: E402
from arene.config import Settings  # noqa: E402


def _make_zip(folder: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{folder}/agent.py", "class ParticipantAgent:\n    def get_action(self, s): pass\n")
    return buf.getvalue()


def _make_app(tmp_path: Path, base_path: str = "/arene"):
    ref_dir = tmp_path / "refs"
    ref_dir.mkdir(exist_ok=True)
    for name in ("rush_bot", "dummy_bot"):
        (ref_dir / f"{name}.zip").write_bytes(_make_zip(name))

    webgl_dir = tmp_path / "webgl"
    (webgl_dir / "Build").mkdir(parents=True, exist_ok=True)
    (webgl_dir / "index.html").write_text("<html><body>WebGL Mock</body></html>", encoding="utf-8")
    (webgl_dir / "Build" / "game.wasm.unityweb").write_bytes(gzip.compress(b"WASM"))

    public_url = f"http://localhost:8001{base_path}" if base_path != "/" else "http://localhost:8001"
    return create_app(
        Settings(
            PUBLIC_URL=public_url,
            ARENE_BASE_PATH=base_path,
            DATABASE_URL=f"sqlite:///{(tmp_path / 'arene.db').as_posix()}",
            DATA_DIR=tmp_path / "data",
            ARENE_REF_AGENTS_DIR=ref_dir,
            ARENE_WEBGL_DIR=webgl_dir,
            SECRET_KEY="secret-k",
            WORKER_TOKEN="w-tok",
            AUTH_DEV_MODE=True,
            ADMIN_GITHUB_LOGINS="admin_user",
            COOLDOWN_MINUTES=0,
            PLACEMENT_OPPONENTS=1,
            PLACEMENT_MAPS_PER_OPPONENT=1,
            BACKGROUND_ENABLED=False,
        )
    )


def test_all_seven_pages_webgl_headers_and_base_path(tmp_path: Path):
    app = _make_app(tmp_path, base_path="/arene")
    client = TestClient(app)

    # 1. Classement
    r = client.get("/arene/")
    assert r.status_code == 200
    assert "rush_bot" in r.text
    assert "1000.0" in r.text

    # 2. Règles
    rules = client.get("/arene/rules")
    assert rules.status_code == 200
    assert "antorchn/automathon" in rules.text

    # 3. Connexion admin + soumission + cycle worker complet -> replay
    client.post("/arene/auth/dev/login", data={"login": "admin_user"})
    submit_html = client.get("/arene/submit").text
    csrf = submit_html.split('name="csrf_token" value="')[1].split('"')[0]

    sub = client.post(
        "/arene/submit",
        data={"csrf_token": csrf},
        files={"agent_zip": ("alpha.zip", _make_zip("alpha_bot"), "application/zip")},
    )
    assert sub.status_code == 200

    # Worker traite validation + placement avec upload de replay gzip
    wh = {"Authorization": "Bearer w-tok"}
    vjob = client.post("/arene/api/worker/claim", json={"worker_id": "w1"}, headers=wh).json()
    client.post(
        f"/arene/api/worker/jobs/{vjob['id']}/result",
        json={
            "worker_id": "w1",
            "validation_ok": True,
            "games": [
                {"index": g["index"], "outcome": "left", "end_frame": 100, "left_health": 1000, "right_health": 0}
                for g in vjob["games"]
            ],
        },
        headers=wh,
    )

    pjob = client.post("/arene/api/worker/claim", json={"worker_id": "w1"}, headers=wh).json()
    replay_payload = gzip.compress(json.dumps({"Version": 1, "Seed": 123, "Steps": []}).encode("utf-8"))
    for g in pjob["games"]:
        client.post(f"/arene/api/worker/jobs/{pjob['id']}/replays/{g['index']}", content=replay_payload, headers=wh)
    client.post(
        f"/arene/api/worker/jobs/{pjob['id']}/result",
        json={
            "worker_id": "w1",
            "games": [
                {"index": g["index"], "outcome": "left", "end_frame": 150, "left_health": 900, "right_health": 0}
                for g in pjob["games"]
            ],
        },
        headers=wh,
    )

    # Vérifier le classement JSON + page Agent
    lb = client.get("/arene/api/leaderboard").json()
    alpha = next(e for e in lb["entries"] if e["name"] == "alpha_bot")
    ag_page = client.get(f"/arene/agents/{alpha['id']}")
    assert ag_page.status_code == 200
    assert "alpha_bot" in ag_page.text

    ag_json = client.get(f"/arene/api/agents/{alpha['id']}").json()
    match_id = ag_json["matches"][0]["id"]

    # Page Replay + fichier .json gzippé + build WebGL
    rep_page = client.get(f"/arene/replays/{match_id}")
    assert rep_page.status_code == 200
    assert "/arene/webgl/index.html?replay=" in rep_page.text

    rep_json = client.get(f"/arene/replays/{match_id}.json")
    assert rep_json.status_code == 200
    assert rep_json.headers.get("content-encoding") == "gzip"
    assert rep_json.json()["Seed"] == 123

    wasm = client.get("/arene/webgl/Build/game.wasm.unityweb")
    assert wasm.status_code == 200
    assert wasm.headers.get("content-encoding") == "gzip"
    assert wasm.headers.get("content-type") == "application/wasm"

    # Page Mon compte & Page Admin
    assert client.get("/arene/account").status_code == 200
    adm = client.get("/arene/admin")
    assert adm.status_code == 200
    assert "alpha_bot" in adm.text

    # Non-admin bloqué sur /admin
    non_admin = TestClient(app)
    non_admin.post("/arene/auth/dev/login", data={"login": "regular_user"})
    assert non_admin.get("/arene/admin").status_code == 403


def test_subdomain_root_base_path(tmp_path: Path):
    app = _make_app(tmp_path, base_path="/")
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/submit"' in r.text
    assert client.get("/health").json()["status"] == "ok"
