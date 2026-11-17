import os
import sys
from collections import deque

import hydra
from omegaconf import DictConfig, OmegaConf
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback, CheckpointCallback
from stable_baselines3.common.vec_env import SubprocVecEnv, VecMonitor

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.env.automathon_env import AutomathonEnv
from core.env.fast_env import FastAutomathonEnv
from core.env.vec_env import AutomathonVecEnv
from agents.my_rl_agent.feature_extractor import extract_features
from training.utils import (
    launch_headless, get_port_base, get_checkpoint_dir, get_num_envs, get_startup_wait,
    resolve_training_path, register_config_resolvers,
)

# Hyperparametres : training/conf/rl_training.yaml (surchargeables en ligne de commande).
register_config_resolvers()


class OutcomeStatsCallback(BaseCallback):
    """
    Journalise l'issue des `window` derniers épisodes d'entraînement (TensorBoard et console) :
    rollout/win_rate, loss_rate, draw_rate (destruction simultanée) et timeout_rate (temps écoulé).
    """
    OUTCOMES = ("Win", "Loss", "Draw", "Timeout")

    def __init__(self, window: int = 100):
        super().__init__()
        self.outcomes = deque(maxlen=window)

    def _on_step(self) -> bool:
        finished = False
        for done, info in zip(self.locals["dones"], self.locals["infos"]):
            if not done or info.get("tcp_timeout"):
                continue
            if info.get("outcome", "Ongoing") == "Ongoing":
                self.outcomes.append("Timeout")
            else:
                self.outcomes.append(info["outcome"])
            finished = True
        if finished:
            total = len(self.outcomes)
            for outcome in self.OUTCOMES:
                self.logger.record(f"rollout/{outcome.lower()}_rate", self.outcomes.count(outcome) / total)
        return True


ENV_BACKENDS = {"json": AutomathonEnv, "binary": FastAutomathonEnv}


def make_env(port: int, env_kwargs: dict, startup_wait: float, backend: str = "json"):
    """
    Fonction constructeur pour SubprocVecEnv.
    Lance une instance Unity Headless unique sur un port unique.
    Compatible Windows, Linux (Colab, Databricks) et macOS.
    backend : "json" (AutomathonEnv) ou "binary" (FastAutomathonEnv, mêmes valeurs, plus rapide).
    """
    if backend not in ENV_BACKENDS:
        raise ValueError(f"env.backend inconnu pour un env par processus : {backend} (json ou binary)")

    def _init():
        print(f"Lancement de Unity sur le port {port}...")
        process = launch_headless(port, timeout=startup_wait)

        env = ENV_BACKENDS[backend](tcp_port=str(port), extractor_fn=extract_features, **env_kwargs)

        # Monkey patch du close() pour tuer le processus Unity quand le VecEnv se ferme
        original_close = env.close
        def new_close():
            for fn in [original_close, env.game.end_training, process.kill]:
                try:
                    fn()
                except Exception:
                    pass
        env.close = new_close

        return env
    return _init


def make_training_env(backend: str, num_envs: int, start_port: int, env_kwargs: dict, startup_wait: float):
    """
    Environnements d'entraînement, dans un VecMonitor (récompense et durée des épisodes dans les logs) :
    - json, binary : un processus Python par environnement (SubprocVecEnv), chacun avec son Headless ;
    - vec : un seul processus pilote tous les Headless (AutomathonVecEnv), mêmes valeurs que binary, plus rapide.
    """
    if backend == "vec":
        env = AutomathonVecEnv(range(start_port, start_port + num_envs), extractor_fn=extract_features,
                               startup_wait=startup_wait, **env_kwargs)
    else:
        env = SubprocVecEnv([make_env(start_port + i, env_kwargs, startup_wait, backend) for i in range(num_envs)])
    return VecMonitor(env)


def train(cfg: DictConfig):
    """Entraînement PPO. Utilisable aussi depuis un notebook avec hydra.compose()."""
    print(OmegaConf.to_yaml(cfg))
    if cfg.get("torch_threads"):
        import torch
        torch.set_num_threads(cfg.torch_threads)

    num_envs = cfg.unity.num_envs or get_num_envs()
    start_port = cfg.unity.port_base or get_port_base()
    startup_wait = cfg.unity.startup_wait or get_startup_wait()
    env_kwargs = {
        "max_steps": cfg.env.max_steps,
        "tcp_timeout": cfg.env.tcp_timeout,
        "frame_skip": cfg.env.frame_skip,
        "map_density": cfg.env.map_density,
        "random_side": cfg.env.random_side,
        "canonical_side": cfg.env.get("canonical_side", True),
        "rules": OmegaConf.to_container(cfg.env.rules) if cfg.env.get("rules") else None,
        "reward_kwargs": OmegaConf.to_container(cfg.reward),
    }

    backend = cfg.env.get("backend", "json")
    print(f"Nombre d'envs    : {num_envs} (backend {backend})")
    print(f"Ports            : {start_port} -> {start_port + num_envs - 1}")
    print(f"Création de {num_envs} environnements en parallèle...")

    env = make_training_env(backend, num_envs, start_port, env_kwargs, startup_wait)
    # Adversaire de tous les environnements : un dossier de agents/. Pour un adversaire quelconque (par exemple une
    # copie de votre agent) : env.env_method("set_opponent", objet avec get_action(state))
    env.env_method("set_opponent_by_name", cfg.opponent)
    print(f"Adversaire       : {cfg.opponent}")

    ppo_kwargs = OmegaConf.to_container(cfg.ppo)
    tensorboard_log = resolve_training_path(cfg.save.tensorboard) if cfg.save.tensorboard else None
    init_model_path = resolve_training_path(cfg.init.model) if cfg.init.model else None

    if init_model_path:
        # Reprise d'un entraînement (ex : un checkpoint) : poids et architecture viennent du modèle.
        if not os.path.exists(init_model_path):
            raise FileNotFoundError(f"init.model introuvable : {init_model_path}")
        print(f"Reprise de l'entraînement depuis : {init_model_path}")
        model = PPO.load(init_model_path, env=env, device=cfg.device, tensorboard_log=tensorboard_log, **ppo_kwargs)
        if cfg.seed is not None:
            model.set_random_seed(cfg.seed)
    else:
        model = PPO(
            "MlpPolicy", env, device=cfg.device, seed=cfg.seed, tensorboard_log=tensorboard_log,
            policy_kwargs=OmegaConf.to_container(cfg.policy_kwargs), **ppo_kwargs,
        )

    # Checkpoint périodique — critique sur Databricks pour protéger contre les interruptions.
    # Configurable via save.checkpoint_dir ou AUTOMATHON_CHECKPOINT_DIR (ex: /dbfs/FileStore/automathon/mon_equipe)
    checkpoint_dir = resolve_training_path(cfg.save.checkpoint_dir) if cfg.save.checkpoint_dir else get_checkpoint_dir()
    os.makedirs(checkpoint_dir, exist_ok=True)
    print(f"Checkpoints      : {checkpoint_dir}")

    checkpoint_callback = CheckpointCallback(
        save_freq=max(cfg.save.checkpoint_every // num_envs, 1),
        save_path=checkpoint_dir,
        name_prefix="rl_model_checkpoint",
        verbose=1
    )

    try:
        print(f"\nDébut du Reinforcement Learning sur {num_envs} cœurs...")
        model.learn(total_timesteps=cfg.total_timesteps,
                    callback=[checkpoint_callback, OutcomeStatsCallback()])

        save_path = resolve_training_path(cfg.save.model)
        model.save(save_path)
        print(f"Entraînement terminé. Modèle sauvegardé : {save_path}")
    except KeyboardInterrupt:
        print("\nEntraînement interrompu. Sauvegarde de sécurité...")
        save_path = resolve_training_path(cfg.save.interrupted)
        model.save(save_path)
        print(f"Modèle sauvegardé : {save_path}")
    finally:
        print("Fermeture de tous les environnements Unity...")
        env.close()


@hydra.main(version_base="1.3", config_path="conf", config_name="rl_training")
def main(cfg: DictConfig):
    train(cfg)


if __name__ == "__main__":
    main()
