"""Arbitre d'une partie et d'une confrontation sur le Headless avec deux agents isolés."""

from __future__ import annotations

import concurrent.futures
import gzip
import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_PYTHON = REPO_ROOT / "PythonAI"
if str(KIT_PYTHON) not in sys.path:
    sys.path.insert(0, str(KIT_PYTHON))

from bridge.datatypes import DECISION_INTERVAL, AIAction, GameState, ResetOptions  # noqa: E402
from bridge.gym import Gym  # noqa: E402

from worker.sandbox import AgentProcess  # noqa: E402

_OPPOSITE_OUTCOME = {"Win": "Loss", "Loss": "Win"}


def enemy_view(state: GameState) -> GameState:
    """Le même état vu par l'adversaire : vaisseaux, dégâts et résultat intervertis."""
    return GameState.model_construct(
        SelfShip=state.EnemyShip,
        EnemyShip=state.SelfShip,
        BulletStates=state.BulletStates,
        MissileStates=state.MissileStates,
        WallStates=state.WallStates,
        ShieldStates=state.ShieldStates,
        Done=state.Done,
        Outcome=_OPPOSITE_OUTCOME.get(state.Outcome, state.Outcome),
        Frame=state.Frame,
        DamageDealt=state.DamageTaken,
        DamageTaken=state.DamageDealt,
        ZoneHalfSize=state.ZoneHalfSize,
    )

REPLAY_VERSION = 1


@dataclass
class MatchLimits:
    decision_mean_ms_limit: float = 5.0
    decision_timeout_ms: int = 500
    max_exceptions_per_game: int = 50
    agent_load_timeout_s: int = 60
    agent_memory_mb: int = 768
    max_frames: int = 4200


@dataclass
class MatchOutcome:
    index: int
    seed: int
    outcome: str  # "left", "right", "draw"
    end_frame: int
    left_health: float
    right_health: float
    left_mean_ms: float
    left_max_ms: float
    right_mean_ms: float
    right_max_ms: float
    left_exceptions: int
    right_exceptions: int
    failure_side: str = ""
    failure_reason: str = ""
    replay_gzip: bytes = b""
    fatal_abort_side: str = ""  # "left" ou "right" si crash/OOM devant annuler la confrontation


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def resolve_headless_binary(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_p = os.environ.get("AUTOMATHON_HEADLESS_PATH")
    if env_p:
        candidates.append(Path(env_p))
    binary = "Headless.exe" if sys.platform == "win32" else "Headless"
    candidates.extend([
        Path("/app/Headless") / binary,
        REPO_ROOT / "sources" / "extracted" / "kit" / "Headless" / binary,
        REPO_ROOT / "Headless" / binary,
        REPO_ROOT / "build" / "headless" / ("win-x64" if sys.platform == "win32" else "linux-x64") / binary,
    ])
    for cand in candidates:
        if cand.is_file():
            return cand
    raise FileNotFoundError(f"Binaire Headless introuvable (cherché : {[str(c) for c in candidates]})")


def launch_headless_process(port: int, binary_path: Path, timeout_s: float = 15.0) -> subprocess.Popen:
    cmd = [str(binary_path), f"tcp://127.0.0.1:{port}"]
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        env = os.environ.copy()
        env["DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"] = "1"
        env["DOTNET_gcServer"] = "0"
        kwargs["env"] = env

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Headless crashé au lancement (code {proc.returncode})")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return proc
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError(f"Headless n'écoute pas sur le port {port} après {timeout_s}s")


def _action_json(action: AIAction) -> dict[str, Any]:
    return json.loads(action.model_dump_json())


def _position_json(ship) -> dict[str, float] | None:
    return None if ship is None else {"X": float(ship.Position.X), "Y": float(ship.Position.Y)}


def _state_dict(state: GameState) -> dict[str, Any]:
    return json.loads(state.model_dump_json())


def play_match(
    gym: Gym,
    left_proc: AgentProcess,
    right_proc: AgentProcess,
    *,
    game_index: int,
    seed: int,
    left_name: str,
    right_name: str,
    limits: MatchLimits,
    game_version: str = "1.0.0",
) -> MatchOutcome:
    """Joue une partie complète entre deux hôtes d'agent isolés et produit son replay gzip."""
    ok_l, err_l = left_proc.reset(limits.agent_load_timeout_s)
    ok_r, err_r = right_proc.reset(limits.agent_load_timeout_s)
    if not ok_l or not ok_r:
        if not ok_l and not ok_r:
            return MatchOutcome(
                index=game_index, seed=seed, outcome="draw", end_frame=0,
                left_health=0.0, right_health=0.0, left_mean_ms=0.0, left_max_ms=0.0,
                right_mean_ms=0.0, right_max_ms=0.0, left_exceptions=0, right_exceptions=0,
                failure_side="both", failure_reason=f"L:{err_l} | R:{err_r}", fatal_abort_side="both",
            )
        side = "left" if not ok_l else "right"
        return MatchOutcome(
            index=game_index, seed=seed, outcome="right" if side == "left" else "left", end_frame=0,
            left_health=0.0 if side == "left" else 1000.0,
            right_health=0.0 if side == "right" else 1000.0,
            left_mean_ms=0.0, left_max_ms=0.0, right_mean_ms=0.0, right_max_ms=0.0,
            left_exceptions=0, right_exceptions=0,
            failure_side=side, failure_reason=err_l if side == "left" else err_r, fatal_abort_side=side,
        )

    state = gym.reset(
        tcp_connection_timeout=5000,
        options=ResetOptions(Seed=seed, MapDensity=None, SelfOnRight=False, Rules=None),
    )

    prev_left = AIAction()
    prev_right = AIAction()
    left_times: list[float] = []
    right_times: list[float] = []
    left_exc = 0
    right_exc = 0
    left_timeouts = 0
    right_timeouts = 0
    steps: list[dict[str, Any]] = []

    decision_id = 0
    fatal_side = ""
    fatal_reason = ""
    abort_side = ""

    while not state.Done and state.Frame < limits.max_frames:
        decision_id += 1
        res_l = left_proc.decide(decision_id, _state_dict(state), limits.decision_timeout_ms)
        if res_l.fatal:
            fatal_side, fatal_reason, abort_side = "left", res_l.fatal_reason, "left"
            break

        res_r = right_proc.decide(decision_id, _state_dict(enemy_view(state)), limits.decision_timeout_ms)
        if res_r.fatal:
            fatal_side, fatal_reason, abort_side = "right", res_r.fatal_reason, "right"
            break

        left_times.append(res_l.cpu_ms)
        right_times.append(res_r.cpu_ms)

        if res_l.timed_out:
            left_timeouts += 1
            left_action = prev_left
        elif res_l.error is not None:
            left_exc += 1
            left_action = AIAction()
        else:
            assert res_l.action is not None
            left_action = res_l.action
            prev_left = left_action

        if res_r.timed_out:
            right_timeouts += 1
            right_action = prev_right
        elif res_r.error is not None:
            right_exc += 1
            right_action = AIAction()
        else:
            assert res_r.action is not None
            right_action = res_r.action
            prev_right = right_action

        if left_exc > limits.max_exceptions_per_game and right_exc > limits.max_exceptions_per_game:
            fatal_side, fatal_reason = "both", "too_many_exceptions"
            break
        if left_exc > limits.max_exceptions_per_game:
            fatal_side, fatal_reason = "left", "too_many_exceptions"
            break
        if right_exc > limits.max_exceptions_per_game:
            fatal_side, fatal_reason = "right", "too_many_exceptions"
            break

        steps.append({
            "Frames": DECISION_INTERVAL,
            "Left": _action_json(left_action),
            "Right": _action_json(right_action),
        })
        state = gym.step(left_action, right_action, tcp_connection_timeout=5000, frames=DECISION_INTERVAL)

    left_hp = float(state.SelfShip.Health) if state.SelfShip else 0.0
    right_hp = float(state.EnemyShip.Health) if state.EnemyShip else 0.0
    left_mean = sum(left_times) / max(1, len(left_times))
    left_max = max(left_times, default=0.0)
    right_mean = sum(right_times) / max(1, len(right_times))
    right_max = max(right_times, default=0.0)

    if fatal_side:
        outcome = "draw" if fatal_side == "both" else ("right" if fatal_side == "left" else "left")
    else:
        if state.Outcome == "Win":
            outcome = "left"
        elif state.Outcome == "Loss":
            outcome = "right"
        else:
            outcome = "draw"

        # Vérification du temps CPU moyen
        slow_l = bool(left_times) and left_mean > limits.decision_mean_ms_limit
        slow_r = bool(right_times) and right_mean > limits.decision_mean_ms_limit
        if slow_l and slow_r:
            fatal_side, fatal_reason, outcome = "both", "too_slow", "draw"
        elif slow_l:
            fatal_side, fatal_reason, outcome = "left", "too_slow", "right"
        elif slow_r:
            fatal_side, fatal_reason, outcome = "right", "too_slow", "left"
        elif left_timeouts == len(left_times) and left_timeouts > 0:
            fatal_side, fatal_reason, outcome = "left", "timeout", "right"
        elif right_timeouts == len(right_times) and right_timeouts > 0:
            fatal_side, fatal_reason, outcome = "right", "timeout", "left"

    replay_obj = {
        "Version": REPLAY_VERSION,
        "GameVersion": game_version,
        "Seed": seed,
        "MapDensity": None,
        "Rules": None,
        "LeftName": left_name,
        "RightName": right_name,
        "Steps": steps,
        "Result": {
            "Frame": state.Frame,
            "Outcome": state.Outcome,
            "LeftHealth": left_hp,
            "RightHealth": right_hp,
            "LeftPosition": _position_json(state.SelfShip),
            "RightPosition": _position_json(state.EnemyShip),
        },
    }
    replay_bytes = gzip.compress(
        json.dumps(replay_obj, separators=(",", ":")).encode("utf-8"),
        mtime=0,
    )

    return MatchOutcome(
        index=game_index,
        seed=seed,
        outcome=outcome,
        end_frame=int(state.Frame),
        left_health=left_hp,
        right_health=right_hp,
        left_mean_ms=round(left_mean, 3),
        left_max_ms=round(left_max, 3),
        right_mean_ms=round(right_mean, 3),
        right_max_ms=round(right_max, 3),
        left_exceptions=left_exc,
        right_exceptions=right_exc,
        failure_side=fatal_side,
        failure_reason=fatal_reason,
        replay_gzip=replay_bytes,
        fatal_abort_side=abort_side,
    )


AgentFactory = Callable[[str, int], AgentProcess]


def play_confrontation(
    games: list[dict[str, Any]],
    agent_names: dict[str, str],
    agent_factory: AgentFactory,
    *,
    headless_binary: Path,
    limits: MatchLimits,
    parallel_matches: int = 2,
    game_version: str = "1.0.0",
) -> list[MatchOutcome]:
    """Joue toutes les parties d'un job en parallèle sur `parallel_matches` slots."""
    if not games:
        return []

    n_slots = max(1, min(parallel_matches, len(games)))
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(n_slots)]
    for i, g in enumerate(games):
        buckets[i % n_slots].append(g)

    abort_lock = threading.Lock()
    aborted_agent_id: str | None = None
    abort_reason = ""

    def _run_slot(slot_idx: int, slot_games: list[dict[str, Any]]) -> list[MatchOutcome]:
        nonlocal aborted_agent_id, abort_reason
        port = find_free_port()
        headless_proc = launch_headless_process(port, headless_binary)
        gym = Gym(tcp_port=str(port))
        procs: dict[str, AgentProcess] = {}
        results: list[MatchOutcome] = []

        try:
            for g in slot_games:
                l_id = g["left_agent_id"]
                r_id = g["right_agent_id"]
                with abort_lock:
                    cur_abort = aborted_agent_id
                    cur_reason = abort_reason

                if cur_abort in (l_id, r_id):
                    side = "left" if cur_abort == l_id else "right"
                    results.append(
                        MatchOutcome(
                            index=int(g["index"]),
                            seed=int(g["seed"]),
                            outcome="right" if side == "left" else "left",
                            end_frame=0,
                            left_health=0.0 if side == "left" else 1000.0,
                            right_health=0.0 if side == "right" else 1000.0,
                            left_mean_ms=0.0,
                            left_max_ms=0.0,
                            right_mean_ms=0.0,
                            right_max_ms=0.0,
                            left_exceptions=0,
                            right_exceptions=0,
                            failure_side=side,
                            failure_reason=cur_reason,
                        )
                    )
                    continue

                for aid in (l_id, r_id):
                    if aid not in procs:
                        proc = agent_factory(aid, slot_idx)
                        ok, err = proc.start(limits.agent_load_timeout_s)
                        if not ok:
                            proc.stop()
                            with abort_lock:
                                aborted_agent_id = aid
                                abort_reason = err or "crash"
                            side = "left" if aid == l_id else "right"
                            results.append(
                                MatchOutcome(
                                    index=int(g["index"]),
                                    seed=int(g["seed"]),
                                    outcome="right" if side == "left" else "left",
                                    end_frame=0,
                                    left_health=0.0 if side == "left" else 1000.0,
                                    right_health=0.0 if side == "right" else 1000.0,
                                    left_mean_ms=0.0,
                                    left_max_ms=0.0,
                                    right_mean_ms=0.0,
                                    right_max_ms=0.0,
                                    left_exceptions=0,
                                    right_exceptions=0,
                                    failure_side=side,
                                    failure_reason=err or "crash",
                                    fatal_abort_side=side,
                                )
                            )
                            break
                        procs[aid] = proc
                else:
                    res = play_match(
                        gym,
                        procs[l_id],
                        procs[r_id],
                        game_index=int(g["index"]),
                        seed=int(g["seed"]),
                        left_name=agent_names[l_id],
                        right_name=agent_names[r_id],
                        limits=limits,
                        game_version=game_version,
                    )
                    if res.fatal_abort_side:
                        dead_id = l_id if res.fatal_abort_side == "left" else r_id
                        with abort_lock:
                            aborted_agent_id = dead_id
                            abort_reason = res.failure_reason
                        # Nettoyer le conteneur planté
                        dead_proc = procs.pop(dead_id, None)
                        if dead_proc is not None:
                            dead_proc.stop()
                    results.append(res)
            return results
        finally:
            for p in procs.values():
                p.stop()
            try:
                gym.end_training()
            except Exception:
                pass
            try:
                gym.close()
            except Exception:
                pass
            if headless_proc.poll() is None:
                headless_proc.kill()
                headless_proc.wait(timeout=3.0)

    all_outcomes: list[MatchOutcome] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_slots) as pool:
        futures = [pool.submit(_run_slot, idx, bucket) for idx, bucket in enumerate(buckets)]
        for fut in futures:
            all_outcomes.extend(fut.result())

    all_outcomes.sort(key=lambda o: o.index)
    return all_outcomes
