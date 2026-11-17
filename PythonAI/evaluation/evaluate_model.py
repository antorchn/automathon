"""
Évalue des modèles PPO (fichiers .zip de stable-baselines3) contre un bot, sur un jeu fixe de cartes.

Chaque carte est jouée deux fois (départ à gauche, puis à droite), avec la politique déterministe.
Les cartes d'évaluation (graines 1 000 000 et suivantes) sont les mêmes pour tous les modèles :
les résultats sont directement comparables.

Usage (depuis PythonAI/) :
    uv run python evaluation/evaluate_model.py CHEMIN [CHEMIN ...] [--opponent rush_bot] [--maps 50]
        [--frame-skip 4] [--workers 4] [--backend binary] [--json resultats.json]

CHEMIN : un modèle .zip, un dossier de checkpoints (tous ses .zip, triés par nombre de steps),
ou « random » pour une politique aléatoire (point de comparaison).
--frame-skip vaut par défaut 4 : l'agent et l'adversaire décident une frame sur 4, exactement comme en match
(15 Hz, voir bridge.datatypes.DECISION_INTERVAL).
"""

import argparse
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EVAL_SEED_BASE = 1_000_000
OUTCOMES = ("Win", "Loss", "Draw", "Timeout")


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalle de confiance à 95 % d'une proportion (Wilson)."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, center - half), min(1.0, center + half)


def checkpoint_steps(path: str) -> int | None:
    match = re.search(r"_(\d+)_steps", os.path.basename(path))
    return int(match.group(1)) if match else None


def classify(info: dict, terminated: bool) -> str:
    outcome = info.get("outcome", "Ongoing")
    return outcome if terminated and outcome != "Ongoing" else "Timeout"


def evaluate(job: dict) -> dict:
    """Joue toutes les cartes d'évaluation pour un modèle (dans un processus, avec son propre Headless)."""
    import torch
    torch.set_num_threads(1)
    from stable_baselines3 import PPO
    from core.env.automathon_env import AutomathonEnv
    from core.env.fast_env import FastAutomathonEnv
    from agents.my_rl_agent.feature_extractor import extract_features
    from training.utils import launch_headless

    process = launch_headless(job["port"])
    env_class = FastAutomathonEnv if job["backend"] == "binary" else AutomathonEnv
    env = env_class(tcp_port=str(job["port"]), extractor_fn=extract_features, frame_skip=job["frame_skip"],
                        max_steps=job["max_frames"], rules=job["rules"])
    env.set_opponent_by_name(job["opponent"])
    model = None if job["model"] == "random" else PPO.load(job["model"], device="cpu")
    counts = dict.fromkeys(OUTCOMES, 0)
    start = time.monotonic()
    try:
        for i in range(job["maps"]):
            for self_on_right in (False, True):
                obs, _ = env.reset(options={"map_seed": EVAL_SEED_BASE + i, "self_on_right": self_on_right})
                while True:
                    if model is None:
                        action = env.action_space.sample()
                    else:
                        action, _ = model.predict(obs, deterministic=True)
                    obs, _, terminated, truncated, info = env.step(action)
                    if terminated or truncated:
                        break
                counts[classify(info, terminated)] += 1
    finally:
        env.game.end_training()
        process.kill()

    games = sum(counts.values())
    low, high = wilson_interval(counts["Win"], games)
    return {"model": job["model"], "steps": checkpoint_steps(job["model"]), "opponent": job["opponent"],
            "games": games, "counts": counts, "win_rate": counts["Win"] / games, "win_rate_ci95": [low, high],
            "seconds": round(time.monotonic() - start, 1)}


def expand(paths: list[str]) -> list[str]:
    models = []
    for path in paths:
        if path == "random":
            models.append(path)
        elif os.path.isdir(path):
            zips = [str(p) for p in Path(path).glob("*.zip")]
            models += sorted(zips, key=lambda p: (checkpoint_steps(p) is None, checkpoint_steps(p) or 0, p))
        else:
            models.append(path)
    return models


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--opponent", default="rush_bot", help="dossier du bot adverse dans agents/")
    parser.add_argument("--maps", type=int, default=50, help="nombre de cartes (chacune jouée des deux côtés)")
    parser.add_argument("--frame-skip", type=int, default=4,
                        help="frames par décision (défaut 4 : conditions d'un match, 15 Hz)")
    parser.add_argument("--max-frames", type=int, default=4200, help="durée maximale d'un match, en frames (un match finit avant)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--port-base", type=int, default=6400)
    parser.add_argument("--backend", choices=("json", "binary"), default="json",
                        help="protocole (binary : plus rapide, mêmes résultats avec l'extracteur par défaut)")
    parser.add_argument("--rules", default=None,
                        help='règles du match en JSON, ex. \'{"MatchDuration": 45}\' (voir bridge.datatypes.GameRules)')
    parser.add_argument("--json", default=None, help="fichier où écrire les résultats")
    args = parser.parse_args()

    models = expand(args.paths)
    rules = json.loads(args.rules) if args.rules else None
    jobs = [{"model": m, "opponent": args.opponent, "maps": args.maps, "frame_skip": args.frame_skip,
             "max_frames": args.max_frames, "rules": rules, "backend": args.backend, "port": args.port_base + i} for i, m in enumerate(models)]
    print(f"{len(jobs)} modèle(s), {2 * args.maps} matchs chacun contre {args.opponent}, frame_skip={args.frame_skip}")

    with ProcessPoolExecutor(max_workers=max(1, min(args.workers, len(jobs)))) as pool:
        results = list(pool.map(evaluate, jobs))

    print(f"\n{'modèle':<45} {'steps':>9} {'victoires':>22}  défaites  nuls  temps")
    for r in results:
        c, (low, high) = r["counts"], r["win_rate_ci95"]
        name = os.path.basename(r["model"])
        steps = "" if r["steps"] is None else f"{r['steps']:,}".replace(",", " ")
        print(f"{name:<45} {steps:>9} {r['win_rate']:>7.1%} [{low:.0%} ; {high:.0%}]  "
              f"{c['Loss']:>8} {c['Draw']:>5} {c['Timeout']:>6}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"args": vars(args), "results": results}, f, indent=2, ensure_ascii=False)
        print(f"\nRésultats : {args.json}")


if __name__ == "__main__":
    main()
