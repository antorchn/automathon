import inspect

import gymnasium as gym
from gymnasium import spaces
import numpy as np

from bridge.gym import Gym
from bridge.datatypes import DECISION_INTERVAL, AIAction, GameState, FixedVector2, GameRules, ResetOptions
from .reward_shaping import calculate_reward
from agents.my_rl_agent.feature_extractor import FEATURE_SIZE, extract_features, flip_action, starts_on_right

# Tentatives de reset avant d'abandonner (chacune attend au plus tcp_timeout)
RESET_ATTEMPTS = 3

_OPPOSITE_OUTCOME = {"Win": "Loss", "Loss": "Win"}


def enemy_view(state: GameState) -> GameState:
    """Le meme etat vu par l'adversaire : vaisseaux, degats et resultat intervertis."""
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

class AutomathonEnv(gym.Env):
    """
    Wrapper Gymnasium complet pour l'entraînement RL sur Automathon.
    """
    
    def __init__(self, tcp_port: str = "5555", max_steps: int = 4200, extractor_fn=None, tcp_timeout: int | None = 500,
                 reward_kwargs: dict | None = None, frame_skip: int = DECISION_INTERVAL, map_density: float | None = None,
                 random_side: bool = False, rules: dict | None = None, canonical_side: bool = True,
                 observation_size: int | None = None):
        """
        max_steps   : duree maximale d'un episode, en frames (4200 = 70 s de jeu ; avec les regles du jeu, un match
                      finit avant).
        frame_skip  : frames jouees par step avec la meme action (repetition d'action), pour l'agent et l'adversaire.
                      Par defaut DECISION_INTERVAL (4) : comme en match, ou les agents decident a 15 Hz.
        map_density : densite de murs des cartes (0 = arene vide, None = densite par defaut du jeu).
        random_side : tire au hasard le cote de depart de l'agent a chaque episode (sinon toujours a gauche).
        rules       : regles du match, ex. {"MatchDuration": 45, "MaxActiveShields": 1} (voir bridge.datatypes.GameRules).
                      None = regles par defaut du jeu.
        canonical_side : quand l'agent part a droite, observations et actions sont tournees de 180 degres
                      (extract_features(state, flip=True)) : il se voit toujours parti a gauche. Sans effet si
                      l'extracteur n'a pas de parametre flip.
        observation_size : taille du vecteur d'observation (defaut : FEATURE_SIZE de l'extracteur fourni).
        Les cartes sont tirees avec le generateur de l'environnement : reproductibles si on passe seed a reset().
        """
        super().__init__()
        self.game = Gym(tcp_port=tcp_port)
        self.tcp_timeout = tcp_timeout
        self.max_steps = max_steps
        self.frame_skip = max(1, int(frame_skip))
        self.map_density = map_density
        self.random_side = random_side
        self.rules = GameRules(**rules) if rules else None
        # Poids de calculate_reward (section `reward` de training/conf/rl_training.yaml)
        self.reward_kwargs = reward_kwargs or {}
        self._frames_since_reset = 0
        
        self.extractor_fn = extractor_fn or extract_features
        self.canonical_side = canonical_side and "flip" in inspect.signature(self.extractor_fn).parameters
        self._flip = False

        # Espace d'action continu : 8 valeurs [-1, 1]
        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(8,), dtype=np.float32)

        # Espace d'observation : vecteur de features dans [-1, 1]
        size = observation_size or getattr(inspect.getmodule(self.extractor_fn), "FEATURE_SIZE", FEATURE_SIZE)
        self.observation_space = spaces.Box(low=-1.0, high=1.0, shape=(size,), dtype=np.float32)
        
        # Adversaire par défaut
        import importlib
        try:
            mod = importlib.import_module("agents.rush_bot.agent")
            self.opponent_policy = mod.ParticipantAgent()
        except Exception:
            self.opponent_policy = None
        self._last_state = None
        self.current_step = 0

    def _observe(self, state: GameState) -> np.ndarray:
        return self.extractor_fn(state, flip=self._flip) if self.canonical_side else self.extractor_fn(state)

    def set_opponent(self, opponent_bot):
        """Change l'adversaire (un objet avec get_action(state), par exemple une copie de votre agent)."""
        self.opponent_policy = opponent_bot

    def set_opponent_by_name(self, folder_name: str):
        import importlib
        try:
            mod = importlib.import_module(f"agents.{folder_name}.agent")
            self.opponent_policy = mod.ParticipantAgent()
        except Exception as e:
            print(f"Erreur de chargement de l'adversaire {folder_name} : {e}")

    def set_current_step(self, step: int):
        """Permet au callback de synchroniser le step actuel pour le calcul des récompenses dynamiques."""
        self.current_step = step

    def reset(self, seed=None, options=None):
        """options (facultatif) : {"map_seed": int, "map_density": float, "self_on_right": bool}."""
        super().reset(seed=seed)
        options = options or {}

        # 1. Options du match : graine de carte tiree du generateur de l'env (reproductible si seed est fourni)
        map_seed = options.get("map_seed", int(self.np_random.integers(0, 2**31 - 1)))
        self_on_right = options.get("self_on_right", self.random_side and bool(self.np_random.integers(2)))
        reset_options = ResetOptions(Seed=map_seed, MapDensity=options.get("map_density", self.map_density),
                                     SelfOnRight=self_on_right, Rules=self.rules)

        # 2. Reset the underlying environment (nouvelles tentatives si le Headless est momentanément lent :
        #    le bridge rouvre sa socket après chaque délai dépassé)
        for attempt in range(1, RESET_ATTEMPTS + 1):
            try:
                game_state = self.game.reset(tcp_connection_timeout=self.tcp_timeout, options=reset_options)
                break
            except TimeoutError:
                if attempt == RESET_ATTEMPTS:
                    raise
        self._last_state = game_state
        self._frames_since_reset = 0
        self._flip = self.canonical_side and starts_on_right(game_state)

        # 3. Extract features (tournees de 180 degres si l'agent part a droite, voir canonical_side)
        obs = self._observe(game_state)

        # 4. Return (obs, info)
        info = {"map_seed": map_seed, "self_on_right": self_on_right, "flipped": self._flip}
        return obs, info

    def step(self, action):
        if isinstance(action, AIAction):
            self_action = action
        else:
            if self._flip:
                action = flip_action(action)  # l'agent raisonne dans le repere tourne de 180 degres
            # 1. Convertir l'action numpy en objet AIAction
            self_action = AIAction(
                MovingDirection=FixedVector2(float(action[0]), float(action[1])),
                AimingDirection=FixedVector2(float(action[2]), float(action[3])),
                MachineGun=bool(action[4] > 0.0),
                Missile=bool(action[5] > 0.0),
                Shield=bool(action[6] > 0.0),
                Dash=bool(action[7] > 0.0),
            )
        
        # Les murs et limites de l'arène sont gérés directement par le moteur Unity.
        # Aucun clamping Python nécessaire.
        # 2. Obtenir l'action de l'adversaire
        # (Note : l'adversaire voit le state tel qu'il est, mais de base les bots heuristiques
        # supposent qu'ils sont le SelfShip. Il faut intervertir Self et Enemy pour l'adversaire !)

        enemy_action = self.opponent_policy.get_action(enemy_view(self._last_state))
        
        # Les murs et limites de l'arène sont gérés directement par le moteur Unity.
        # Aucun clamping Python nécessaire pour l'adversaire.
        
        # 3. Step in the game
        try:
            next_state = self.game.step(self_action, enemy_action, tcp_connection_timeout=self.tcp_timeout,
                                        frames=self.frame_skip)
        except TimeoutError:
            # Headless trop lent ou planté : l'épisode est interrompu (tronqué, sans récompense de fin de
            # match, qui serait fausse) et le prochain reset en relance un. Le bridge a rouvert sa socket.
            self._frames_since_reset += self.frame_skip
            info = {"timeout": False, "tcp_timeout": True}
            return self._observe(self._last_state), 0.0, False, True, info

        # 4. Extract new observation
        obs = self._observe(next_state)
        
        # 5. Calculate reward
        reward = calculate_reward(self._last_state, next_state, self_action, self.current_step,
                                  frames=self.frame_skip, **self.reward_kwargs)
        
        self._last_state = next_state

        # 6. Check termination
        terminated = next_state.Done
        self._frames_since_reset += self.frame_skip
        truncated = not terminated and self._frames_since_reset >= self.max_steps
        info = {
            "timeout": truncated,
            "tcp_timeout": False,
            "outcome": next_state.Outcome,
        }

        return obs, reward, terminated, truncated, info
