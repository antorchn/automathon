"""Tests de l'hôte d'agent (agent_host/host.py) et du protocole JSON-line."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST_SCRIPT = ROOT / "agent_host" / "host.py"
KIT_PYTHON = ROOT / "PythonAI"

if str(KIT_PYTHON) not in sys.path:
    sys.path.insert(0, str(KIT_PYTHON))

from bridge.datatypes import AIAction, FixedVector2, GameState, ShipState  # noqa: E402


def _sample_state_dict() -> dict:
    state = GameState(
        SelfShip=ShipState(
            Position=FixedVector2(-5.0, 0.0),
            Team="Green",
            Width=1.0,
            Height=1.0,
            Velocity=FixedVector2(0.0, 0.0),
            Health=1000.0,
            ShieldCooldownFramesLeft=0,
            MissileCooldownFramesLeft=0,
            MachineGunCooldownFramesLeft=0,
            DashCooldownFramesLeft=0,
        ),
        EnemyShip=ShipState(
            Position=FixedVector2(5.0, 0.0),
            Team="Red",
            Width=1.0,
            Height=1.0,
            Velocity=FixedVector2(0.0, 0.0),
            Health=1000.0,
            ShieldCooldownFramesLeft=0,
            MissileCooldownFramesLeft=0,
            MachineGunCooldownFramesLeft=0,
            DashCooldownFramesLeft=0,
        ),
        Done=False,
        Frame=4,
    )
    return json.loads(state.model_dump_json())


def _spawn_host(agent_dir: Path, agent_name: str | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    env["AGENT_DIR"] = str(agent_dir)
    env["AGENT_NAME"] = agent_name or agent_dir.name
    env["AUTOMATHON_KIT_PYTHON"] = str(KIT_PYTHON)
    env["PYTHONPATH"] = str(KIT_PYTHON)
    return subprocess.Popen(
        [sys.executable, "-u", str(HOST_SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _rpc(proc: subprocess.Popen, payload: dict) -> dict:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    assert line, f"EOF from host; stderr={proc.stderr.read() if proc.stderr else ''}"
    return json.loads(line)


def test_rush_bot_and_dummy_bot_protocol():
    state_dict = _sample_state_dict()
    for bot_name in ("rush_bot", "dummy_bot"):
        proc = _spawn_host(KIT_PYTHON / "agents" / bot_name, bot_name)
        try:
            res = _rpc(proc, {"cmd": "init"})
            assert res == {"cmd": "init", "ok": True}

            res = _rpc(proc, {"cmd": "decide", "decision_id": 7, "state": state_dict})
            assert res["cmd"] == "decide"
            assert res["decision_id"] == 7
            assert "error" not in res
            action = AIAction.model_validate(res["action"])
            assert action.MachineGun is True

            res = _rpc(proc, {"cmd": "reset"})
            assert res == {"cmd": "reset", "ok": True}

            res = _rpc(proc, {"cmd": "quit"})
            assert res == {"cmd": "quit", "ok": True}
            assert proc.wait(timeout=5) == 0
        finally:
            if proc.poll() is None:
                proc.kill()


def test_participant_print_does_not_corrupt_stdout_and_reset_recreates(tmp_path: Path):
    agent_dir = tmp_path / "noisy_bot"
    agent_dir.mkdir()
    (agent_dir / "helper.py").write_text("VALUE = 42\n", encoding="utf-8")
    (agent_dir / "agent.py").write_text(
        "from bridge.datatypes import AIAction\n"
        "from agents.noisy_bot.helper import VALUE\n"
        "class ParticipantAgent:\n"
        "    def __init__(self):\n"
        "        self.calls = 0\n"
        "        print('INIT NOISE TO STDOUT', VALUE)\n"
        "    def get_action(self, state):\n"
        "        self.calls += 1\n"
        "        print(f'CALL {self.calls}')\n"
        "        return AIAction(MachineGun=(self.calls == 1))\n",
        encoding="utf-8",
    )

    state_dict = _sample_state_dict()
    proc = _spawn_host(agent_dir, "noisy_bot")
    try:
        assert _rpc(proc, {"cmd": "init"}) == {"cmd": "init", "ok": True}
        d1 = _rpc(proc, {"cmd": "decide", "decision_id": 1, "state": state_dict})
        d2 = _rpc(proc, {"cmd": "decide", "decision_id": 2, "state": state_dict})
        assert d1["action"]["MachineGun"] is True
        assert d2["action"]["MachineGun"] is False

        assert _rpc(proc, {"cmd": "reset"}) == {"cmd": "reset", "ok": True}
        d3 = _rpc(proc, {"cmd": "decide", "decision_id": 3, "state": state_dict})
        assert d3["action"]["MachineGun"] is True

        _rpc(proc, {"cmd": "quit"})
        proc.wait(timeout=5)
        stderr_out = proc.stderr.read() if proc.stderr else ""
        assert "INIT NOISE TO STDOUT 42" in stderr_out
    finally:
        if proc.poll() is None:
            proc.kill()


def test_agent_exception_returns_error_payload(tmp_path: Path):
    agent_dir = tmp_path / "crash_bot"
    agent_dir.mkdir()
    (agent_dir / "agent.py").write_text(
        "class ParticipantAgent:\n"
        "    def get_action(self, state):\n"
        "        raise RuntimeError('boom!')\n",
        encoding="utf-8",
    )
    proc = _spawn_host(agent_dir, "crash_bot")
    try:
        assert _rpc(proc, {"cmd": "init"}) == {"cmd": "init", "ok": True}
        res = _rpc(proc, {"cmd": "decide", "decision_id": 99, "state": _sample_state_dict()})
        assert res["decision_id"] == 99
        assert "RuntimeError: boom!" in res["error"]
        _rpc(proc, {"cmd": "quit"})
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
