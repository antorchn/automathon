"""Tests d'intégration du worker, de l'arbitre, de la sandbox et des agents hostiles."""

from __future__ import annotations

import gzip
import io
import json
import sys
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
import zmq
from fastapi import FastAPI
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
KIT_PYTHON = ROOT / "PythonAI"
for p in (str(ROOT), str(KIT_PYTHON)):
    if p not in sys.path:
        sys.path.insert(0, p)

from arene import scheduler  # noqa: E402
from arene.config import Settings  # noqa: E402
from arene.db import create_db_engine, create_session_factory, write_tx  # noqa: E402
from arene.migrations import apply_migrations  # noqa: E402
from arene.storage import LocalFileStore  # noqa: E402
from arene.worker_api import create_worker_router  # noqa: E402
from bridge.datatypes import AIMessage, FixedVector2, GameState, ShipState  # noqa: E402
from bridge.gym import Gym  # noqa: E402
from worker.api_client import WorkerAPIClient  # noqa: E402
from worker.config import WorkerConfig  # noqa: E402
from worker.main import WorkerDaemon  # noqa: E402
from worker.referee import MatchLimits, play_match  # noqa: E402
from worker.sandbox import SubprocessAgentProcess, parse_cpu_stat_usec  # noqa: E402


class FakeHeadlessServer:
    """Serveur ZeroMQ REP minimal simulant le Headless pour tester l'arbitre sans binaire externe."""

    def __init__(self, max_steps_before_win: int = 4, outcome: str = "Win"):
        self.ctx = zmq.Context()
        self.sock = self.ctx.socket(zmq.REP)
        self.port = self.sock.bind_to_random_port("tcp://127.0.0.1")
        self.max_steps = max_steps_before_win
        self.outcome = outcome
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _make_state(self, frame: int, done: bool, outcome: str) -> str:
        l_hp = 1000.0 if outcome != "Loss" else 0.0
        r_hp = 0.0 if outcome == "Win" else 1000.0
        st = GameState(
            SelfShip=ShipState(
                Position=FixedVector2(-4.0, 0.0),
                Team="Green",
                Width=1.0,
                Height=1.0,
                Velocity=FixedVector2(0.0, 0.0),
                Health=l_hp,
                ShieldCooldownFramesLeft=0,
                MissileCooldownFramesLeft=0,
                MachineGunCooldownFramesLeft=0,
                DashCooldownFramesLeft=0,
            ),
            EnemyShip=ShipState(
                Position=FixedVector2(4.0, 0.0),
                Team="Red",
                Width=1.0,
                Height=1.0,
                Velocity=FixedVector2(0.0, 0.0),
                Health=r_hp,
                ShieldCooldownFramesLeft=0,
                MissileCooldownFramesLeft=0,
                MachineGunCooldownFramesLeft=0,
                DashCooldownFramesLeft=0,
            ),
            Done=done,
            Outcome=outcome if done else "Ongoing",
            Frame=frame,
        )
        return st.model_dump_json()

    def _serve(self):
        frame = 0
        steps = 0
        poller = zmq.Poller()
        poller.register(self.sock, zmq.POLLIN)
        while not self._stop:
            if not poller.poll(100):
                continue
            raw = self.sock.recv_string()
            msg = AIMessage.model_validate_json(raw)
            if msg.DoneWithTraining:
                self.sock.send_string(self._make_state(frame, True, "Ongoing"))
                break
            if msg.Reset:
                frame = 0
                steps = 0
                self.sock.send_string(self._make_state(0, False, "Ongoing"))
            else:
                steps += 1
                frame += msg.Frames
                done = steps >= self.max_steps
                self.sock.send_string(self._make_state(frame, done, self.outcome))

    def close(self):
        self._stop = True
        self._thread.join(timeout=2.0)
        self.sock.close(linger=0)
        self.ctx.term()


def _zip_dir(folder: Path) -> bytes:
    buf = io.BytesIO()
    name = folder.name
    with zipfile.ZipFile(buf, "w") as zf:
        for p in folder.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                zf.write(p, f"{name}/{p.relative_to(folder).as_posix()}")
    return buf.getvalue()


def test_parse_cpu_stat():
    sample = "usage_usec 123456\nuser_usec 100000\nsystem_usec 23456\n"
    assert parse_cpu_stat_usec(sample) == 123456
    assert parse_cpu_stat_usec("other 1\n") is None


def test_referee_normal_and_hostile_agents(tmp_path: Path):
    # 1. Match normal rush_bot vs dummy_bot -> replay gzip valide
    srv = FakeHeadlessServer(max_steps_before_win=3, outcome="Win")
    gym = Gym(tcp_port=str(srv.port))
    p_left = SubprocessAgentProcess(KIT_PYTHON / "agents" / "rush_bot", "rush_bot")
    p_right = SubprocessAgentProcess(KIT_PYTHON / "agents" / "dummy_bot", "dummy_bot")
    try:
        assert p_left.start(10)[0]
        assert p_right.start(10)[0]
        res = play_match(
            gym, p_left, p_right,
            game_index=0, seed=42, left_name="rush_bot", right_name="dummy_bot",
            limits=MatchLimits(decision_mean_ms_limit=500.0, decision_timeout_ms=500),
        )
        assert res.outcome == "left"
        assert res.failure_side == ""
        replay = json.loads(gzip.decompress(res.replay_gzip).decode("utf-8"))
        assert replay["Version"] == 1
        assert replay["GameVersion"] == "1.0.0"
        assert replay["Seed"] == 42
        assert len(replay["Steps"]) == 3
    finally:
        p_left.stop()
        p_right.stop()
        gym.close()
        srv.close()

    # 2. Agent hostile qui lève une exception à chaque décision (> MAX_EXCEPTIONS_PER_GAME -> forfait)
    exc_dir = tmp_path / "exc_bot"
    exc_dir.mkdir()
    (exc_dir / "agent.py").write_text(
        "class ParticipantAgent:\n"
        "    def get_action(self, state):\n"
        "        raise ValueError('always fails')\n",
        encoding="utf-8",
    )
    srv2 = FakeHeadlessServer(max_steps_before_win=20, outcome="Draw")
    gym2 = Gym(tcp_port=str(srv2.port))
    p_exc = SubprocessAgentProcess(exc_dir, "exc_bot")
    p_dummy = SubprocessAgentProcess(KIT_PYTHON / "agents" / "dummy_bot", "dummy_bot")
    try:
        assert p_exc.start(10)[0]
        assert p_dummy.start(10)[0]
        res2 = play_match(
            gym2, p_exc, p_dummy,
            game_index=0, seed=7, left_name="exc_bot", right_name="dummy_bot",
            limits=MatchLimits(max_exceptions_per_game=3, decision_mean_ms_limit=500.0),
        )
        assert res2.outcome == "right"
        assert res2.failure_side == "left"
        assert res2.failure_reason == "too_many_exceptions"
    finally:
        p_exc.stop()
        p_dummy.stop()
        gym2.close()
        srv2.close()

    # 3. Agent hostile qui boucle sans fin dans get_action -> ne bloque pas chaque frame, perd par timeout/too_slow
    loop_dir = tmp_path / "loop_bot"
    loop_dir.mkdir()
    (loop_dir / "agent.py").write_text(
        "import time\n"
        "class ParticipantAgent:\n"
        "    def get_action(self, state):\n"
        "        time.sleep(5)\n"
        "        from bridge.datatypes import AIAction\n"
        "        return AIAction()\n",
        encoding="utf-8",
    )
    srv3 = FakeHeadlessServer(max_steps_before_win=4, outcome="Draw")
    gym3 = Gym(tcp_port=str(srv3.port))
    p_loop = SubprocessAgentProcess(loop_dir, "loop_bot")
    p_dummy3 = SubprocessAgentProcess(KIT_PYTHON / "agents" / "dummy_bot", "dummy_bot")
    try:
        assert p_loop.start(10)[0]
        assert p_dummy3.start(10)[0]
        res3 = play_match(
            gym3, p_loop, p_dummy3,
            game_index=0, seed=9, left_name="loop_bot", right_name="dummy_bot",
            limits=MatchLimits(decision_timeout_ms=100, decision_mean_ms_limit=5.0),
        )
        assert res3.outcome == "right"
        assert res3.failure_side == "left"
        assert res3.failure_reason in ("too_slow", "timeout")
    finally:
        p_loop.stop()
        p_dummy3.stop()
        gym3.close()
        srv3.close()


def test_worker_daemon_runs_validation_and_placement_against_api(tmp_path: Path, monkeypatch):
    # Préparer le site FastAPI + base SQLite
    db_path = tmp_path / "arene.db"
    data_dir = tmp_path / "data"
    ref_dir = tmp_path / "refs"
    ref_dir.mkdir()
    for name in ("rush_bot", "dummy_bot"):
        (ref_dir / f"{name}.zip").write_bytes(_zip_dir(KIT_PYTHON / "agents" / name))

    settings = Settings(
        DATABASE_URL=f"sqlite:///{db_path.as_posix()}",
        DATA_DIR=data_dir,
        ARENE_REF_AGENTS_DIR=ref_dir,
        WORKER_TOKEN="w-tok",
        COOLDOWN_MINUTES=0,
        PLACEMENT_OPPONENTS=2,
        PLACEMENT_MAPS_PER_OPPONENT=1,
        VALIDATION_GAMES=2,
        DECISION_MEAN_MS_LIMIT=500.0,
        BACKGROUND_ENABLED=False,
    )
    engine = create_db_engine(settings.database_url)
    apply_migrations(engine)
    sf = create_session_factory(engine)
    store = LocalFileStore(data_dir)
    with sf() as s:
        scheduler.seed_reference_agents(s, store, settings)
        with write_tx(s):
            s.execute(
                text("INSERT INTO accounts (id, github_id, github_login, created_at) VALUES ('acc1', 777, 'alice', :now)"),
                {"now": datetime.now(timezone.utc).isoformat()},
            )
        ag_id = scheduler.submit_agent(s, store, settings, "acc1", _zip_dir(KIT_PYTHON / "agents" / "rush_bot"), "127.0.0.1")

    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = sf
    app.state.store = store
    app.include_router(create_worker_router(settings))

    from fastapi.testclient import TestClient
    import worker.referee as ref_mod

    def _launch_on_port(port: int, binary_path: Path, timeout_s: float = 15.0):
        ctx = zmq.Context()
        sock = ctx.socket(zmq.REP)
        sock.bind(f"tcp://127.0.0.1:{port}")
        stop_flag = {"stop": False}

        def _loop():
            poller = zmq.Poller()
            poller.register(sock, zmq.POLLIN)
            frame = 0
            steps = 0
            while not stop_flag["stop"]:
                if not poller.poll(50):
                    continue
                msg = AIMessage.model_validate_json(sock.recv_string())
                if msg.DoneWithTraining:
                    sock.send_string(FakeHeadlessServer._make_state(None, frame, True, "Ongoing"))
                    break
                if msg.Reset:
                    frame = 0
                    steps = 0
                    sock.send_string(FakeHeadlessServer._make_state(None, 0, False, "Ongoing"))
                else:
                    steps += 1
                    frame += msg.Frames
                    sock.send_string(FakeHeadlessServer._make_state(None, frame, steps >= 2, "Win"))

        t = threading.Thread(target=_loop, daemon=True)
        t.start()

        class _Proc:
            def poll(self):
                return None
            def kill(self):
                stop_flag["stop"] = True
                t.join(timeout=1.5)
                sock.close(linger=0)
                ctx.term()
            def wait(self, timeout=None):
                return 0
        return _Proc()

    monkeypatch.setattr(ref_mod, "launch_headless_process", _launch_on_port)

    client = WorkerAPIClient("http://testserver/arene", "w-tok", tmp_path / "cache")
    client._client.close()
    tc = TestClient(app)
    tc.base_url = "http://testserver/arene"
    tc.headers.update({"Authorization": "Bearer w-tok"})
    client._client = tc
    wconf = WorkerConfig(
        site_url="http://testserver/arene",
        worker_token="w-tok",
        parallel_matches=1,
        nice=0,
        worker_id="test-worker",
        agent_image="automathon-agent:1.0.0",
        agent_work_volume="v",
        agent_work_dir=tmp_path / "work",
        cgroup_root=tmp_path / "cg",
        headless_bin=None,
        cache_dir=tmp_path / "cache",
    )
    daemon = WorkerDaemon(
        wconf,
        client,
        headless_binary=tmp_path / "dummy_headless",
        agent_process_factory=lambda ag_dir, ag_name, _mem: SubprocessAgentProcess(ag_dir, ag_name),
    )

    try:
        # 1 validation + 2 placement jobs = 3 jobs
        assert daemon.run_once() is True
        assert daemon.run_once() is True
        assert daemon.run_once() is True
        assert daemon.run_once() is False
    finally:
        client.close()

    with sf() as s:
        row = s.execute(
            text("SELECT status, is_active, games_played, placement_done FROM agents WHERE id = :id"),
            {"id": ag_id},
        ).fetchone()
        replay_count = s.execute(
            text("SELECT COUNT(*) FROM matches WHERE replay_key != ''")
        ).scalar_one()

    assert row[0] == "ranked"
    assert row[1] == 1
    assert row[2] == 4
    assert row[3] == 2
    assert replay_count == 4
