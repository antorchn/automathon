"""
Environnement Gymnasium rapide : même interface, mêmes options et mêmes valeurs qu'AutomathonEnv, mais avec le
protocole binaire (bridge.binary). Les observations, récompenses et fins d'épisode sont identiques à celles
d'AutomathonEnv pour les mêmes graines et actions.

Les observations viennent de l'extracteur de agents/my_rl_agent/feature_extractor.py (compilé avec Numba),
appliqué directement à l'état binaire : modifiez-le là, il sert aussi à AutomathonEnv et en match. Pour une
fonction Python quelconque (non compilable par Numba), utiliser AutomathonEnv (env.backend=json).
"""

import importlib

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from bridge.binary import BinaryGym, LazyGameState, RawState, to_game_state
from bridge.datatypes import DECISION_INTERVAL, AIAction, GameRules, GameState, ResetOptions
from agents.my_rl_agent.feature_extractor import (FEATURE_SIZE, extract_features_raw, flip_action,
                                                        starts_on_right_raw)

RESET_ATTEMPTS = 3
_ONE = 65536


def raw_reward(raw: RawState, frames: int = 1, time_penalty: float = 0.1, damage_dealt_coef: float = 1.0,
               pain_factor: float = 0.1, win_bonus: float = 1000.0, loss_penalty: float = 1000.0,
               draw_penalty: float = 1000.0) -> float:
    """Même calcul que core/env/reward_shaping.calculate_reward, sur l'état brut."""
    reward = -time_penalty * frames
    damage_dealt = raw.damage_dealt / _ONE
    if damage_dealt > 0:
        reward += damage_dealt * damage_dealt_coef
    damage_taken = raw.damage_taken / _ONE
    if damage_taken > 0:
        reward -= damage_taken * pain_factor
    if raw.outcome == "Win":
        reward += win_bonus
    elif raw.outcome == "Loss":
        reward -= loss_penalty
    elif raw.outcome == "Draw":
        reward -= draw_penalty
    return float(reward)


class FastAutomathonEnv(gym.Env):
    """Voir AutomathonEnv pour la signification des options (identiques)."""

    def __init__(self, tcp_port: str = "5555", max_steps: int = 4200, extractor_fn=None, tcp_timeout: int | None = 500,
                 reward_kwargs: dict | None = None, frame_skip: int = DECISION_INTERVAL, map_density: float | None = None,
                 random_side: bool = False, rules: dict | None = None, canonical_side: bool = True,
                 observation_size: int | None = None):
        super().__init__()
        if extractor_fn is not None and getattr(extractor_fn, "__name__", "") != "extract_features":
            raise ValueError("FastAutomathonEnv utilise l'extracteur de feature_extractor.py (modifiez-le "
                             "directement) : pour une autre fonction, prenez AutomathonEnv (env.backend=json).")
        self.game = BinaryGym(tcp_port=tcp_port)
        self.tcp_timeout = tcp_timeout
        self.max_steps = max_steps
        self.frame_skip = max(1, int(frame_skip))
        self.map_density = map_density
        self.random_side = random_side
        self.rules = GameRules(**rules) if rules else None
        self.reward_kwargs = reward_kwargs or {}
        self.canonical_side = canonical_side
        self._flip = False
        self._frames_since_reset = 0
        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(8,), dtype=np.float32)
        self.observation_space = spaces.Box(low=-1.0, high=1.0, shape=(observation_size or FEATURE_SIZE,),
                                            dtype=np.float32)
        try:
            self.opponent_policy = importlib.import_module("agents.rush_bot.agent").ParticipantAgent()
        except Exception:
            self.opponent_policy = None
        self._raw: RawState | None = None
        self.current_step = 0

    @property
    def _last_state(self) -> GameState | None:
        """Dernier état en GameState (construit à la demande), pour les scripts qui lisent env._last_state."""
        return to_game_state(self._raw) if self._raw is not None else None

    def set_opponent(self, opponent_bot):
        self.opponent_policy = opponent_bot

    def set_opponent_by_name(self, folder_name: str):
        try:
            self.opponent_policy = importlib.import_module(f"agents.{folder_name}.agent").ParticipantAgent()
        except Exception as e:
            print(f"Erreur de chargement de l'adversaire {folder_name} : {e}")

    def set_current_step(self, step: int):
        self.current_step = step

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        options = options or {}
        map_seed = options.get("map_seed", int(self.np_random.integers(0, 2**31 - 1)))
        self_on_right = options.get("self_on_right", self.random_side and bool(self.np_random.integers(2)))
        reset_options = ResetOptions(Seed=map_seed, MapDensity=options.get("map_density", self.map_density),
                                     SelfOnRight=self_on_right, Rules=self.rules)
        for attempt in range(1, RESET_ATTEMPTS + 1):
            try:
                raw = self.game.reset(reset_options, tcp_connection_timeout=self.tcp_timeout)
                break
            except TimeoutError:
                if attempt == RESET_ATTEMPTS:
                    raise
        self._raw = raw
        self._frames_since_reset = 0
        self._flip = self.canonical_side and starts_on_right_raw(raw)
        info = {"map_seed": map_seed, "self_on_right": self_on_right, "flipped": self._flip}
        return extract_features_raw(raw, flip=self._flip), info

    def step(self, action):
        if not isinstance(action, AIAction) and self._flip:
            action = flip_action(action)  # l'agent raisonne dans le repère tourné de 180 degrés
        enemy_action = self.opponent_policy.get_action(LazyGameState(self._raw, swap=True))

        try:
            raw = self.game.step(action, enemy_action, frames=self.frame_skip, tcp_connection_timeout=self.tcp_timeout)
        except TimeoutError:
            self._frames_since_reset += self.frame_skip
            info = {"timeout": False, "tcp_timeout": True}
            return extract_features_raw(self._raw, flip=self._flip), 0.0, False, True, info

        obs = extract_features_raw(raw, flip=self._flip)
        reward = raw_reward(raw, frames=self.frame_skip, **self.reward_kwargs)
        self._raw = raw

        terminated = raw.done
        self._frames_since_reset += self.frame_skip
        truncated = not terminated and self._frames_since_reset >= self.max_steps
        info = {"timeout": truncated, "tcp_timeout": False, "outcome": raw.outcome}
        return obs, reward, terminated, truncated, info
