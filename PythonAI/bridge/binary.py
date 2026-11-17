"""
Protocole binaire d'entraînement : mêmes informations que le JSON, en tableaux d'entiers 32 bits
(les nombres Fixed sous forme de leur valeur brute Q16.16), beaucoup plus rapides à écrire et à lire.

Le format est décrit dans game/AutomathonUnity/Assets/Core/AI/BinaryProtocol.cs : les deux fichiers
changent ensemble.

    gym = BinaryGym(tcp_port="5555")
    raw = gym.reset(ResetOptions(Seed=42))              # RawState : tableaux NumPy d'entiers bruts
    raw = gym.step(self_action, enemy_action, frames=4) # actions : AIAction ou 8 floats (comme l'env Gym)
    state = to_game_state(raw)                          # GameState complet, pour les bots
    raw = encode_state(state)                           # et l'inverse : GameState -> RawState
"""

import json

import numpy as np
import zmq

from bridge.datatypes import (ARENA_HALF_HEIGHT, ARENA_HALF_WIDTH, AIAction, BulletState, Fixed, FixedVector2, GameState,
                              MissileState, ResetOptions, ShieldState, ShipState, WallState)

STEP_TAG = 0x31504554
STATE_VERSION = 2
HEADER_SIZE, SHIP_SIZE, BULLET_SIZE, MISSILE_SIZE, SHIELD_SIZE, WALL_SIZE = 12, 16, 6, 8, 9, 5
# Colonnes de l'en-tete (HEADER_SIZE entiers) : version, fin, resultat (indice dans OUTCOMES), frame, degats infliges
# et subis pendant le dernier step (Fixed bruts), nombres de balles, missiles, boucliers et murs, puis la demi-taille de
# la zone sure (Fixed bruts)
H_VERSION, H_DONE, H_OUTCOME, H_FRAME, H_DAMAGE_DEALT, H_DAMAGE_TAKEN = range(6)
H_N_BULLETS, H_N_MISSILES, H_N_SHIELDS, H_N_WALLS, H_ZONE_X, H_ZONE_Y = range(6, 12)
OUTCOMES = ("Ongoing", "Win", "Loss", "Draw")
TEAMS = {0: "Green", 1: "Red"}
ONE_RAW = 65536.0

# Colonnes d'un bloc vaisseau
(SHIP_EXISTS, SHIP_TEAM, SHIP_X, SHIP_Y, SHIP_VX, SHIP_VY, SHIP_AIM_X, SHIP_AIM_Y, SHIP_ROT, SHIP_HP,
 SHIP_MAX_HP) = range(11)
SHIP_CD_MG, SHIP_CD_MISSILE, SHIP_CD_SHIELD, SHIP_CD_DASH = 11, 12, 13, 14
# Colonnes des lignes de balles (B_), missiles (M_), boucliers (S_) et murs (W_). Propriétaire : 0 Green, 1 Red, -1 aucun
B_X, B_Y, B_VX, B_VY, B_RADIUS, B_OWNER = range(BULLET_SIZE)
M_X, M_Y, M_VX, M_VY, M_ROT, M_RADIUS, M_OWNER, M_ARMED = range(MISSILE_SIZE)
S_X, S_Y, S_VX, S_VY, S_ROT, S_SIZE_X, S_SIZE_Y, S_HP, S_OWNER = range(SHIELD_SIZE)
W_X, W_Y, W_SIZE_X, W_SIZE_Y, W_ROT = range(WALL_SIZE)

# Dimensions constantes d'un vaisseau (Tank.cs), non transmises
_SHIP_WIDTH = Fixed(1.138)
_SHIP_HEIGHT = Fixed(0.838)
_SHIP_RADIUS = min(_SHIP_WIDTH, _SHIP_HEIGHT) / 2


class RawState:
    """
    État binaire décodé : tableaux NumPy d'entiers bruts (Fixed = valeur / 65536), en lecture seule.
    header : les HEADER_SIZE entiers de l'en-tête (colonnes H_*) ;
    self_ship et enemy_ship : SHIP_SIZE entiers chacun (colonnes T_*, tout à zéro si le vaisseau est détruit) ;
    bullets, missiles, shields, walls : une ligne par entité (colonnes B_*, M_*, S_*, W_*).
    """

    __slots__ = ("data", "header", "done", "outcome", "frame", "damage_dealt", "damage_taken", "zone_x", "zone_y",
                 "self_ship", "enemy_ship", "bullets", "missiles", "shields", "walls")

    def __init__(self, data: bytes):
        ints = np.frombuffer(data, dtype="<i4")
        if ints[0] != STATE_VERSION:
            raise ValueError(f"Version de l'état binaire inattendue : {ints[0]} (attendu {STATE_VERSION})")
        self.data = data
        self.header = ints[:HEADER_SIZE]
        self.done = bool(ints[1])
        self.outcome = OUTCOMES[ints[2]]
        self.frame = int(ints[3])
        self.damage_dealt = int(ints[4])
        self.damage_taken = int(ints[5])
        counts = ints[6:10]
        self.zone_x, self.zone_y = int(ints[H_ZONE_X]), int(ints[H_ZONE_Y])  # demi-taille de la zone sure (brut)
        i = HEADER_SIZE
        self.self_ship = ints[i:i + SHIP_SIZE]
        self.enemy_ship = ints[i + SHIP_SIZE:i + 2 * SHIP_SIZE]
        i += 2 * SHIP_SIZE
        blocks = []
        for count, size in zip(counts, (BULLET_SIZE, MISSILE_SIZE, SHIELD_SIZE, WALL_SIZE)):
            blocks.append(ints[i:i + count * size].reshape(int(count), size))
            i += count * size
        self.bullets, self.missiles, self.shields, self.walls = blocks


# --------------------------------------------------------------------------- vers GameState

def _fixed(raw) -> Fixed:
    return Fixed.from_raw(int(raw))


def _vector(x, y) -> FixedVector2:
    return FixedVector2.model_construct(X=_fixed(x), Y=_fixed(y))


def _owner(code) -> str | None:
    return TEAMS.get(int(code))


def _ship(block) -> ShipState | None:
    if not block[SHIP_EXISTS]:
        return None
    return ShipState.model_construct(
        Type="ShipState", Team=_owner(block[SHIP_TEAM]), Position=_vector(block[SHIP_X], block[SHIP_Y]),
        Width=_SHIP_WIDTH, Height=_SHIP_HEIGHT, Radius=_SHIP_RADIUS, Rotation=_fixed(block[SHIP_ROT]),
        Velocity=_vector(block[SHIP_VX], block[SHIP_VY]), AimingDirection=_vector(block[SHIP_AIM_X], block[SHIP_AIM_Y]),
        Health=_fixed(block[SHIP_HP]), MaxHealth=_fixed(block[SHIP_MAX_HP]),
        ShieldCooldownFramesLeft=int(block[SHIP_CD_SHIELD]), MissileCooldownFramesLeft=int(block[SHIP_CD_MISSILE]),
        MachineGunCooldownFramesLeft=int(block[SHIP_CD_MG]), DashCooldownFramesLeft=int(block[SHIP_CD_DASH]))


_wall_cache: dict[bytes, list] = {}


def _walls(walls: np.ndarray) -> list:
    """Les murs ne bougent pas pendant un match : leurs objets sont construits une fois puis réutilisés."""
    key = walls.tobytes()
    cached = _wall_cache.get(key)
    if cached is None:
        cached = [WallState.model_construct(Type="WallState", Position=_vector(w[0], w[1]), Size=_vector(w[2], w[3]),
                                            Rotation=_fixed(w[4])) for w in walls]
        if len(_wall_cache) > 64:
            _wall_cache.clear()
        _wall_cache[key] = cached
    return cached


class LazyGameState:
    """
    Se comporte comme un GameState (mêmes attributs), mais ne construit les listes d'entités (balles, missiles,
    boucliers, murs) que si on les lit. Un bot qui ne regarde que les vaisseaux ne paie pas le reste.
    """

    def __init__(self, raw: RawState, swap: bool = False):
        self._raw, self._swap, self._full = raw, swap, None
        self_ship, enemy_ship = _ship(raw.self_ship), _ship(raw.enemy_ship)
        self.SelfShip, self.EnemyShip = (enemy_ship, self_ship) if swap else (self_ship, enemy_ship)
        self.Done = raw.done
        self.Frame = raw.frame
        self.ZoneHalfSize = _vector(raw.zone_x, raw.zone_y)
        self.Outcome = {"Win": "Loss", "Loss": "Win"}.get(raw.outcome, raw.outcome) if swap else raw.outcome

    def _state(self) -> GameState:
        if self._full is None:
            self._full = to_game_state(self._raw, self._swap)
        return self._full

    def raw_state(self) -> RawState:
        """Le même état en binaire, de son point de vue (sans construire les listes d'entités)."""
        return swap_sides(self._raw) if self._swap else self._raw

    BulletStates = property(lambda self: self._state().BulletStates)
    MissileStates = property(lambda self: self._state().MissileStates)
    WallStates = property(lambda self: self._state().WallStates)
    ShieldStates = property(lambda self: self._state().ShieldStates)
    DamageDealt = property(lambda self: self._state().DamageDealt)
    DamageTaken = property(lambda self: self._state().DamageTaken)

    def __getattr__(self, name):  # tout autre attribut ou méthode de GameState (model_dump...)
        return getattr(self._state(), name)


def to_game_state(raw: RawState, swap: bool = False) -> GameState:
    """
    GameState équivalent à celui du protocole JSON, pour les bots (objets Fixed exacts).
    swap=True : le même état vu par l'adversaire (vaisseaux, dégâts et résultat intervertis).
    """
    self_ship, enemy_ship = _ship(raw.self_ship), _ship(raw.enemy_ship)
    dealt, taken, outcome = raw.damage_dealt, raw.damage_taken, raw.outcome
    if swap:
        self_ship, enemy_ship, dealt, taken = enemy_ship, self_ship, taken, dealt
        outcome = {"Win": "Loss", "Loss": "Win"}.get(outcome, outcome)
    return GameState.model_construct(
        SelfShip=self_ship, EnemyShip=enemy_ship,
        BulletStates=[BulletState.model_construct(Type="BulletState", Owner=_owner(b[5]), Position=_vector(b[0], b[1]),
                                                  Velocity=_vector(b[2], b[3]), Radius=_fixed(b[4])) for b in raw.bullets],
        MissileStates=[MissileState.model_construct(Type="MissileState", Owner=_owner(m[6]), Position=_vector(m[0], m[1]),
                                                    Velocity=_vector(m[2], m[3]), Rotation=_fixed(m[4]),
                                                    Radius=_fixed(m[5]), Armed=bool(m[7])) for m in raw.missiles],
        WallStates=_walls(raw.walls),
        ShieldStates=[ShieldState.model_construct(Type="ShieldState", Owner=_owner(s[8]), Position=_vector(s[0], s[1]),
                                                  Velocity=_vector(s[2], s[3]), Rotation=_fixed(s[4]),
                                                  Size=_vector(s[5], s[6]), Health=_fixed(s[7])) for s in raw.shields],
        Done=raw.done, Outcome=outcome, Frame=raw.frame, DamageDealt=_fixed(dealt), DamageTaken=_fixed(taken),
        ZoneHalfSize=_vector(raw.zone_x, raw.zone_y))


_TEAM_CODES = {"Green": 0, "Red": 1}
_ARENA_RAW_X, _ARENA_RAW_Y = Fixed(ARENA_HALF_WIDTH).raw, Fixed(ARENA_HALF_HEIGHT).raw


def _team_code(team) -> int:
    return _TEAM_CODES.get(team, -1)


def _ship_ints(ship) -> list[int]:
    if ship is None:
        return [0] * SHIP_SIZE
    return [1, _team_code(ship.Team), ship.Position.X.raw, ship.Position.Y.raw, ship.Velocity.X.raw,
            ship.Velocity.Y.raw, ship.AimingDirection.X.raw, ship.AimingDirection.Y.raw, ship.Rotation.raw,
            ship.Health.raw, ship.MaxHealth.raw, int(ship.MachineGunCooldownFramesLeft),
            int(ship.MissileCooldownFramesLeft), int(ship.ShieldCooldownFramesLeft), int(ship.DashCooldownFramesLeft),
            0]


def encode_state(state) -> RawState:
    """
    GameState -> RawState : exactement l'état binaire qu'enverrait le Headless pour cet état. Permet d'appliquer
    les fonctions écrites pour l'état brut (comme l'extracteur de features compilé avec Numba) à un GameState,
    venu du protocole JSON ou du jeu graphique en match.
    """
    if isinstance(state, RawState):
        return state
    if isinstance(state, LazyGameState):
        return state.raw_state()
    ints = [STATE_VERSION, int(bool(state.Done)), OUTCOMES.index(getattr(state, "Outcome", None) or "Ongoing"),
            int(getattr(state, "Frame", 0) or 0), getattr(state, "DamageDealt", Fixed.ZERO).raw,
            getattr(state, "DamageTaken", Fixed.ZERO).raw, len(state.BulletStates), len(state.MissileStates),
            len(state.ShieldStates), len(state.WallStates)]
    zone = getattr(state, "ZoneHalfSize", None)
    ints += [zone.X.raw, zone.Y.raw] if zone is not None else [_ARENA_RAW_X, _ARENA_RAW_Y]
    ints += _ship_ints(state.SelfShip) + _ship_ints(state.EnemyShip)
    for b in state.BulletStates:
        ints += [b.Position.X.raw, b.Position.Y.raw, b.Velocity.X.raw, b.Velocity.Y.raw, b.Radius.raw,
                 _team_code(b.Owner)]
    for m in state.MissileStates:
        ints += [m.Position.X.raw, m.Position.Y.raw, m.Velocity.X.raw, m.Velocity.Y.raw, m.Rotation.raw,
                 m.Radius.raw, _team_code(m.Owner), int(bool(getattr(m, "Armed", True)))]
    for s in state.ShieldStates:
        ints += [s.Position.X.raw, s.Position.Y.raw, s.Velocity.X.raw, s.Velocity.Y.raw, s.Rotation.raw,
                 s.Size.X.raw, s.Size.Y.raw, s.Health.raw, _team_code(s.Owner)]
    for w in state.WallStates:
        ints += [w.Position.X.raw, w.Position.Y.raw, w.Size.X.raw, w.Size.Y.raw, w.Rotation.raw]
    return RawState(np.array(ints, dtype="<i4").tobytes())


def swap_sides(raw: RawState) -> RawState:
    """Le même état vu par l'adversaire : vaisseaux, dégâts infligés et subis, victoire et défaite intervertis."""
    ints = np.frombuffer(raw.data, dtype="<i4").copy()
    ints[2] = {1: 2, 2: 1}.get(int(ints[2]), int(ints[2]))
    ints[4], ints[5] = ints[5], ints[4]
    first = slice(HEADER_SIZE, HEADER_SIZE + SHIP_SIZE)
    second = slice(HEADER_SIZE + SHIP_SIZE, HEADER_SIZE + 2 * SHIP_SIZE)
    ints[first], ints[second] = ints[second].copy(), ints[first].copy()
    return RawState(ints.tobytes())


# --------------------------------------------------------------------------- actions

def _raw_from_floats(values: np.ndarray) -> np.ndarray:
    """Floats -> entiers bruts Q16.16, arrondi à la demi-unité loin de zéro (comme Fixed(float))."""
    scaled = np.asarray(values, dtype=np.float64) * ONE_RAW
    raw = np.trunc(scaled + np.copysign(0.5, scaled))
    return np.clip(raw, -2**31, 2**31 - 1).astype(np.int64)


def encode_action(action) -> list[int]:
    """AIAction, ou 8 floats comme l'espace d'action de l'env Gym (déplacement, visée, 4 boutons > 0) -> 5 entiers."""
    if isinstance(action, AIAction):
        move, aim = action.MovingDirection, action.AimingDirection
        values = [move.X.raw, move.Y.raw, aim.X.raw, aim.Y.raw]
        buttons = (action.MachineGun, action.Missile, action.Shield, action.Dash)
    else:
        action = np.asarray(action)
        values = [int(v) for v in _raw_from_floats(action[:4])]
        buttons = tuple(bool(b > 0.0) for b in action[4:8])
    return values + [sum(1 << i for i, pressed in enumerate(buttons) if pressed)]


# --------------------------------------------------------------------------- connexion

class BinaryGym:
    """Comme bridge.gym.Gym (mode entraînement, socket REQ), mais avec le protocole binaire."""

    def __init__(self, tcp_port: str = "5555"):
        self.address = "tcp://localhost:" + tcp_port
        self.context = zmq.Context()
        self.socket = None
        self.__connect__()

    def reset(self, options: ResetOptions | None = None, tcp_connection_timeout: int | None = 500) -> RawState:
        message = {"Reset": True, "DoneWithTraining": False, "BinaryState": True,
                   "ResetOptions": options.model_dump(mode="json", exclude_none=True) if options else None}
        self.socket.send_string(json.dumps(message))
        return self.__wait_state__(tcp_connection_timeout, "Make sure the Headless version of the game is running!")

    def step(self, self_action, enemy_action, frames: int = 1, tcp_connection_timeout: int | None = 500) -> RawState:
        """Joue `frames` frames avec ces actions (AIAction ou 8 floats) et renvoie l'état obtenu."""
        request = [STEP_TAG, frames] + encode_action(self_action) + encode_action(enemy_action)
        self.socket.send(np.array(request, dtype="<i4").tobytes())
        return self.__wait_state__(tcp_connection_timeout, "The Headless did not answer in time (step).")

    def end_training(self):
        self.socket.send_string(json.dumps({"Reset": False, "DoneWithTraining": True}))

    def close(self):
        self.socket.close(linger=0)
        self.context.term()

    def __connect__(self):
        # Après un délai dépassé, la socket REQ est inutilisable : on la remplace ("Lazy Pirate"), comme Gym
        if self.socket is not None:
            self.socket.close(linger=0)
        self.socket = self.context.socket(zmq.REQ)
        self.socket.connect(self.address)
        self.poller = zmq.Poller()
        self.poller.register(self.socket, zmq.POLLIN)

    def __wait_state__(self, tcp_connection_timeout: int | None, timeout_message: str) -> RawState:
        if self.poller.poll(tcp_connection_timeout):
            return RawState(self.socket.recv())
        self.__connect__()
        raise TimeoutError(timeout_message)
