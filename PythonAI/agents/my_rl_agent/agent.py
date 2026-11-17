import os
import sys
import numpy as np

# Ajouter le dossier parent au path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bridge.datatypes import GameState, AIAction, FixedVector2
from .feature_extractor import extract_features, flip_action, starts_on_right, warmup

class ParticipantAgent:
    """
    Modèle d'agent RL : joue un modèle PPO (stable-baselines3) entraîné avec training/rl_training.py, avec
    l'extracteur de features de ce dossier. Le kit ne fournit pas de modèle entraîné : c'est le vôtre.

    Le modèle est cherché d'abord dans ce dossier (model.zip), puis dans training/rl_model.zip (là où
    l'entraînement l'écrit). Pour rendre votre agent, copiez-le en model.zip dans son dossier : le dossier se
    suffit alors à lui-même (code, extracteur, modèle).
    """

    MODEL_FILE = "model.zip"

    def __init__(self):
        try:
            from stable_baselines3 import PPO
        except ImportError:
            print("Veuillez installer stable-baselines3 et torch pour utiliser cet agent.")
            sys.exit(1)

        agent_dir = os.path.dirname(os.path.abspath(__file__))
        python_ai_dir = os.path.dirname(os.path.dirname(agent_dir))
        candidates = [os.path.join(agent_dir, self.MODEL_FILE), os.path.join(python_ai_dir, "training", "rl_model.zip")]
        model_path = next((path for path in candidates if os.path.exists(path)), None)
        if model_path is None:
            print("Erreur : aucun modèle trouvé (" + " ni ".join(candidates) + ").")
            print("Entraînez d'abord un modèle : uv run python training/rl_training.py")
            sys.exit(1)

        # On charge le modèle sans l'environnement
        self.model = PPO.load(model_path, device="cpu")
        print(f"Modèle chargé : {model_path}")
        # Extracteur compilé maintenant (Numba), et non pendant la première frame du match
        warmup()

    def get_action(self, state: GameState) -> AIAction:
        # 1. Extraction des features, dans le repere tourne de 180 degres si on part a droite
        #    (comme a l'entrainement, voir canonical_side dans core/env/automathon_env.py)
        flip = starts_on_right(state)
        obs = extract_features(state, flip=flip)
        
        # 2. Inférence (prediction déterministe)
        action_np, _states = self.model.predict(obs, deterministic=True)
        if flip:
            action_np = flip_action(action_np)
        
        # 3. Conversion du vecteur numpy (8 dimensions) vers AIAction
        action = AIAction(
            MovingDirection=FixedVector2(float(action_np[0]), float(action_np[1])),
            AimingDirection=FixedVector2(float(action_np[2]), float(action_np[3])),
            MachineGun=bool(action_np[4] > 0.0),
            Missile=bool(action_np[5] > 0.0),
            Shield=bool(action_np[6] > 0.0),
            Dash=bool(action_np[7] > 0.0),
        )
        
        return action
