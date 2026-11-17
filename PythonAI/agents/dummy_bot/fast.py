"""Version Numba de dummy_bot (agent.py) : mêmes actions (voir agents/rush_bot/fast.py pour l'interface)."""

import numpy as np
from numba import njit

from bridge.fixed_numba import ONE_RAW


@njit(cache=True)
def get_action_raw(self_ship, enemy_ship, bullets, missiles, shields, walls):
    action = np.zeros(5, np.int32)
    action[2] = ONE_RAW  # immobile, vise vers la droite
    action[4] = 1        # mitrailleuse
    return action
