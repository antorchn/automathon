"""Calcul du classement Elo (Bradley-Terry), incertitude, placement et affinage."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np

LN10_OVER_400 = math.log(10.0) / 400.0
FOUR_HUNDRED_OVER_LN10 = 400.0 / math.log(10.0)


@dataclass(frozen=True)
class EloEstimate:
    agent_id: str
    elo: float
    margin: float
    sigma: float
    provisional: bool
    games_played: int
    wins: int
    draws: int
    losses: int


@dataclass(frozen=True)
class MatchRecord:
    left_id: str
    right_id: str
    # Outcome du point de vue du joueur de gauche : "left", "right", "draw"
    outcome: str


def _score_from_outcome(outcome: str) -> float:
    if outcome == "left":
        return 1.0
    if outcome == "right":
        return 0.0
    if outcome == "draw":
        return 0.5
    raise ValueError(f"Issue de partie inconnue : {outcome!r}")


def solve_bradley_terry(
    agent_ids: Sequence[str],
    matches: Iterable[MatchRecord | tuple[str, str, str]],
    anchor_id: str,
    anchor_elo: float = 1000.0,
    prior_draws: float = 2.0,
    provisional_margin: float = 75.0,
    max_iters: int = 200,
    tol_elo: float = 1e-8,
) -> dict[str, EloEstimate]:
    """Résout le maximum de vraisemblance Bradley-Terry avec ancre fixe et a priori.

    - Un nul compte pour 0,5 victoire pour chacun.
    - `anchor_id` (ex. rush_bot) est fixé à `anchor_elo` (1000).
    - A priori : `prior_draws` nuls virtuels (poids n = prior_draws, score = 0.5 * prior_draws)
      contre l'ancre pour tout autre agent, garantissant un Elo fini même à 100% ou 0%.
    - Incertitude : sigma_i = (400 / ln 10) / sqrt(sum_j n_ij * p_ij * (1 - p_ij)),
      marge = 1.96 * sigma_i.
    """
    unique_ids = list(dict.fromkeys(agent_ids))
    if not unique_ids:
        return {}

    has_anchor = anchor_id in unique_ids
    idx_of = {aid: i for i, aid in enumerate(unique_ids)}
    n_agents = len(unique_ids)

    n_mat = np.zeros((n_agents, n_agents), dtype=np.float64)
    w_mat = np.zeros((n_agents, n_agents), dtype=np.float64)

    games_played = {aid: 0 for aid in unique_ids}
    wins = {aid: 0 for aid in unique_ids}
    draws = {aid: 0 for aid in unique_ids}
    losses = {aid: 0 for aid in unique_ids}

    for item in matches:
        if isinstance(item, MatchRecord):
            left_id, right_id, outcome = item.left_id, item.right_id, item.outcome
        else:
            left_id, right_id, outcome = item
        if left_id not in idx_of or right_id not in idx_of or left_id == right_id:
            continue
        i, j = idx_of[left_id], idx_of[right_id]
        s_left = _score_from_outcome(outcome)
        s_right = 1.0 - s_left

        n_mat[i, j] += 1.0
        n_mat[j, i] += 1.0
        w_mat[i, j] += s_left
        w_mat[j, i] += s_right

        games_played[left_id] += 1
        games_played[right_id] += 1
        if s_left == 1.0:
            wins[left_id] += 1
            losses[right_id] += 1
        elif s_left == 0.0:
            losses[left_id] += 1
            wins[right_id] += 1
        else:
            draws[left_id] += 1
            draws[right_id] += 1

    # Matrice incluant l'a priori pour le MLE (sans fausser games_played/W/D/L)
    n_fit = n_mat.copy()
    w_fit = w_mat.copy()
    if has_anchor and prior_draws > 0.0:
        a_idx = idx_of[anchor_id]
        for i in range(n_agents):
            if i == a_idx:
                continue
            n_fit[i, a_idx] += prior_draws
            n_fit[a_idx, i] += prior_draws
            w_fit[i, a_idx] += 0.5 * prior_draws
            w_fit[a_idx, i] += 0.5 * prior_draws
    elif prior_draws > 0.0 and n_agents > 1:
        # Si l'ancre est absente (cas dégénéré), faible a priori symétrique
        tiny = prior_draws / max(1, n_agents - 1)
        off = 1.0 - np.eye(n_agents)
        n_fit += tiny * off
        w_fit += 0.5 * tiny * off

    # Paramètre naturel theta_i = (R_i - anchor_elo) * ln(10) / 400
    theta = np.zeros(n_agents, dtype=np.float64)
    free_indices = [i for i in range(n_agents) if not (has_anchor and i == idx_of[anchor_id])]

    if free_indices:
        k = len(free_indices)
        for _ in range(max_iters):
            diff = np.clip(theta[:, None] - theta[None, :], -40.0, 40.0)
            p_mat = 1.0 / (1.0 + np.exp(-diff))
            np.fill_diagonal(p_mat, 0.0)

            grad_full = (w_fit - n_fit * p_mat).sum(axis=1)
            grad = grad_full[free_indices]

            # Hessienne négative de la log-vraisemblance : H_ii = sum_j n_ij p_ij (1-p_ij),
            # H_ij = -n_ij p_ij (1-p_ij)
            fisher_full = n_fit * p_mat * (1.0 - p_mat)
            h_sub = -fisher_full[np.ix_(free_indices, free_indices)]
            row_sums = fisher_full.sum(axis=1)
            for pos, i in enumerate(free_indices):
                h_sub[pos, pos] = row_sums[i] + 1e-12

            try:
                step = np.linalg.solve(h_sub, grad)
            except np.linalg.LinAlgError:
                diag = np.diag(h_sub)
                step = grad / np.maximum(diag, 1e-9)

            max_step = float(np.max(np.abs(step)))
            if max_step > 2.0:
                step = step * (2.0 / max_step)

            for pos, i in enumerate(free_indices):
                theta[i] += step[pos]

            if not has_anchor:
                theta -= theta.mean()

            if float(np.max(np.abs(step)) * FOUR_HUNDRED_OVER_LN10) < tol_elo:
                break

    # Calcul final des probabilités prédites et des marges d'incertitude (sur les parties réelles + a priori)
    diff = np.clip(theta[:, None] - theta[None, :], -40.0, 40.0)
    p_mat = 1.0 / (1.0 + np.exp(-diff))
    np.fill_diagonal(p_mat, 0.0)
    info_mat = n_fit * p_mat * (1.0 - p_mat)
    info_sums = info_mat.sum(axis=1)

    results: dict[str, EloEstimate] = {}
    for i, aid in enumerate(unique_ids):
        if has_anchor and aid == anchor_id:
            elo_val = float(anchor_elo)
            sigma = 0.0
            margin = 0.0
            is_prov = False
        else:
            elo_val = float(anchor_elo + theta[i] * FOUR_HUNDRED_OVER_LN10)
            info_i = float(info_sums[i])
            if info_i > 1e-12:
                sigma = FOUR_HUNDRED_OVER_LN10 / math.sqrt(info_i)
                margin = 1.96 * sigma
            else:
                sigma = 999.0
                margin = 999.0
            is_prov = margin > provisional_margin

        results[aid] = EloEstimate(
            agent_id=aid,
            elo=round(elo_val, 1),
            margin=round(margin, 1),
            sigma=round(sigma, 2),
            provisional=is_prov,
            games_played=games_played[aid],
            wins=wins[aid],
            draws=draws[aid],
            losses=losses[aid],
        )
    return results


def estimate_single_agent_elo(
    candidate_id: str,
    matches: Iterable[MatchRecord | tuple[str, str, str]],
    fixed_elos: Mapping[str, float],
    anchor_id: str | None = None,
    anchor_elo: float = 1000.0,
    prior_draws: float = 2.0,
) -> float:
    """Recalcule l'Elo d'un seul agent (en placement) avec les autres Elo fixes."""
    obs: list[tuple[float, float, float]] = []  # (opp_theta, n, w)
    by_opp: dict[str, list[float]] = {}
    for item in matches:
        left_id, right_id, outcome = (
            (item.left_id, item.right_id, item.outcome)
            if isinstance(item, MatchRecord)
            else item
        )
        s_left = _score_from_outcome(outcome)
        if left_id == candidate_id and right_id in fixed_elos:
            opp_id, score = right_id, s_left
        elif right_id == candidate_id and left_id in fixed_elos:
            opp_id, score = left_id, 1.0 - s_left
        else:
            continue
        cur = by_opp.setdefault(opp_id, [0.0, 0.0])
        cur[0] += 1.0
        cur[1] += score

    for opp_id, (n_ij, w_ij) in by_opp.items():
        opp_theta = (fixed_elos[opp_id] - anchor_elo) * LN10_OVER_400
        obs.append((opp_theta, n_ij, w_ij))

    if prior_draws > 0.0:
        anchor_theta = 0.0
        if anchor_id and anchor_id in fixed_elos:
            anchor_theta = (fixed_elos[anchor_id] - anchor_elo) * LN10_OVER_400
        obs.append((anchor_theta, prior_draws, 0.5 * prior_draws))

    if not obs:
        return anchor_elo

    theta = 0.0
    for _ in range(100):
        grad = 0.0
        hess = 1e-12
        for opp_theta, n_ij, w_ij in obs:
            d = max(-40.0, min(40.0, theta - opp_theta))
            p = 1.0 / (1.0 + math.exp(-d))
            grad += w_ij - n_ij * p
            hess += n_ij * p * (1.0 - p)
        step = max(-2.0, min(2.0, grad / hess))
        theta += step
        if abs(step) * FOUR_HUNDRED_OVER_LN10 < 1e-6:
            break

    return round(anchor_elo + theta * FOUR_HUNDRED_OVER_LN10, 1)


def choose_next_placement_opponent(
    ranked_agents: Sequence[EloEstimate],
    already_played_ids: set[str],
    candidate_elo: float,
    candidate_id: str | None = None,
) -> EloEstimate | None:
    """Choisit le prochain adversaire de placement selon la spec.

    1. Premier adversaire : agent médian du classement, non provisoire si possible.
    2. Suivants : agent pas encore affronté le plus proche de `candidate_elo`.
    """
    candidates = [
        a for a in ranked_agents
        if a.agent_id != candidate_id and a.agent_id not in already_played_ids
    ]
    if not candidates:
        return None

    if not already_played_ids:
        non_prov = [a for a in candidates if not a.provisional]
        pool = non_prov if non_prov else candidates
        pool_sorted = sorted(pool, key=lambda a: (a.elo, a.agent_id))
        return pool_sorted[len(pool_sorted) // 2]

    return min(candidates, key=lambda a: (abs(a.elo - candidate_elo), a.agent_id))


def choose_refinement_pair(
    ranked_agents: Sequence[EloEstimate],
    pair_game_counts: Mapping[tuple[str, str], int],
    target_margin: float = 50.0,
    max_games_per_pair: int = 100,
    anchor_id: str | None = None,
) -> tuple[EloEstimate, EloEstimate] | None:
    """Choisit une paire pour l'affinage en tâche de fond.

    - Agent actif dont la marge est la plus grande (> target_margin)
    - Adversaire le plus proche en Elo avec moins de `max_games_per_pair` parties.
    """
    if len(ranked_agents) < 2:
        return None

    def _pair_count(a_id: str, b_id: str) -> int:
        key = (a_id, b_id) if a_id < b_id else (b_id, a_id)
        return pair_game_counts.get(key, 0)

    # Agents éligibles : marge > target_margin (et pas uniquement l'ancre dont la marge vaut 0)
    eligible_focus = [
        a for a in ranked_agents
        if a.agent_id != anchor_id and a.margin > target_margin
    ]
    eligible_focus.sort(key=lambda a: (-a.margin, a.games_played, a.agent_id))

    for focus in eligible_focus:
        opponents = [
            b for b in ranked_agents
            if b.agent_id != focus.agent_id and _pair_count(focus.agent_id, b.agent_id) < max_games_per_pair
        ]
        if not opponents:
            continue
        best_opp = min(
            opponents,
            key=lambda b: (abs(b.elo - focus.elo), _pair_count(focus.agent_id, b.agent_id), b.agent_id),
        )
        # Éviter qu'un agent extrême (ex: dummy_bot à -700 Elo) ayant déjà atteint son quota contre
        # ses voisins ne bloque l'affinage avec des adversaires à >600 Elo d'écart (p*(1-p) < 0.03).
        if abs(best_opp.elo - focus.elo) > 600.0 and focus.games_played >= 20:
            continue
        return focus, best_opp

    return None
