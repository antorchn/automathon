"""
Benchmark RL : entraîne un PPO from scratch sur plusieurs graines avec un protocole fixe, puis évalue tous
les checkpoints de chaque graine sur les mêmes cartes. Sert à comparer des versions du jeu ou du kit.

Usage (depuis PythonAI/, après `python tools/build.py headless`) :
    uv run python evaluation/benchmark.py NOM [--seeds 0 1 2] [--steps 3000000]
        [--rules '{"MatchDuration": 45}'] [--override ppo.learning_rate=1e-4 ...]

Sorties dans build/benchmarks/NOM/ (non versionné) :
    bench.json            protocole, commit, options
    headless/             copie du Headless utilisé (on peut recompiler le jeu pendant le benchmark)
    seedN/                logs, checkpoints, TensorBoard et évaluation de la graine N
    summary.json          taux de victoire par checkpoint et par graine, score final

Score : moyenne, sur les graines, du taux de victoire des 4 derniers checkpoints (75 % à 100 % de
l'entraînement). C'est plus stable qu'un checkpoint isolé.
"""

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PYTHONAI = Path(__file__).resolve().parent.parent
ROOT = PYTHONAI.parent

# Protocole de référence : les valeurs par défaut de rl_training.yaml depuis la version 0.5.0 (minibatch de 1 024),
# écrites ici en entier pour que le protocole ne change pas avec elles. Toute option
# peut être surchargée avec --override. Avant la 0.5.0 : minibatch de 512.
PROTOCOL = [
    "unity.num_envs=8", "unity.port_base=6200",
    "env.frame_skip=4", "env.random_side=true", "opponent=rush_bot",
    "ppo.learning_rate=3e-4", "ppo.n_steps=1024", "ppo.batch_size=1024", "ppo.n_epochs=10", "ppo.ent_coef=0.0",
    "policy_kwargs.net_arch=[64,64]",
    # Récompenses à l'échelle de 10 (une victoire, un vaisseau détruit)
    "reward.time_penalty=0.001", "reward.damage_dealt_coef=0.01", "reward.pain_factor=0.001",
    "reward.win_bonus=10", "reward.loss_penalty=10", "reward.draw_penalty=10",
    "save.checkpoint_every=250000",
    # Vitesse seulement, apprentissage inchangé (depuis le 15/11/2026, environ 1,8 fois plus rapide) : un seul
    # processus pour tous les Headless (protocole binaire, observations identiques au bit près) et 2 threads
    # PyTorch pour un réseau aussi petit
    "env.backend=vec", "torch_threads=2",
]
EVAL_OPPONENT, EVAL_MAPS, EVAL_WORKERS, EVAL_PORT_BASE = "rush_bot", 50, 6, 6500


def log(message: str):
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def host_rid() -> str:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    return f"{ {'win32': 'win', 'darwin': 'osx'}.get(sys.platform, 'linux') }-{arch}"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def option(overrides: list[str], key: str, default: str) -> str:
    """Valeur d'une option Hydra dans la liste (la dernière gagne)."""
    values = [o.split("=", 1)[1] for o in overrides if o.split("=", 1)[0].lstrip("+") == key]
    return values[-1] if values else default


def rules_overrides(rules: dict) -> list[str]:
    return [f"+env.rules.{k}={json.dumps(v)}" for k, v in rules.items()]


def summarize(bench_dir: Path, seeds: list[int]) -> dict:
    per_steps: dict[int, dict[int, float]] = {}
    for seed in seeds:
        path = bench_dir / f"seed{seed}" / "eval.json"
        if not path.exists():
            continue
        for r in json.loads(path.read_text(encoding="utf-8"))["results"]:
            if r["steps"] is not None:
                per_steps.setdefault(r["steps"], {})[seed] = r["win_rate"]
    steps = sorted(per_steps)
    last = steps[-4:]
    scores = {seed: statistics.mean(per_steps[s][seed] for s in last if seed in per_steps[s])
              for seed in seeds if all(seed in per_steps[s] for s in last)}
    summary = {
        "checkpoints": {s: per_steps[s] for s in steps},
        "score_per_seed": scores,
        "score": statistics.mean(scores.values()) if scores else None,
        "score_spread": [min(scores.values()), max(scores.values())] if scores else None,
    }
    (bench_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def print_summary(summary: dict, seeds: list[int]):
    print(f"\n{'steps':>10}  " + "  ".join(f"graine {s:>2}" for s in seeds) + "   moyenne")
    for steps, rates in summary["checkpoints"].items():
        cells = "  ".join(f"{rates[s]:>9.0%}" if s in rates else f"{'-':>9}" for s in seeds)
        print(f"{steps:>10,}  {cells}   {statistics.mean(rates.values()):>7.0%}".replace(",", " "))
    if summary["score"] is not None:
        low, high = summary["score_spread"]
        print(f"\nScore (moyenne des 4 derniers checkpoints) : {summary['score']:.1%} "
              f"(graines de {low:.0%} à {high:.0%})")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name")
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--steps", type=int, default=3_000_000)
    parser.add_argument("--rules", default=None, help="règles du match en JSON (voir bridge.datatypes.GameRules)")
    parser.add_argument("--override", nargs="*", default=[], help="options Hydra supplémentaires (après le protocole)")
    parser.add_argument("--summary-only", action="store_true", help="refait seulement le résumé")
    args = parser.parse_args()

    bench_dir = ROOT / "build" / "benchmarks" / args.name
    if args.summary_only:
        print_summary(summarize(bench_dir, args.seeds), args.seeds)
        return

    if bench_dir.exists():
        sys.exit(f"{bench_dir} existe déjà : choisissez un autre nom (ou supprimez le dossier).")
    rules = json.loads(args.rules) if args.rules else {}
    overrides = PROTOCOL + rules_overrides(rules) + args.override + [f"total_timesteps={args.steps}"]
    frame_skip = option(overrides, "env.frame_skip", "4")  # défaut de common.yaml
    backend = option(overrides, "env.backend", "json")
    eval_backend = "binary" if backend == "vec" else backend  # l'évaluation joue une carte à la fois

    source = ROOT / "build" / "headless" / host_rid()
    if not source.is_dir():
        sys.exit(f"{source} introuvable : lancez d'abord python tools/build.py headless")
    bench_dir.mkdir(parents=True)
    shutil.copytree(source, bench_dir / "headless")
    headless = bench_dir / "headless" / ("Headless.exe" if sys.platform == "win32" else "Headless")
    env = {**os.environ, "AUTOMATHON_HEADLESS_PATH": str(headless), "PYTHONUNBUFFERED": "1"}

    (bench_dir / "bench.json").write_text(json.dumps({
        "name": args.name, "seeds": args.seeds, "overrides": overrides, "rules": rules,
        "eval": {"opponent": EVAL_OPPONENT, "maps": EVAL_MAPS, "frame_skip": int(frame_skip)},
        "commit": git("rev-parse", "HEAD"), "uncommitted_changes": bool(git("status", "--porcelain")),
        "started": datetime.now().isoformat(timespec="seconds"),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Benchmark {args.name} : graines {args.seeds}, {args.steps:,} steps chacune".replace(",", " "))

    for seed in args.seeds:
        run_dir = bench_dir / f"seed{seed}"
        run_dir.mkdir()
        start = time.monotonic()
        log(f"graine {seed} : entraînement")
        with open(run_dir / "train.log", "w", encoding="utf-8") as out:
            code = subprocess.run(
                [sys.executable, "training/rl_training.py", *overrides, f"seed={seed}",
                 f"save.checkpoint_dir={run_dir / 'checkpoints'}", f"save.model={run_dir / 'final.zip'}",
                 f"save.tensorboard={run_dir / 'tensorboard'}", f"hydra.run.dir={run_dir / 'hydra'}"],
                cwd=PYTHONAI, env=env, stdout=out, stderr=subprocess.STDOUT).returncode
        if code != 0:
            sys.exit(f"Échec de l'entraînement de la graine {seed} (code {code}), voir {run_dir / 'train.log'}")
        log(f"graine {seed} : entraînement terminé en {(time.monotonic() - start) / 60:.0f} min, évaluation")

        eval_cmd = [sys.executable, "evaluation/evaluate_model.py", str(run_dir / "checkpoints"),
                    "--opponent", EVAL_OPPONENT, "--maps", str(EVAL_MAPS), "--frame-skip", frame_skip,
                    "--backend", eval_backend,
                    "--workers", str(EVAL_WORKERS), "--port-base", str(EVAL_PORT_BASE), "--json", str(run_dir / "eval.json")]
        if rules:
            eval_cmd += ["--rules", json.dumps(rules)]
        with open(run_dir / "eval.log", "w", encoding="utf-8") as out:
            code = subprocess.run(eval_cmd, cwd=PYTHONAI, env=env, stdout=out, stderr=subprocess.STDOUT).returncode
        if code != 0:
            sys.exit(f"Échec de l'évaluation de la graine {seed} (code {code}), voir {run_dir / 'eval.log'}")
        summary = summarize(bench_dir, args.seeds)
        seed_score = summary["score_per_seed"].get(seed)
        log(f"graine {seed} : évaluée, score {seed_score:.1%}" if seed_score is not None else f"graine {seed} : évaluée")

    print_summary(summarize(bench_dir, args.seeds), args.seeds)
    log("Benchmark terminé")


if __name__ == "__main__":
    main()
