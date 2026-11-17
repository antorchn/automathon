from bridge.datatypes import GameState, AIAction


def calculate_reward(current_state: GameState, next_state: GameState, action: AIAction = None, current_step: int = 0,
                     frames: int = 1, time_penalty: float = 0.1, damage_dealt_coef: float = 1.0,
                     pain_factor: float = 0.1, win_bonus: float = 1000.0, loss_penalty: float = 1000.0,
                     draw_penalty: float = 1000.0) -> float:
    """
    Calcule la récompense (Reward Shaping) entre deux étapes.
    frames : frames simulées pendant ce step (répétition d'action).
    Les poids sont réglables dans la section `reward` de training/conf/rl_training.yaml.
    """
    reward = -time_penalty * frames  # pénalité de temps : pousse l'agent à engager le combat

    # Dégâts du dernier step, comptés par le Headless sur toutes ses frames (coup fatal compris)
    damage_dealt = float(next_state.DamageDealt)
    if damage_dealt > 0:
        reward += damage_dealt * damage_dealt_coef

    # Dégâts subis : un pain_factor bas évite que l'agent apprenne à fuir le combat
    damage_taken = float(next_state.DamageTaken)
    if damage_taken > 0:
        reward -= damage_taken * pain_factor

    # Fin du match
    if next_state.Outcome == "Win":
        reward += win_bonus
    elif next_state.Outcome == "Loss":
        reward -= loss_penalty
    elif next_state.Outcome == "Draw":
        reward -= draw_penalty  # les deux vaisseaux détruits en même temps

    return float(reward)
