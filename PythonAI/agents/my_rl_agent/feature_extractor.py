"""
Extracteur de features par défaut : transforme l'état du jeu en un vecteur de FEATURE_SIZE valeurs dans [-1, 1].

Une seule implémentation, `_features`, compilée avec Numba et utilisée partout (valeurs identiques au bit près) :
- extract_features_raw(raw, flip) : sur l'état binaire (bridge.binary.RawState), pour l'entraînement rapide ;
- extract_features(state, flip) : sur un GameState (protocole JSON, match avec le jeu graphique), converti en
  état binaire par bridge.binary.encode_state.

Version 3 (112 features, R&D des règles) : la v2, plus « armé ou non » pour chaque missile et un bloc de 4 features
pour la zone sûre (sa demi-taille et la marge de chaque vaisseau jusqu'à son bord).

Version 2 (106 features). Par rapport à la v1 (82 features) :
- la visée de chaque vaisseau et les recharges de l'adversaire sont incluses ;
- missiles et boucliers indiquent s'ils appartiennent à l'agent ; seules les balles adverses sont gardées ;
- chaque vitesse est normalisée par la vitesse maximale de son type (vaisseau, balle, missile) ;
- murs et boucliers ont leur orientation, codée (cos 2θ, sin 2θ) car un segment est identique à 180° près ;
- flip=True tourne tout de 180° (x, y -> -x, -y) : un agent parti à droite se voit comme s'il était parti à
  gauche. Les cartes étant symétriques par rapport au centre, les deux côtés donnent alors les mêmes
  observations, et l'agent n'a qu'un côté à apprendre. Ses actions doivent être tournées de la même façon.

Pour adapter l'extracteur, modifiez `_features` (et FEATURE_SIZE) : voir « Écrire son extracteur de features »
dans PythonAI/README.md. Numba compile un sous-ensemble de Python : nombres, tableaux NumPy, boucles, if,
fonctions de `math` et d'autres fonctions @njit ; pas d'objets GameState, de dictionnaires ni de listes de
types mélangés. Pour déboguer, lancez avec la variable d'environnement NUMBA_DISABLE_JIT=1 : le code s'exécute
alors en Python normal (lentement, mais avec les messages d'erreur habituels).
"""

import math

import numpy as np
from numba import njit

from bridge.binary import (B_OWNER, B_VX, B_VY, B_X, B_Y, H_ZONE_X, H_ZONE_Y, HEADER_SIZE, M_ARMED, M_OWNER, M_VX,
                           M_VY, M_X, M_Y, S_HP, S_OWNER, S_ROT, S_VX, S_VY, S_X, S_Y, STATE_VERSION, SHIP_AIM_X,
                           SHIP_AIM_Y, SHIP_CD_DASH, SHIP_CD_MG, SHIP_CD_MISSILE, SHIP_CD_SHIELD, SHIP_EXISTS, SHIP_HP,
                           SHIP_MAX_HP, SHIP_TEAM, SHIP_VX, SHIP_VY, SHIP_X, SHIP_Y, SHIP_SIZE, W_ROT, W_SIZE_X,
                           W_SIZE_Y, W_X, W_Y, RawState, encode_state)
from bridge.datatypes import ARENA_HALF_HEIGHT, ARENA_HALF_WIDTH, GameState

# Normalisations, en unités Unity (la carte fait environ 27,5 x 15,5, centrée en (0, 0))
NORM_DIST = 15.0
SHIP_SPEED = 7.0       # vitesse maximale d'un vaisseau (le dash, plus rapide, est écrêté à 1)
BULLET_SPEED = 17.0
MISSILE_SPEED = 9.0
SHIELD_SPEED = 2.0     # les boucliers, très lourds, ne bougent que s'ils sont poussés
SHIELD_MAX_HEALTH = 1500.0
ZONE_MARGIN_NORM = 5.0  # marge jusqu'au bord de la zone sûre, en unités (écrêtée à ±1 au-delà de 5 unités)

# Durée de chaque recharge, en frames (60 par seconde ; voir les *Ability.cs du Core). Chaque recharge est
# normalisée par sa propre durée : la feature va de ~1 (capacité tout juste utilisée) à 0 (prête).
MACHINE_GUN_COOLDOWN_FRAMES = 120  # 2 s
MISSILE_COOLDOWN_FRAMES = 90       # 1,5 s
SHIELD_COOLDOWN_FRAMES = 120       # 2 s
DASH_COOLDOWN_FRAMES = 42          # 0,7 s

NUM_MISSILES, NUM_BULLETS, NUM_SHIELDS, NUM_WALLS = 2, 5, 2, 4
FEATURE_SIZE = 11 + 12 + 7 * NUM_MISSILES + 5 * NUM_BULLETS + 9 * NUM_SHIELDS + 7 * NUM_WALLS + 4  # 112

# Une valeur Fixed brute (entier Q16.16) vaut raw / 65536 unités Unity
ONE = 65536.0


# --------------------------------------------------------------------------- fonctions publiques

def extract_features_raw(raw: RawState, flip: bool = False) -> np.ndarray:
    """Vecteur de features d'un état binaire (voir le docstring du module)."""
    return _features(raw.header, raw.self_ship, raw.enemy_ship, raw.bullets, raw.missiles, raw.shields, raw.walls,
                     bool(flip))


def extract_features(state: GameState, flip: bool = False) -> np.ndarray:
    """Vecteur de features d'un GameState : exactement celui d'extract_features_raw sur le même état."""
    return extract_features_raw(encode_state(state), flip)


def starts_on_right(state: GameState) -> bool:
    """Votre vaisseau est-il celui qui part à droite (équipe Red) ? Sert à choisir flip."""
    ship = state.SelfShip
    if ship is None:
        return False
    if ship.Team is not None:
        return ship.Team == "Red"
    return float(ship.Position.X) > 0  # Headless sans champ Team : côté actuel


def starts_on_right_raw(raw: RawState) -> bool:
    return bool(raw.self_ship[SHIP_EXISTS]) and int(raw.self_ship[SHIP_TEAM]) == 1


def flip_action(action: np.ndarray) -> np.ndarray:
    """Tourne de 180° les directions de déplacement et de visée d'une action (8 valeurs) produite avec flip=True."""
    action = np.array(action, dtype=np.float32, copy=True)
    action[:4] = -action[:4]
    return action


def warmup():
    """
    Compile l'extracteur (ou le charge depuis le cache de Numba) sur un état vide. À appeler au démarrage d'un
    agent : sinon la première frame du match attend la compilation (quelques secondes la première fois).
    """
    empty = np.zeros(HEADER_SIZE + 2 * SHIP_SIZE, dtype="<i4")
    empty[0] = STATE_VERSION
    extract_features_raw(RawState(empty.tobytes()), False)


# --------------------------------------------------------------------------- calcul (compilé par Numba)
# Les états sont des tableaux d'entiers bruts (voir bridge/binary.py pour les colonnes T_*, B_*, M_*, S_*, W_*) :
# une valeur Fixed vaut raw / ONE. Les calculs sont faits en float64, puis rangés dans un vecteur float32.

@njit(cache=True)
def _clip(value):
    return min(max(value, -1.0), 1.0)


@njit(cache=True)
def _health(ship):
    return (ship[SHIP_HP] / ONE) / max(ship[SHIP_MAX_HP] / ONE, 1.0)


@njit(cache=True)
def _cooldowns(ship, out, i):
    out[i] = min(ship[SHIP_CD_MG] / MACHINE_GUN_COOLDOWN_FRAMES, 1.0)
    out[i + 1] = min(ship[SHIP_CD_MISSILE] / MISSILE_COOLDOWN_FRAMES, 1.0)
    out[i + 2] = min(ship[SHIP_CD_SHIELD] / SHIELD_COOLDOWN_FRAMES, 1.0)
    out[i + 3] = min(ship[SHIP_CD_DASH] / DASH_COOLDOWN_FRAMES, 1.0)


@njit(cache=True)
def _moving(out, i, x, y, vx, vy, speed, sign, self_x, self_y):
    """Existe, position relative, vitesse (5 features)."""
    out[i] = 1.0
    out[i + 1] = _clip((sign * (x / ONE) - self_x) / NORM_DIST)
    out[i + 2] = _clip((sign * (y / ONE) - self_y) / NORM_DIST)
    out[i + 3] = _clip(sign * (vx / ONE) / speed)
    out[i + 4] = _clip(sign * (vy / ONE) / speed)


@njit(cache=True)
def _nearest(block, keep, count, sign, self_x, self_y):
    """
    Indices des `count` lignes de `block` (colonnes 0 et 1 : position) les plus proches de votre vaisseau, parmi
    celles où keep est vrai ; -1 s'il en manque. À distance égale, la première ligne passe d'abord.
    """
    n = block.shape[0]
    dist = np.empty(n)
    for j in range(n):
        dx = sign * (block[j, 0] / ONE) - self_x
        dy = sign * (block[j, 1] / ONE) - self_y
        dist[j] = dx * dx + dy * dy
    chosen = np.full(count, -1, np.int64)
    taken = np.zeros(n, np.bool_)
    for k in range(count):
        best = -1
        for j in range(n):
            if keep[j] and not taken[j] and (best < 0 or dist[j] < dist[best]):
                best = j
        if best < 0:
            break
        chosen[k] = best
        taken[best] = True
    return chosen


@njit(cache=True)
def _zone_margin(ship, zone_x, zone_y):
    """Distance du centre du vaisseau au bord de la zone sûre (négative hors de la zone), normalisée."""
    margin = min(zone_x - abs(ship[SHIP_X] / ONE), zone_y - abs(ship[SHIP_Y] / ONE))
    return _clip(margin / ZONE_MARGIN_NORM)


@njit(cache=True)
def _features(header, self_ship, enemy_ship, bullets, missiles, shields, walls, flip):
    out = np.zeros(FEATURE_SIZE, np.float32)  # entités absentes : zéros, flag « existe » compris
    sign = -1.0 if flip else 1.0

    # --- 1. Votre vaisseau (11) : position, vitesse, PV, 4 recharges, visée ---
    self_x, self_y = 0.0, 0.0
    team = self_ship[SHIP_TEAM]
    has_team = self_ship[SHIP_EXISTS] != 0 and team >= 0
    if self_ship[SHIP_EXISTS] != 0:
        self_x = sign * (self_ship[SHIP_X] / ONE)
        self_y = sign * (self_ship[SHIP_Y] / ONE)
        out[0] = _clip(self_x / NORM_DIST)
        out[1] = _clip(self_y / NORM_DIST)
        out[2] = _clip(sign * (self_ship[SHIP_VX] / ONE) / SHIP_SPEED)
        out[3] = _clip(sign * (self_ship[SHIP_VY] / ONE) / SHIP_SPEED)
        out[4] = _health(self_ship)
        _cooldowns(self_ship, out, 5)
        out[9] = sign * (self_ship[SHIP_AIM_X] / ONE)
        out[10] = sign * (self_ship[SHIP_AIM_Y] / ONE)
    i = 11

    # --- 2. Vaisseau adverse (12) : existe, position relative, vitesse, PV, visée, 4 recharges ---
    if enemy_ship[SHIP_EXISTS] != 0:
        _moving(out, i, enemy_ship[SHIP_X], enemy_ship[SHIP_Y], enemy_ship[SHIP_VX], enemy_ship[SHIP_VY], SHIP_SPEED,
                sign, self_x, self_y)
        out[i + 5] = _health(enemy_ship)
        out[i + 6] = sign * (enemy_ship[SHIP_AIM_X] / ONE)
        out[i + 7] = sign * (enemy_ship[SHIP_AIM_Y] / ONE)
        _cooldowns(enemy_ship, out, i + 8)
    i += 12

    # --- 3. Missiles, les 2 plus proches (7 chacun) : + à vous ou non (les vôtres peuvent vous toucher), armé ---
    nearest = _nearest(missiles, np.ones(missiles.shape[0], np.bool_), NUM_MISSILES, sign, self_x, self_y)
    for k in range(NUM_MISSILES):
        if nearest[k] >= 0:
            m = missiles[nearest[k]]
            _moving(out, i, m[M_X], m[M_Y], m[M_VX], m[M_VY], MISSILE_SPEED, sign, self_x, self_y)
            out[i + 5] = 1.0 if has_team and m[M_OWNER] == team else 0.0
            out[i + 6] = 1.0 if m[M_ARMED] != 0 else 0.0
        i += 7

    # --- 4. Balles adverses qui se rapprochent, les 5 plus proches (5 chacune) ---
    # Vos propres balles ne vous touchent pas. Une balle se rapproche si (Vaisseau -> Balle) . Vitesse <= 0.
    keep = np.empty(bullets.shape[0], np.bool_)
    for j in range(bullets.shape[0]):
        b = bullets[j]
        dx = sign * (b[B_X] / ONE) - self_x
        dy = sign * (b[B_Y] / ONE) - self_y
        approaching = dx * (sign * (b[B_VX] / ONE)) + dy * (sign * (b[B_VY] / ONE)) <= 0
        keep[j] = approaching and not (has_team and b[B_OWNER] == team)
    nearest = _nearest(bullets, keep, NUM_BULLETS, sign, self_x, self_y)
    for k in range(NUM_BULLETS):
        if nearest[k] >= 0:
            b = bullets[nearest[k]]
            _moving(out, i, b[B_X], b[B_Y], b[B_VX], b[B_VY], BULLET_SPEED, sign, self_x, self_y)
        i += 5

    # --- 5. Boucliers, les 2 plus proches (9 chacun) : + PV, à vous ou non, orientation ---
    nearest = _nearest(shields, np.ones(shields.shape[0], np.bool_), NUM_SHIELDS, sign, self_x, self_y)
    for k in range(NUM_SHIELDS):
        if nearest[k] >= 0:
            s = shields[nearest[k]]
            _moving(out, i, s[S_X], s[S_Y], s[S_VX], s[S_VY], SHIELD_SPEED, sign, self_x, self_y)
            out[i + 5] = (s[S_HP] / ONE) / SHIELD_MAX_HEALTH
            out[i + 6] = 1.0 if has_team and s[S_OWNER] == team else 0.0
            angle = 2.0 * (s[S_ROT] / ONE)  # orientation d'un segment : inchangée par la rotation de 180°
            out[i + 7] = math.cos(angle)
            out[i + 8] = math.sin(angle)
        i += 9

    # --- 6. Murs, les 4 plus proches (7 chacun) : existe, position relative, taille, orientation ---
    nearest = _nearest(walls, np.ones(walls.shape[0], np.bool_), NUM_WALLS, sign, self_x, self_y)
    for k in range(NUM_WALLS):
        if nearest[k] >= 0:
            w = walls[nearest[k]]
            out[i] = 1.0
            out[i + 1] = _clip((sign * (w[W_X] / ONE) - self_x) / NORM_DIST)
            out[i + 2] = _clip((sign * (w[W_Y] / ONE) - self_y) / NORM_DIST)
            out[i + 3] = _clip((w[W_SIZE_X] / ONE) / NORM_DIST)
            out[i + 4] = _clip((w[W_SIZE_Y] / ONE) / NORM_DIST)
            angle = 2.0 * (w[W_ROT] / ONE)
            out[i + 5] = math.cos(angle)
            out[i + 6] = math.sin(angle)
        i += 7

    # --- 7. Zone sûre (4) : demi-taille (fraction de l'arène), marge de votre vaisseau et de l'adversaire jusqu'à
    # son bord (négative dehors). Sans zone, c'est l'arène entière. Symétrique : inchangée par flip ---
    zone_x, zone_y = header[H_ZONE_X] / ONE, header[H_ZONE_Y] / ONE
    out[i] = zone_x / ARENA_HALF_WIDTH
    out[i + 1] = zone_y / ARENA_HALF_HEIGHT
    if self_ship[SHIP_EXISTS] != 0:
        out[i + 2] = _zone_margin(self_ship, zone_x, zone_y)
    if enemy_ship[SHIP_EXISTS] != 0:
        out[i + 3] = _zone_margin(enemy_ship, zone_x, zone_y)
    i += 4

    return out
