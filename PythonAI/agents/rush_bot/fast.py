"""
Version Numba de rush_bot (agent.py) : exactement les mêmes actions, calculées sur l'état binaire en quelques
microsecondes. L'environnement à processus unique (env.backend=vec) l'utilise automatiquement quand rush_bot est
l'adversaire.

Interface commune aux versions Numba des bots : get_action_raw(self_ship, enemy_ship, bullets, missiles, shields,
walls) reçoit l'état binaire du point de vue du bot (voir bridge/binary.py) et renvoie 5 entiers : déplacement x,
y et visée x, y (valeurs Fixed brutes), puis les boutons (1 mitrailleuse, 2 missile, 4 bouclier, 8 dash).
"""

import numpy as np
from numba import njit

from bridge.binary import SHIP_CD_DASH, SHIP_CD_MG, SHIP_CD_MISSILE, SHIP_CD_SHIELD, SHIP_EXISTS, SHIP_X, SHIP_Y
from bridge.fixed_numba import ONE_RAW, dot, normalized, saturate

from .agent import MIN_DISTANCE

MIN_DISTANCE_SQ_RAW = MIN_DISTANCE * MIN_DISTANCE * ONE_RAW


@njit(cache=True)
def get_action_raw(self_ship, enemy_ship, bullets, missiles, shields, walls):
    action = np.zeros(5, np.int32)
    if self_ship[SHIP_EXISTS] == 0 or enemy_ship[SHIP_EXISTS] == 0:
        # DummyBot : immobile, vise vers la droite et tire
        action[2] = ONE_RAW
        action[4] = 1
        return action

    to_x = saturate(np.int64(enemy_ship[SHIP_X]) - np.int64(self_ship[SHIP_X]))
    to_y = saturate(np.int64(enemy_ship[SHIP_Y]) - np.int64(self_ship[SHIP_Y]))
    distance_sq = dot(to_x, to_y, to_x, to_y)
    if distance_sq > 0:
        aim_x, aim_y = normalized(to_x, to_y)
    else:
        aim_x, aim_y = np.int64(ONE_RAW), np.int64(0)
    if distance_sq > MIN_DISTANCE_SQ_RAW:
        action[0], action[1] = aim_x, aim_y
    else:
        action[0], action[1] = saturate(-aim_x), saturate(-aim_y)
    action[2], action[3] = aim_x, aim_y
    action[4] = ((self_ship[SHIP_CD_MG] == 0) * 1 + (self_ship[SHIP_CD_MISSILE] == 0) * 2
                 + (self_ship[SHIP_CD_SHIELD] == 0) * 4 + (self_ship[SHIP_CD_DASH] == 0) * 8)
    return action
