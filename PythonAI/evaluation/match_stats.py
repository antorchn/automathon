"""
Statistiques de jeu entre deux agents : qui gagne, et comment (arme, distance, durée des matchs).

Usage (depuis PythonAI/) :
    python evaluation/match_stats.py rush_bot dummy_bot --games 20
    python evaluation/match_stats.py model:training/rl_model.zip rush_bot --rules '{"MissileArmingDistance": 0}'

Un agent est un dossier de agents/, ou model:<chemin> pour un modèle PPO (joué comme my_rl_agent). Les
matchs se jouent sur le Headless dans les conditions d'un match (les deux agents décident une frame sur 4), mais
frame par frame, pour attribuer chaque perte de PV :
- missile : la perte atteint les dégâts d'un missile (le plus petit de MissileDirectDamage et MissileAreaDamage) et un
  missile est à portée (un missile qui explose figure encore dans l'état de la frame de l'explosion) ;
- zone : le vaisseau est hors de la zone sûre (au plus ZoneDamagePerSecond / 60 par frame) ;
- balles : le reste.
Chaque match est joué sur une carte fixe, des deux côtés (graines --seed0 et suivantes).
"""

import argparse
import importlib
import json
import math
import os
import sys
import time
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.binary import (M_X, M_Y, SHIP_CD_DASH, SHIP_CD_MG, SHIP_CD_MISSILE, SHIP_CD_SHIELD,  # noqa: E402
                           SHIP_EXISTS, SHIP_HP, SHIP_X, SHIP_Y, BinaryGym, LazyGameState)
from bridge.datatypes import DECISION_INTERVAL, AIAction, FixedVector2, GameRules, ResetOptions  # noqa: E402
from training.utils import launch_headless  # noqa: E402

ONE = 65536.0
MAX_FRAMES = 4200  # un match finit avant : la zone se ferme à 40 s, puis 50 PV/s
MISSILE_REACH = 1.6        # un missile à moins de cette distance du vaisseau a pu le toucher (zone + rayons)
DISTANCES = (2.0, 4.0, 8.0)  # tranches de distance entre les vaisseaux au moment des dégâts
DIST_LABELS = ("< 2", "2-4", "4-8", "> 8")


class ModelAgent:
    """Un modèle PPO (stable-baselines3), joué avec l'extracteur du kit, comme my_rl_agent."""

    def __init__(self, path: str):
        from stable_baselines3 import PPO

        from agents.my_rl_agent.feature_extractor import (extract_features, flip_action, starts_on_right,
                                                                warmup)
        self.model = PPO.load(path, device="cpu")
        self._extract, self._flip_action, self._right = extract_features, flip_action, starts_on_right
        warmup()

    def get_action(self, state) -> AIAction:
        flip = self._right(state)
        action, _ = self.model.predict(self._extract(state, flip=flip), deterministic=True)
        if flip:
            action = self._flip_action(action)
        return AIAction(MovingDirection=FixedVector2(float(action[0]), float(action[1])),
                        AimingDirection=FixedVector2(float(action[2]), float(action[3])),
                        MachineGun=bool(action[4] > 0), Missile=bool(action[5] > 0), Shield=bool(action[6] > 0),
                        Dash=bool(action[7] > 0))


def load_agent(spec: str):
    if spec.startswith("model:"):
        return ModelAgent(spec[len("model:"):])
    return importlib.import_module(f"agents.{spec}.agent").ParticipantAgent()


def _pos(ship):
    return ship[SHIP_X] / ONE, ship[SHIP_Y] / ONE


def play(gym, spec_a, spec_b, seed, a_on_right, rules: GameRules, stats):
    """Un match ; A est le vaisseau « self » du Headless. Ajoute ses statistiques à stats."""
    agent_a, agent_b = load_agent(spec_a), load_agent(spec_b)
    raw = gym.reset(ResetOptions(Seed=seed, SelfOnRight=a_on_right, Rules=rules), tcp_connection_timeout=10000)
    zone_dps = float(rules.value("ZoneDamagePerSecond"))
    missile_min = min(float(rules.value("MissileDirectDamage")), float(rules.value("MissileAreaDamage")))
    prev = raw
    action_a = action_b = None
    near_frames = 0
    while True:
        if raw.frame % DECISION_INTERVAL == 0:
            action_a = agent_a.get_action(LazyGameState(raw))
            action_b = agent_b.get_action(LazyGameState(raw, swap=True))
        raw = gym.step(action_a, action_b, frames=1, tcp_connection_timeout=10000)
        ships_prev = (prev.self_ship, prev.enemy_ship)
        ships_now = (raw.self_ship, raw.enemy_ship)
        if ships_prev[0][SHIP_EXISTS] and ships_prev[1][SHIP_EXISTS]:
            (xa, ya), (xb, yb) = _pos(ships_prev[0]), _pos(ships_prev[1])
            dist = math.hypot(xa - xb, ya - yb)
            near_frames += dist < 3.0
        else:
            dist = 0.0
        # missiles de la frame précédente et de celle-ci (un missile qui explose y figure encore)
        missiles = [(m[M_X] / ONE, m[M_Y] / ONE) for block in (prev.missiles, raw.missiles) for m in block]
        for side in (0, 1):
            before, after = ships_prev[side], ships_now[side]
            if not before[SHIP_EXISTS]:
                continue
            loss = before[SHIP_HP] / ONE - (after[SHIP_HP] / ONE if after[SHIP_EXISTS] else 0.0)
            if loss <= 1e-6:
                continue
            bx, by = _pos(before)
            zone_part = 0.0
            if zone_dps > 0 and (abs(bx) > raw.zone_x / ONE or abs(by) > raw.zone_y / ONE):
                zone_part = min(loss, zone_dps / 60.0 + 1e-3)
            rest = loss - zone_part
            near_missile = any(math.hypot(mx - bx, my - by) < MISSILE_REACH for mx, my in missiles)
            weapon = "missile" if rest >= missile_min - 1e-3 and near_missile else "balles"
            dealer = "B" if side == 0 else "A"  # dégâts subis par A = infligés par B
            victim = "A" if side == 0 else "B"
            stats["zone_" + victim] += zone_part
            if rest > 1e-6:
                stats[f"{dealer}_{weapon}"] += rest
                bucket = sum(dist >= d for d in DISTANCES)
                stats[f"{dealer}_dist_{bucket}"] += rest
        for side, name in ((0, "A"), (1, "B")):
            before, after = ships_prev[side], ships_now[side]
            if before[SHIP_EXISTS] and after[SHIP_EXISTS]:
                for col, label in ((SHIP_CD_MG, "rafales"), (SHIP_CD_MISSILE, "missiles"), (SHIP_CD_SHIELD, "boucliers"),
                                   (SHIP_CD_DASH, "dashs")):
                    if after[col] > before[col]:
                        stats[f"{name}_{label}"] += 1
        prev = raw
        if raw.done or raw.frame >= MAX_FRAMES:
            break
    outcome = raw.outcome if raw.done else "Timeout"
    stats["matches"] += 1
    stats[f"issue_{outcome}"] += 1
    stats["frames"] += raw.frame
    stats["near_frames"] += near_frames


def report(stats, spec_a, spec_b, rules):
    n = max(stats["matches"], 1)
    lines = [f"{spec_a} (A) contre {spec_b} (B), {stats['matches']} matchs, règles {rules or 'par défaut'}"]
    lines.append(f"  issues pour A : {stats['issue_Win']} V / {stats['issue_Loss']} D / {stats['issue_Draw']} N"
                 f" / {stats['issue_Timeout']} temps écoulé ; durée moyenne {stats['frames'] / n / 60:.1f} s ;"
                 f" vaisseaux à moins de 3 unités {stats['near_frames'] / max(stats['frames'], 1):.0%} du temps")
    for who in ("A", "B"):
        total = stats[f"{who}_balles"] + stats[f"{who}_missile"]
        if total <= 0:
            lines.append(f"  {who} : aucun dégât infligé")
            continue
        dist = " ".join(f"{lab} {stats[f'{who}_dist_{k}'] / total:.0%}" for k, lab in enumerate(DIST_LABELS))
        lines.append(f"  {who} inflige {total / n:.0f} PV par match : balles {stats[f'{who}_balles'] / total:.0%},"
                     f" missile {stats[f'{who}_missile'] / total:.0%} ; distance : {dist}")
        lines.append(f"     par match : {stats[f'{who}_rafales'] / n:.1f} rafales, {stats[f'{who}_missiles'] / n:.1f}"
                     f" missiles, {stats[f'{who}_boucliers'] / n:.1f} boucliers, {stats[f'{who}_dashs'] / n:.1f} dashs ;"
                     f" zone subie {stats[f'zone_{who}'] / n:.0f} PV")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("agent_a")
    parser.add_argument("agent_b")
    parser.add_argument("--games", type=int, default=20, help="nombre de matchs (chaque carte des deux côtés)")
    parser.add_argument("--seed0", type=int, default=2000)
    parser.add_argument("--rules", default=None, help="règles du match en JSON (voir bridge.datatypes.GameRules)")
    parser.add_argument("--port", type=int, default=6950)
    parser.add_argument("--json", default=None, help="fichier où écrire les statistiques brutes")
    args = parser.parse_args()

    rules = GameRules(**json.loads(args.rules)) if args.rules else GameRules()
    stats = Counter()
    process = launch_headless(args.port)
    start = time.monotonic()
    try:
        gym = BinaryGym(tcp_port=str(args.port))
        for k in range(args.games):
            play(gym, args.agent_a, args.agent_b, args.seed0 + k // 2, bool(k % 2), rules, stats)
        try:
            gym.end_training()
        except Exception:
            pass
    finally:
        process.kill()
    print(report(stats, args.agent_a, args.agent_b, args.rules))
    print(f"  ({time.monotonic() - start:.0f} s)")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"agent_a": args.agent_a, "agent_b": args.agent_b, "rules": args.rules, **stats}, f, indent=1)


if __name__ == "__main__":
    main()
