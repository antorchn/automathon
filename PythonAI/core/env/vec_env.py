"""
Environnement vectorisé à processus unique (env.backend=vec) : un seul processus Python pilote directement tous
les Headless avec le protocole binaire, sans SubprocVecEnv ni un processus Python par environnement.

À chaque step, les requêtes partent vers tous les Headless, qui simulent en parallèle. Une fois les réponses
reçues, une seule fonction compilée avec Numba les traite toutes : lecture des états, features
(agents/my_rl_agent/feature_extractor.py), récompense et fin de match. Les adversaires qui
ont une version Numba (agents/<bot>/fast.py, fonction get_action_raw) sont calculés de la même façon, en un appel
pour tous les environnements.

Observations, récompenses, fins d'épisode et infos sont identiques à celles de
SubprocVecEnv([FastAutomathonEnv(...), ...]) pour les mêmes graines et actions (même tirage des cartes et des
côtés, même reset automatique, mêmes infos « terminal_observation » et « TimeLimit.truncated »).

Un adversaire sans version Numba reste utilisable : son agent.py est appelé dans ce processus, état par état,
ce qui ralentit tout l'entraînement. Dans ce cas, env.backend=binary (un processus par environnement) est plus rapide.
"""

import importlib
import inspect
import json
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import zmq
from gymnasium import spaces
from gymnasium.utils import seeding
from numba import njit
from stable_baselines3.common.vec_env.base_vec_env import VecEnv

from agents.my_rl_agent.feature_extractor import (FEATURE_SIZE, _features, extract_features_raw,
                                                        starts_on_right_raw)
from bridge.binary import (BULLET_SIZE, HEADER_SIZE, MISSILE_SIZE, OUTCOMES, SHIELD_SIZE,
                           STATE_VERSION, STEP_TAG,
                           SHIP_SIZE, WALL_SIZE, LazyGameState, RawState, encode_action)
from bridge.datatypes import DECISION_INTERVAL, GameRules, ResetOptions
from .fast_env import RESET_ATTEMPTS, raw_reward

_ONE = 65536.0
_INT32_MIN, _INT32_MAX = -2**31, 2**31 - 1
# Poids de la récompense : valeurs par défaut de raw_reward (identique à reward_shaping.calculate_reward)
_REWARD_DEFAULTS = {name: p.default for name, p in inspect.signature(raw_reward).parameters.items()
                    if p.default is not inspect.Parameter.empty and name != "frames"}
_REWARD_ORDER = ("time_penalty", "damage_dealt_coef", "pain_factor", "win_bonus", "loss_penalty", "draw_penalty")


# --------------------------------------------------------------------------- calculs groupés (Numba)

@njit(cache=True)
def _views(ints, start):
    """Tableaux d'un état binaire qui commence à l'entier `start` de `ints` (comme RawState)."""
    nb, nm = np.int64(ints[start + 6]), np.int64(ints[start + 7])
    ns, nw = np.int64(ints[start + 8]), np.int64(ints[start + 9])
    self_ship = ints[start + HEADER_SIZE:start + HEADER_SIZE + SHIP_SIZE]
    enemy_ship = ints[start + HEADER_SIZE + SHIP_SIZE:start + HEADER_SIZE + 2 * SHIP_SIZE]
    p = start + HEADER_SIZE + 2 * SHIP_SIZE
    bullets = ints[p:p + nb * BULLET_SIZE].reshape((nb, BULLET_SIZE))
    p += nb * BULLET_SIZE
    missiles = ints[p:p + nm * MISSILE_SIZE].reshape((nm, MISSILE_SIZE))
    p += nm * MISSILE_SIZE
    shields = ints[p:p + ns * SHIELD_SIZE].reshape((ns, SHIELD_SIZE))
    p += ns * SHIELD_SIZE
    walls = ints[p:p + nw * WALL_SIZE].reshape((nw, WALL_SIZE))
    return self_ship, enemy_ship, bullets, missiles, shields, walls


@njit(cache=True)
def _process_steps(ints, offsets, flips, frames, weights, obs, rewards, done, outcomes):
    """
    Traite les états reçus de tous les environnements : observation, récompense (comme fast_env.raw_reward, dans le
    même ordre d'opérations), fin de match et code du résultat (-1 si la version de l'état est inattendue).
    """
    time_penalty, dealt_coef, pain_factor, win_bonus, loss_penalty, draw_penalty = (
        weights[0], weights[1], weights[2], weights[3], weights[4], weights[5])
    for i in range(offsets.shape[0]):
        start = offsets[i]
        if ints[start] != STATE_VERSION:
            outcomes[i] = -1
            continue
        self_ship, enemy_ship, bullets, missiles, shields, walls = _views(ints, start)
        obs[i, :] = _features(ints[start:start + HEADER_SIZE], self_ship, enemy_ship, bullets, missiles, shields,
                              walls, flips[i])
        outcome = ints[start + 2]
        reward = -time_penalty * frames
        damage_dealt = ints[start + 4] / _ONE
        if damage_dealt > 0:
            reward += damage_dealt * dealt_coef
        damage_taken = ints[start + 5] / _ONE
        if damage_taken > 0:
            reward -= damage_taken * pain_factor
        if outcome == 1:
            reward += win_bonus
        elif outcome == 2:
            reward -= loss_penalty
        elif outcome == 3:
            reward -= draw_penalty
        rewards[i] = reward
        done[i] = ints[start + 1] != 0
        outcomes[i] = outcome


_OPPONENT_KERNELS: dict = {}


def _opponent_kernel(fast_fn):
    """Fonction Numba qui applique le bot `fast_fn` (get_action_raw) à plusieurs environnements en un appel."""
    kernel = _OPPONENT_KERNELS.get(fast_fn)
    if kernel is None:
        @njit
        def kernel(ints, offsets, indices, out):
            for k in range(indices.shape[0]):
                i = indices[k]
                self_ship, enemy_ship, bullets, missiles, shields, walls = _views(ints, offsets[i])
                # Point de vue de l'adversaire : vaisseaux intervertis
                out[i, :] = fast_fn(enemy_ship, self_ship, bullets, missiles, shields, walls)
        _OPPONENT_KERNELS[fast_fn] = kernel
    return kernel


def _raw_actions(actions: np.ndarray, flip: np.ndarray) -> np.ndarray:
    """
    Actions de l'agent (N x 8 floats) -> N x 5 entiers du protocole binaire, comme flip_action puis encode_action
    pour chaque environnement : déplacement et visée en Fixed bruts (arrondi loin de zéro), boutons si > 0.
    """
    actions = np.asarray(actions)
    values = actions[:, :4].astype(np.float64)
    buttons = actions[:, 4:8] > 0.0
    if flip.any():
        flipped = actions[flip].astype(np.float32)
        values[flip] = -flipped[:, :4].astype(np.float64)
        buttons[flip] = flipped[:, 4:8] > 0.0
    scaled = values * _ONE
    raw = np.clip(np.trunc(scaled + np.copysign(0.5, scaled)), _INT32_MIN, _INT32_MAX).astype(np.int64)
    out = np.empty((len(actions), 5), dtype=np.int64)
    out[:, :4] = raw
    out[:, 4] = buttons @ np.array([1, 2, 4, 8])
    return out


# --------------------------------------------------------------------------- adversaires

class _Opponent:
    """Adversaire d'un environnement : version Numba (fast.py) si elle existe, sinon l'agent Python."""

    def __init__(self, fast_fn=None, agent=None, name: str | None = None):
        self.fast_fn, self.agent, self.name = fast_fn, agent, name

    @classmethod
    def by_name(cls, folder: str) -> "_Opponent":
        try:
            fast_fn = importlib.import_module(f"agents.{folder}.fast").get_action_raw
            return cls(fast_fn=fast_fn, name=folder)
        except (ImportError, AttributeError):
            pass
        return cls(agent=importlib.import_module(f"agents.{folder}.agent").ParticipantAgent(), name=folder)


class _Slot:
    """Ce que FastAutomathonEnv garde pour un environnement (accessible par env_method et get_attr)."""

    render_mode = None

    def __init__(self):
        self.opponent = _Opponent.by_name("rush_bot")
        self.current_step = 0

    def set_opponent(self, opponent_bot):
        self.opponent = _Opponent(fast_fn=getattr(opponent_bot, "get_action_raw", None),
                                  agent=None if hasattr(opponent_bot, "get_action_raw") else opponent_bot)

    def set_opponent_by_name(self, folder_name: str):
        try:
            self.opponent = _Opponent.by_name(folder_name)
        except Exception as e:
            print(f"Erreur de chargement de l'adversaire {folder_name} : {e}")

    def set_current_step(self, step: int):
        self.current_step = step


# --------------------------------------------------------------------------- environnement

class AutomathonVecEnv(VecEnv):
    """
    `ports` : un Headless par port, lancé ici (launch=True) ou déjà lancé. Les autres options sont celles
    d'AutomathonEnv et de FastAutomathonEnv (voir core/env/automathon_env.py).
    """

    def __init__(self, ports, max_steps: int = 4200, extractor_fn=None, tcp_timeout: int | None = 500,
                 reward_kwargs: dict | None = None, frame_skip: int = DECISION_INTERVAL, map_density: float | None = None,
                 random_side: bool = False, rules: dict | None = None, canonical_side: bool = True,
                 observation_size: int | None = None, launch: bool = True, startup_wait: float | None = None):
        if extractor_fn is not None and getattr(extractor_fn, "__name__", "") != "extract_features":
            raise ValueError("AutomathonVecEnv utilise l'extracteur de feature_extractor.py (modifiez-le "
                             "directement) : pour une autre fonction, prenez AutomathonEnv (env.backend=json).")
        self.ports = [int(p) for p in ports]
        n = len(self.ports)
        self.max_steps = max_steps
        self.tcp_timeout = tcp_timeout
        self.frame_skip = max(1, int(frame_skip))
        self.map_density = map_density
        self.random_side = random_side
        self.rules = GameRules(**rules) if rules else None
        self.canonical_side = canonical_side
        weights = {**_REWARD_DEFAULTS, **(reward_kwargs or {})}
        unknown = set(weights) - set(_REWARD_ORDER)
        if unknown:
            raise TypeError(f"Poids de récompense inconnus : {sorted(unknown)}")
        self._weights = np.array([float(weights[k]) for k in _REWARD_ORDER])
        self.slots = [_Slot() for _ in range(n)]

        self.processes = []
        if launch:
            from training.utils import launch_headless
            with ThreadPoolExecutor(max_workers=min(n, 16)) as pool:
                self.processes = list(pool.map(lambda port: launch_headless(port, timeout=startup_wait), self.ports))

        self.context = zmq.Context()
        self.poller = zmq.Poller()
        self.sockets: list = [None] * n
        self._index: dict = {}
        for i in range(n):
            self._connect(i)

        self._data: list[bytes | None] = [None] * n  # dernier état binaire reçu de chaque environnement
        self._flip = np.zeros(n, dtype=bool)
        self._frames = [0] * n
        self._rngs: list[np.random.Generator | None] = [None] * n
        self._reset_message: list[str | None] = [None] * n
        self._reset_info: list[dict] = [{} for _ in range(n)]
        self.closed = False

        observation_space = spaces.Box(low=-1.0, high=1.0, shape=(observation_size or FEATURE_SIZE,), dtype=np.float32)
        action_space = spaces.Box(low=-1.0, high=1.0, shape=(8,), dtype=np.float32)
        super().__init__(n, observation_space, action_space)

    # ------------------------------------------------------------------ connexions

    def _connect(self, i: int):
        """(Re)crée la socket REQ de l'environnement i : après un délai dépassé, l'ancienne est inutilisable."""
        old = self.sockets[i]
        if old is not None:
            self.poller.unregister(old)
            self._index.pop(old, None)
            old.close(linger=0)
        socket = self.context.socket(zmq.REQ)
        socket.connect(f"tcp://localhost:{self.ports[i]}")
        self.poller.register(socket, zmq.POLLIN)
        self.sockets[i] = socket
        self._index[socket] = i

    def _receive(self, waiting: set[int]) -> list[tuple[int, bytes | None]]:
        """Réponses des environnements de `waiting`, dans leur ordre d'arrivée ; None pour ceux hors délai."""
        replies = []
        deadline = None if self.tcp_timeout is None else time.monotonic() + self.tcp_timeout / 1000
        while waiting:
            remaining = None if deadline is None else max(0, int((deadline - time.monotonic()) * 1000))
            events = self.poller.poll(remaining)
            if not events:
                if deadline is not None and time.monotonic() >= deadline:
                    for i in list(waiting):
                        self._connect(i)
                        replies.append((i, None))
                    break
                continue
            for socket, _ in events:
                i = self._index[socket]
                data = socket.recv()
                if i in waiting:  # sinon réponse inattendue, ignorée
                    replies.append((i, data))
                    waiting.discard(i)
        return replies

    def _batch(self, datas: list[bytes]) -> tuple[np.ndarray, np.ndarray]:
        """États binaires mis bout à bout : un tableau d'entiers et la position du début de chaque état."""
        offsets = np.zeros(len(datas), dtype=np.int64)
        np.cumsum([len(d) // 4 for d in datas[:-1]], out=offsets[1:])
        return np.frombuffer(b"".join(datas), dtype="<i4"), offsets

    # ------------------------------------------------------------------ resets (comme FastAutomathonEnv.reset)

    def _rng(self, i: int, seed=None) -> np.random.Generator:
        if seed is not None or self._rngs[i] is None:
            self._rngs[i], _ = seeding.np_random(seed)
        return self._rngs[i]

    def _send_reset(self, i: int, seed=None, options: dict | None = None):
        options = options or {}
        rng = self._rng(i, seed)
        drawn_seed = int(rng.integers(0, 2**31 - 1))  # tirés dans le même ordre que FastAutomathonEnv.reset
        drawn_side = self.random_side and bool(rng.integers(2))
        map_seed = options.get("map_seed", drawn_seed)
        self_on_right = options.get("self_on_right", drawn_side)
        reset_options = ResetOptions(Seed=map_seed, MapDensity=options.get("map_density", self.map_density),
                                     SelfOnRight=self_on_right, Rules=self.rules)
        self._reset_message[i] = json.dumps({"Reset": True, "DoneWithTraining": False, "BinaryState": True,
                                             "ResetOptions": reset_options.model_dump(mode="json", exclude_none=True)})
        self._reset_info[i] = {"map_seed": map_seed, "self_on_right": self_on_right}
        self.sockets[i].send_string(self._reset_message[i])

    def _wait_resets(self, waiting: set[int], obs: np.ndarray):
        """Attend les resets envoyés, en les renvoyant après un délai dépassé (RESET_ATTEMPTS essais au total)."""
        attempts = dict.fromkeys(waiting, 1)
        while waiting:
            for i, data in self._receive(set(waiting)):
                if data is not None:
                    raw = RawState(data)
                    self._data[i] = data
                    self._frames[i] = 0
                    self._flip[i] = self.canonical_side and starts_on_right_raw(raw)
                    self._reset_info[i]["flipped"] = bool(self._flip[i])
                    self.reset_infos[i] = self._reset_info[i]
                    obs[i] = extract_features_raw(raw, self._flip[i])
                    waiting.discard(i)
                elif attempts[i] >= RESET_ATTEMPTS:
                    raise TimeoutError("Make sure the Headless version of the game is running!")
                else:
                    attempts[i] += 1
                    self.sockets[i].send_string(self._reset_message[i])

    def reset(self):
        obs = np.zeros((self.num_envs, *self.observation_space.shape), dtype=np.float32)
        for i in range(self.num_envs):
            self._send_reset(i, self._seeds[i], self._options[i])
        self._wait_resets(set(range(self.num_envs)), obs)
        self._reset_seeds()
        self._reset_options()
        return obs

    # ------------------------------------------------------------------ steps (comme FastAutomathonEnv.step)

    def step_async(self, actions: np.ndarray) -> None:
        n = self.num_envs
        request = np.empty((n, 12), dtype="<i4")
        request[:, 0] = STEP_TAG
        request[:, 1] = self.frame_skip
        request[:, 2:7] = _raw_actions(actions, self._flip)
        # Adversaires : un appel Numba par bot, un appel Python par environnement pour les bots sans fast.py
        groups: dict = {}
        for i, slot in enumerate(self.slots):
            if slot.opponent.fast_fn is not None:
                groups.setdefault(slot.opponent.fast_fn, []).append(i)
            else:
                state = LazyGameState(RawState(self._data[i]), swap=True)
                request[i, 7:12] = encode_action(slot.opponent.agent.get_action(state))
        if groups:
            ints, offsets = self._batch(self._data)
            for fast_fn, indices in groups.items():
                _opponent_kernel(fast_fn)(ints, offsets, np.array(indices, dtype=np.int64), request[:, 7:12])
        for i in range(n):
            self.sockets[i].send(request[i].tobytes())

    def step_wait(self):
        n = self.num_envs
        timed_out = []
        for i, data in self._receive(set(range(n))):
            if data is None:
                timed_out.append(i)  # l'état précédent reste dans self._data : son observation sera renvoyée
            else:
                self._data[i] = data

        obs = np.zeros((n, *self.observation_space.shape), dtype=np.float32)
        rewards = np.zeros(n, dtype=np.float64)
        done = np.zeros(n, dtype=np.bool_)
        outcomes = np.zeros(n, dtype=np.int64)
        ints, offsets = self._batch(self._data)
        _process_steps(ints, offsets, self._flip, float(self.frame_skip), self._weights, obs, rewards, done, outcomes)
        if (outcomes < 0).any():
            raise ValueError(f"Version de l'état binaire inattendue (attendu {STATE_VERSION})")

        dones = np.zeros(n, dtype=bool)
        infos: list[dict] = [{} for _ in range(n)]
        timed_out_set = set(timed_out)
        resetting = set()
        for i in range(n):
            self._frames[i] += self.frame_skip
            if i in timed_out_set:  # délai dépassé : épisode tronqué, sans récompense
                info = {"timeout": False, "tcp_timeout": True}
                terminated, truncated = False, True
                rewards[i] = 0.0
            else:
                terminated = bool(done[i])
                truncated = not terminated and self._frames[i] >= self.max_steps
                info = {"timeout": truncated, "tcp_timeout": False, "outcome": OUTCOMES[outcomes[i]]}
            info["TimeLimit.truncated"] = truncated and not terminated
            infos[i] = info
            if terminated or truncated:
                dones[i] = True
                info["terminal_observation"] = obs[i].copy()
                self._send_reset(i)
                resetting.add(i)
        if resetting:
            self._wait_resets(resetting, obs)
        return obs, rewards, dones, infos

    # ------------------------------------------------------------------ interface VecEnv

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for socket in self.sockets:
            try:
                socket.send_string(json.dumps({"Reset": False, "DoneWithTraining": True}), zmq.NOBLOCK)
            except zmq.ZMQError:
                pass
            socket.close(linger=0)
        self.context.term()
        for process in self.processes:
            try:
                process.kill()
            except Exception:
                pass

    def _indices(self, indices):
        if indices is None:
            return range(self.num_envs)
        if isinstance(indices, int):
            return [indices]
        return indices

    def get_attr(self, attr_name: str, indices=None) -> list:
        return [getattr(self.slots[i], attr_name) for i in self._indices(indices)]

    def set_attr(self, attr_name: str, value, indices=None) -> None:
        for i in self._indices(indices):
            setattr(self.slots[i], attr_name, value)

    def env_method(self, method_name: str, *method_args, indices=None, **method_kwargs) -> list:
        return [getattr(self.slots[i], method_name)(*method_args, **method_kwargs) for i in self._indices(indices)]

    def env_is_wrapped(self, wrapper_class, indices=None) -> list[bool]:
        return [False for _ in self._indices(indices)]
