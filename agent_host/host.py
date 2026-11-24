"""Hôte isolé d'un agent Automathon.

Tourne dans le conteneur `automathon-agent:1.0.0` sans accès réseau, avec le dossier
de l'agent monté en lecture seule dans AGENT_DIR (défaut : /agent).

Protocole sur stdin / stdout (un objet JSON par ligne) :
  - {"cmd": "init"}                                -> {"cmd": "init", "ok": true}
  - {"cmd": "reset"}                               -> {"cmd": "reset", "ok": true}
  - {"cmd": "decide", "decision_id": N, "state": ...}
        -> {"cmd": "decide", "decision_id": N, "action": ...}
        ou {"cmd": "decide", "decision_id": N, "error": "..."}
  - {"cmd": "quit"}                                -> exit 0
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import traceback
import types
from pathlib import Path

# Garde la vraie sortie standard pour le protocole JSON-line, et redirige print()
# des participants vers stderr pour ne jamais corrompre le canal de commande.
_PROTO_OUT = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
os.dup2(2, 1)
sys.stdout = sys.stderr


def _send(payload: dict) -> None:
    _PROTO_OUT.write(json.dumps(payload, separators=(",", ":")) + "\n")
    _PROTO_OUT.flush()


def _short_trace(limit: int = 1200) -> str:
    tb = traceback.format_exc()
    return tb[-limit:] if len(tb) > limit else tb


def _configure_agent_import_paths(agent_dir: Path, agent_name: str) -> None:
    kit_python = os.environ.get("AUTOMATHON_KIT_PYTHON")
    if kit_python and kit_python not in sys.path:
        sys.path.insert(0, kit_python)
    parent = str(agent_dir.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    agent_str = str(agent_dir)
    if agent_str not in sys.path:
        sys.path.insert(0, agent_str)

    # Expose agents.<agent_name> pointant vers agent_dir, pour supporter
    # `from agents.<nom>.xxx import ...` ainsi que les imports relatifs dans agent.py.
    if "agents" not in sys.modules:
        agents_pkg = types.ModuleType("agents")
        agents_pkg.__path__ = [parent, agent_str]
        sys.modules["agents"] = agents_pkg

    pkg_name = f"agents.{agent_name}"
    if pkg_name not in sys.modules:
        init_file = agent_dir / "__init__.py"
        if init_file.is_file():
            spec = importlib.util.spec_from_file_location(
                pkg_name,
                str(init_file),
                submodule_search_locations=[agent_str],
            )
            mod = importlib.util.module_from_spec(spec)
            sys.modules[pkg_name] = mod
            if spec and spec.loader:
                spec.loader.exec_module(mod)
        else:
            pkg = types.ModuleType(pkg_name)
            pkg.__path__ = [agent_str]
            sys.modules[pkg_name] = pkg


def _load_agent_class(agent_dir: Path, agent_name: str):
    agent_file = agent_dir / "agent.py"
    if not agent_file.is_file():
        raise FileNotFoundError(f"agent.py introuvable dans {agent_dir}")
    _configure_agent_import_paths(agent_dir, agent_name)
    mod_name = f"agents.{agent_name}.agent"
    if mod_name in sys.modules:
        module = sys.modules[mod_name]
    else:
        spec = importlib.util.spec_from_file_location(mod_name, str(agent_file))
        if spec is None or spec.loader is None:
            raise ImportError(f"Impossible de charger {agent_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
    if not hasattr(module, "ParticipantAgent"):
        raise AttributeError("agent.py ne définit pas la classe ParticipantAgent")
    return module.ParticipantAgent


def main() -> int:
    agent_dir = Path(os.environ.get("AGENT_DIR", "/agent")).resolve()
    agent_name = os.environ.get("AGENT_NAME", agent_dir.name)

    from bridge.datatypes import AIAction, BulletState, FixedVector2, GameState, MissileState, ShipState, WallState  # noqa: E402

    def _warmup_state() -> GameState:
        return GameState(
            SelfShip=ShipState(
                Position=FixedVector2(-6.0, 0.0),
                Team="Green",
                Width=0.838,
                Height=0.838,
                Velocity=FixedVector2(0.0, 0.0),
                Health=1000.0,
                ShieldCooldownFramesLeft=0,
                MissileCooldownFramesLeft=0,
                MachineGunCooldownFramesLeft=0,
                DashCooldownFramesLeft=0,
            ),
            EnemyShip=ShipState(
                Position=FixedVector2(6.0, 0.0),
                Team="Red",
                Width=0.838,
                Height=0.838,
                Velocity=FixedVector2(0.0, 0.0),
                Health=1000.0,
                ShieldCooldownFramesLeft=0,
                MissileCooldownFramesLeft=0,
                MachineGunCooldownFramesLeft=0,
                DashCooldownFramesLeft=0,
            ),
            BulletStates=[
                BulletState(Position=FixedVector2(0.0, 1.0), Owner="Green", Radius=0.1, Velocity=FixedVector2(10.0, 0.0))
            ],
            MissileStates=[
                MissileState(Position=FixedVector2(0.0, -1.0), Owner="Red", Radius=0.2, Velocity=FixedVector2(-5.0, 0.0), Armed=True)
            ],
            WallStates=[
                WallState(Position=FixedVector2(0.0, 0.0), Size=FixedVector2(1.0, 2.0), Rotation=0.0)
            ],
            Done=False,
            Frame=4,
        )

    agent_cls = None
    agent_instance = None

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as exc:
            _send({"cmd": "error", "ok": False, "error": f"JSON invalide : {exc}"})
            continue

        cmd = msg.get("cmd")
        if cmd == "quit":
            _send({"cmd": "quit", "ok": True})
            return 0

        if cmd == "init":
            try:
                agent_cls = _load_agent_class(agent_dir, agent_name)
                agent_instance = agent_cls()
                if not callable(getattr(agent_instance, "get_action", None)):
                    raise AttributeError("ParticipantAgent.get_action introuvable ou non appelable")
                # Préchauffage réaliste (compile Numba / initialise PyTorch pendant AGENT_LOAD_TIMEOUT_S)
                try:
                    agent_instance.get_action(_warmup_state())
                except Exception:
                    pass
                # Recrée une instance propre pour la première partie
                agent_instance = agent_cls()
                _send({"cmd": "init", "ok": True})
            except Exception:
                _send({"cmd": "init", "ok": False, "error": _short_trace()})

        elif cmd == "reset":
            try:
                if agent_cls is None:
                    agent_cls = _load_agent_class(agent_dir, agent_name)
                agent_instance = agent_cls()
                _send({"cmd": "reset", "ok": True})
            except Exception:
                _send({"cmd": "reset", "ok": False, "error": _short_trace()})

        elif cmd == "decide":
            decision_id = msg.get("decision_id")
            try:
                if agent_instance is None:
                    if agent_cls is None:
                        agent_cls = _load_agent_class(agent_dir, agent_name)
                    agent_instance = agent_cls()
                state = GameState.model_validate(msg["state"])
                action = agent_instance.get_action(state)
                if not isinstance(action, AIAction):
                    action = AIAction.model_validate(action)
                _send({
                    "cmd": "decide",
                    "decision_id": decision_id,
                    "action": json.loads(action.model_dump_json()),
                })
            except Exception:
                _send({
                    "cmd": "decide",
                    "decision_id": decision_id,
                    "error": _short_trace(),
                })
        else:
            _send({"cmd": str(cmd), "ok": False, "error": f"Commande inconnue : {cmd}"})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
