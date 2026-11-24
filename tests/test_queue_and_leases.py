"""Tests de la base, des migrations, de la file d'attente, des baux et de l'API worker."""

from __future__ import annotations

import io
import sys
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arene import scheduler  # noqa: E402
from arene.config import Settings  # noqa: E402
from arene.db import create_db_engine, create_session_factory, write_tx  # noqa: E402
from arene.migrations import apply_migrations  # noqa: E402
from arene.storage import LocalFileStore  # noqa: E402
from arene.worker_api import create_worker_router  # noqa: E402


def _make_agent_zip(folder: str = "my_bot", code: str = "pass") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{folder}/agent.py", f"class ParticipantAgent:\n    def get_action(self, state):\n        {code}\n")
    return buf.getvalue()


def _setup_env(tmp_path: Path, **overrides):
    db_path = tmp_path / "arene.db"
    data_dir = tmp_path / "data"
    ref_dir = tmp_path / "refs"
    ref_dir.mkdir(parents=True, exist_ok=True)
    for name in ("rush_bot", "dummy_bot"):
        (ref_dir / f"{name}.zip").write_bytes(_make_agent_zip(name))

    defaults = dict(
        DATABASE_URL=f"sqlite:///{db_path.as_posix()}",
        DATA_DIR=data_dir,
        ARENE_REF_AGENTS_DIR=ref_dir,
        WORKER_TOKEN="secret-worker-token",
        COOLDOWN_MINUTES=0,
        PLACEMENT_OPPONENTS=2,
        PLACEMENT_MAPS_PER_OPPONENT=1,
        BACKGROUND_ENABLED=False,
    )
    defaults.update(overrides)
    settings = Settings(**defaults)
    engine = create_db_engine(settings.database_url)
    apply_migrations(engine)
    sf = create_session_factory(engine)
    store = LocalFileStore(settings.data_dir)
    with sf() as session:
        scheduler.seed_reference_agents(session, store, settings)
    return settings, engine, sf, store


def _create_account(sf, github_id: int, login: str) -> str:
    acc_id = f"acc_{github_id}"
    with sf() as session:
        with write_tx(session):
            session.execute(
                text(
                    "INSERT INTO accounts (id, github_id, github_login, avatar_url, created_at) "
                    "VALUES (:id, :gid, :login, '', :now)"
                ),
                {"id": acc_id, "gid": github_id, "login": login, "now": datetime.now(timezone.utc).isoformat()},
            )
    return acc_id


def test_atomic_claim_never_double_claims_across_threads(tmp_path: Path):
    settings, engine, sf, store = _setup_env(tmp_path, MAX_PLACEMENTS_IN_PROGRESS=4)
    for i in range(1, 5):
        acc = _create_account(sf, 100 + i, f"user{i}")
        with sf() as s:
            scheduler.submit_agent(s, store, settings, acc, _make_agent_zip(f"bot{i}"), "127.0.0.1")

    claimed_ids: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker_fn(idx: int):
        barrier.wait()
        with sf() as s:
            job = scheduler.claim_job(s, settings, f"worker-{idx}")
        if job is not None:
            with lock:
                claimed_ids.append(job["id"])

    threads = [threading.Thread(target=worker_fn, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed_ids) == 4
    assert len(set(claimed_ids)) == 4


def test_lease_expiration_requeues_then_dead_letters_after_3_attempts(tmp_path: Path):
    settings, engine, sf, store = _setup_env(tmp_path, JOB_LEASE_SECONDS=60)
    acc = _create_account(sf, 200, "alice")
    with sf() as s:
        agent_id = scheduler.submit_agent(s, store, settings, acc, _make_agent_zip("alice_bot"), "127.0.0.1")

    past = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()

    for attempt in range(1, 4):
        with sf() as s:
            job = scheduler.claim_job(s, settings, f"w-{attempt}")
        assert job is not None
        assert job["type"] == "validation"
        # Simuler expiration du bail
        with sf() as s:
            with write_tx(s):
                s.execute(
                    text("UPDATE jobs SET lease_expires_at = :past WHERE id = :id"),
                    {"past": past, "id": job["id"]},
                )

    # 4e claim : le bail du 3e essai a expiré -> job marqué 'error' et agent 'rejected', donc aucun job dispo
    with sf() as s:
        assert scheduler.claim_job(s, settings, "w-4") is None
        jstatus = s.execute(text("SELECT status FROM jobs WHERE id = :id"), {"id": job["id"]}).scalar_one()
        astatus = s.execute(text("SELECT status FROM agents WHERE id = :id"), {"id": agent_id}).scalar_one()
    assert jstatus == "error"
    assert astatus == "rejected"


def test_validation_rejection_keeps_previous_ranked_agent_and_cooldown_queue(tmp_path: Path):
    settings, engine, sf, store = _setup_env(tmp_path, COOLDOWN_MINUTES=30, QUEUE_MAX=2)
    acc1 = _create_account(sf, 301, "bob")

    # 1. Créer un agent déjà classé pour bob
    with sf() as s:
        with write_tx(s):
            s.execute(
                text(
                    """
                    INSERT INTO agents (
                        id, account_id, name, zip_key, sha256, status, is_active,
                        elo, margin, provisional, submitted_at, updated_at
                    ) VALUES (
                        'ag_old', :acc, 'old_bot', 'agents/x.zip', 'abc', 'ranked', 1,
                        1050.0, 40.0, 0, :old_t, :old_t
                    )
                    """
                ),
                {"acc": acc1, "old_t": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()},
            )

    # 2. Soumettre un nouvel agent -> passe en validation ; un 2e envoi immédiat échoue (cooldown)
    with sf() as s:
        new_id = scheduler.submit_agent(s, store, settings, acc1, _make_agent_zip("new_bot"), "127.0.0.1")
    with sf() as s:
        try:
            scheduler.submit_agent(s, store, settings, acc1, _make_agent_zip("newer_bot"), "127.0.0.1")
            assert False, "Cooldown attendu"
        except scheduler.SubmissionRejectedError:
            pass

    # 3. Worker rejette le nouvel agent -> l'ancien reste classé et actif
    with sf() as s:
        job = scheduler.claim_job(s, settings, "w1")
        assert job is not None
        scheduler.complete_job(
            s, store, settings, job["id"], "w1",
            {"validation_ok": False, "validation_error": "SyntaxError", "games": []},
        )
        old_active = s.execute(text("SELECT is_active, status FROM agents WHERE id = 'ag_old'")).fetchone()
        new_status = s.execute(text("SELECT status, reject_reason FROM agents WHERE id = :id"), {"id": new_id}).fetchone()

    assert tuple(old_active) == (1, "ranked")
    assert new_status[0] == "rejected"
    assert "SyntaxError" in new_status[1]


def test_worker_api_endpoints_end_to_end(tmp_path: Path):
    settings, engine, sf, store = _setup_env(tmp_path)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = sf
    app.state.store = store
    app.include_router(create_worker_router(settings))

    acc = _create_account(sf, 401, "charlie")
    with sf() as s:
        ag_id = scheduler.submit_agent(s, store, settings, acc, _make_agent_zip("charlie_bot"), "127.0.0.1")

    client = TestClient(app)
    # Sans token -> 401
    assert client.post("/arene/api/worker/claim", json={"worker_id": "w1"}).status_code == 401

    headers = {"Authorization": "Bearer secret-worker-token"}
    r = client.post("/arene/api/worker/claim", json={"worker_id": "w1"}, headers=headers)
    assert r.status_code == 200
    job = r.json()
    assert job["type"] == "validation"

    # Téléchargement zip
    z_resp = client.get(job["agents"][ag_id]["zip_url"], headers=headers)
    assert z_resp.status_code == 200

    # Heartbeat
    hb = client.post(f"/arene/api/worker/jobs/{job['id']}/heartbeat", json={"worker_id": "w1"}, headers=headers)
    assert hb.status_code == 200

    # Résultat validation OK -> crée job placement 1
    res = client.post(
        f"/arene/api/worker/jobs/{job['id']}/result",
        json={
            "worker_id": "w1",
            "validation_ok": True,
            "games": [
                {
                    "index": g["index"],
                    "outcome": "left" if g["left_agent_id"] == ag_id else "right",
                    "end_frame": 200,
                    "left_health": 1000.0,
                    "right_health": 0.0,
                }
                for g in job["games"]
            ],
        },
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "placing"

    # Jouer les 2 confrontations de placement
    for step in range(2):
        r2 = client.post("/arene/api/worker/claim", json={"worker_id": "w1"}, headers=headers)
        assert r2.status_code == 200
        pjob = r2.json()
        assert pjob["type"] == "placement"
        for g in pjob["games"]:
            up = client.post(
                f"/arene/api/worker/jobs/{pjob['id']}/replays/{g['index']}",
                content=b"\x1f\x8bFAKEGZIP",
                headers=headers,
            )
            assert up.status_code == 200
        res2 = client.post(
            f"/arene/api/worker/jobs/{pjob['id']}/result",
            json={
                "worker_id": "w1",
                "games": [
                    {
                        "index": g["index"],
                        "outcome": "left" if g["left_agent_id"] == ag_id else "right",
                        "end_frame": 300,
                        "left_health": 800.0,
                        "right_health": 0.0,
                    }
                    for g in pjob["games"]
                ],
            },
            headers=headers,
        )
        assert res2.status_code == 200

    assert res2.json()["status"] == "ranked"
    with sf() as s:
        row = s.execute(
            text("SELECT status, is_active, elo, games_played FROM agents WHERE id = :id"),
            {"id": ag_id},
        ).fetchone()
    assert row[0] == "ranked"
    assert row[1] == 1
    assert row[2] > 1000.0
    assert row[3] == 4
