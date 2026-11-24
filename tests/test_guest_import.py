"""Tests de l'import d'agents invités par un administrateur (page Admin et commande arene.import_agents)."""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arene import import_agents  # noqa: E402
from arene.app import create_app  # noqa: E402
from arene.config import Settings  # noqa: E402

AGENT_PY = "class ParticipantAgent:\n    def get_action(self, s): pass\n"


def _make_zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _settings(tmp_path: Path, **overrides) -> dict:
    ref_dir = tmp_path / "refs"
    ref_dir.mkdir(parents=True, exist_ok=True)
    for name in ("rush_bot", "dummy_bot"):
        (ref_dir / f"{name}.zip").write_bytes(_make_zip({f"{name}/agent.py": AGENT_PY}))
    values = dict(
        PUBLIC_URL="http://localhost:8001/arene",
        ARENE_BASE_PATH="/arene",
        DATABASE_URL=f"sqlite:///{(tmp_path / 'arene.db').as_posix()}",
        DATA_DIR=str(tmp_path / "data"),
        ARENE_REF_AGENTS_DIR=str(ref_dir),
        ARENE_WEBGL_DIR=str(tmp_path / "webgl"),
        SECRET_KEY="test-secret-key",
        WORKER_TOKEN="test-worker-token",
        AUTH_DEV_MODE="true",
        ADMIN_GITHUB_LOGINS="admin_user",
        COOLDOWN_MINUTES="30",
        QUEUE_MAX="1",
    )
    values.update(overrides)
    return values


def _csrf(client: TestClient, path: str) -> str:
    html = client.get(path).text
    return html.split('name="csrf_token" value="')[1].split('"')[0]


def _rows(app, sql: str, **params):
    with app.state.session_factory() as db:
        return db.execute(text(sql), params).fetchall()


def test_admin_page_imports_guest_agents_without_limits(tmp_path: Path):
    app = create_app(Settings(**_settings(tmp_path)))

    # Un participant ordinaire ne peut pas importer
    user = TestClient(app)
    assert user.post("/arene/auth/dev/login", data={"login": "alice"}).status_code == 200
    forbidden = user.post(
        "/arene/admin/import-agents",
        data={"csrf_token": "x"},
        files=[("agent_zips", ("a.zip", _make_zip({"team_a/agent.py": AGENT_PY}), "application/zip"))],
    )
    assert forbidden.status_code == 403

    admin = TestClient(app)
    assert admin.post("/arene/auth/dev/login", data={"login": "admin_user"}).status_code == 200
    csrf = _csrf(admin, "/arene/admin")

    # Trois zips d'un coup, avec QUEUE_MAX=1 : l'import ignore la limite de file ; un zip invalide est refusé seul
    files = [
        ("agent_zips", ("team_a.zip", _make_zip({"team_a/agent.py": AGENT_PY}), "application/zip")),
        ("agent_zips", ("team_b.zip", _make_zip({"team_b/agent.py": AGENT_PY}), "application/zip")),
        ("agent_zips", ("team_c.zip", _make_zip({"team_c/agent.py": AGENT_PY}), "application/zip")),
        ("agent_zips", ("bad.zip", _make_zip({"agent.py": AGENT_PY}), "application/zip")),
    ]
    r = admin.post("/arene/admin/import-agents", data={"csrf_token": csrf}, files=files)
    assert r.status_code == 400  # au moins un refus
    assert r.text.count("importé (") == 3
    assert "bad.zip" in r.text and "refusé" in r.text

    guests = _rows(app, "SELECT id, github_login, is_system, github_id FROM accounts WHERE id LIKE 'acc_guest_%' ORDER BY id")
    assert [g[1] for g in guests] == ["team_a", "team_b", "team_c"]
    assert all(g[2] == 1 and g[3] < 0 for g in guests)
    assert len(_rows(app, "SELECT id FROM agents WHERE account_id LIKE 'acc_guest_%'")) == 3

    # Un seul zip avec un nom affiché ; réimport immédiat malgré le cooldown de 30 min : l'envoi en file est remplacé
    one = [("agent_zips", ("team_a.zip", _make_zip({"team_a/agent.py": AGENT_PY + "# v2\n"}), "application/zip"))]
    r2 = admin.post("/arene/admin/import-agents", data={"csrf_token": csrf, "display_name": "Équipe A"}, files=one)
    assert r2.status_code == 200
    assert _rows(app, "SELECT github_login FROM accounts WHERE id = 'acc_guest_team_a'")[0][0] == "Équipe A"
    statuses = sorted(r[0] for r in _rows(app, "SELECT status FROM agents WHERE account_id = 'acc_guest_team_a'"))
    assert statuses.count("replaced") >= 1 or statuses.count("validating") >= 1

    # Personne ne peut se connecter sur un compte invité : la connexion « team_a » crée un compte distinct
    other = TestClient(app)
    assert other.post("/arene/auth/dev/login", data={"login": "team_a"}).status_code == 200
    assert _rows(app, "SELECT COUNT(*) FROM accounts WHERE github_login = 'team_a' AND is_system = 0")[0][0] == 1
    assert _rows(app, "SELECT github_login FROM accounts WHERE id = 'acc_guest_team_a'")[0][0] == "Équipe A"


def test_cli_imports_a_folder_with_team_names(tmp_path: Path, monkeypatch):
    values = _settings(tmp_path)
    create_app(Settings(**values))  # migrations et bots de référence
    for key, value in values.items():
        monkeypatch.setenv(key, str(value))

    folder = tmp_path / "import"
    folder.mkdir()
    (folder / "crous.zip").write_bytes(_make_zip({"crous/agent.py": AGENT_PY}))
    (folder / "ftl.zip").write_bytes(_make_zip({"ftl/agent.py": AGENT_PY}))
    (folder / "broken.zip").write_bytes(b"not a zip")
    (folder / "equipes.json").write_text(
        json.dumps([{"zip": "crous.zip", "team": "1600 Crous ez"}, {"zip": "ftl.zip", "team": "FTL"}]),
        encoding="utf-8",
    )

    assert import_agents.main([str(folder)]) == 1  # broken.zip refusé, les deux autres importés

    app = create_app(Settings(**values))
    names = dict(_rows(app, "SELECT id, github_login FROM accounts WHERE id LIKE 'acc_guest_%'"))
    assert names == {"acc_guest_crous": "1600 Crous ez", "acc_guest_ftl": "FTL"}
    assert len(_rows(app, "SELECT id FROM agents WHERE account_id LIKE 'acc_guest_%'")) == 2
