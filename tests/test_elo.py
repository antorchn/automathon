"""Tests du calcul Elo Bradley-Terry, des marges, du placement et de l'affinage."""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arene.elo import (  # noqa: E402
    EloEstimate,
    MatchRecord,
    choose_next_placement_opponent,
    choose_refinement_pair,
    estimate_single_agent_elo,
    solve_bradley_terry,
)


def _simulate_matches(true_elos: dict[str, float], games_per_pair: int = 80, seed: int = 123) -> list[MatchRecord]:
    rng = random.Random(seed)
    ids = list(true_elos.keys())
    matches: list[MatchRecord] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            p_a = 1.0 / (1.0 + 10.0 ** ((true_elos[b] - true_elos[a]) / 400.0))
            for _ in range(games_per_pair):
                r = rng.random()
                outcome = "left" if r < p_a else "right"
                matches.append(MatchRecord(left_id=a, right_id=b, outcome=outcome))
    return matches


def test_bradley_terry_recovers_known_elos_within_margin():
    true_elos = {
        "rush_bot": 1000.0,
        "dummy_bot": 720.0,
        "bot_a": 880.0,
        "bot_b": 1040.0,
        "bot_c": 1180.0,
        "bot_d": 1310.0,
        "bot_e": 1430.0,
        "bot_f": 1520.0,
    }
    matches = _simulate_matches(true_elos, games_per_pair=120, seed=42)
    est = solve_bradley_terry(
        list(true_elos.keys()),
        matches,
        anchor_id="rush_bot",
        anchor_elo=1000.0,
        prior_draws=2.0,
        provisional_margin=75.0,
    )

    assert est["rush_bot"].elo == 1000.0
    assert est["rush_bot"].margin == 0.0
    assert est["rush_bot"].provisional is False

    within = 0
    for aid, true_r in true_elos.items():
        if aid == "rush_bot":
            continue
        assert abs(est[aid].elo - true_r) < 60.0
        if abs(est[aid].elo - true_r) <= est[aid].margin:
            within += 1
    # 95% confidence interval across 7 non-anchor agents -> at least 6/7 inside margin
    assert within >= 6


def test_100_percent_and_0_percent_agents_stay_finite():
    ids = ["rush_bot", "god_bot", "zero_bot"]
    matches = [MatchRecord("god_bot", "rush_bot", "left") for _ in range(20)]
    matches += [MatchRecord("zero_bot", "rush_bot", "right") for _ in range(20)]
    matches += [MatchRecord("god_bot", "zero_bot", "left") for _ in range(20)]

    est = solve_bradley_terry(ids, matches, anchor_id="rush_bot")
    assert math.isfinite(est["god_bot"].elo)
    assert math.isfinite(est["zero_bot"].elo)
    assert est["god_bot"].elo > 1300.0
    assert est["zero_bot"].elo < 700.0


def test_draws_count_half_win():
    ids = ["rush_bot", "equal_bot"]
    matches = [MatchRecord("equal_bot", "rush_bot", "draw") for _ in range(10)]
    est = solve_bradley_terry(ids, matches, anchor_id="rush_bot")
    assert abs(est["equal_bot"].elo - 1000.0) < 1e-6
    assert est["equal_bot"].draws == 10


def test_placement_opponent_selection_median_then_closest():
    ranked = [
        EloEstimate("dummy_bot", 700.0, 40.0, 20.4, False, 100, 10, 0, 90),
        EloEstimate("bot1", 900.0, 45.0, 23.0, False, 100, 30, 0, 70),
        EloEstimate("rush_bot", 1000.0, 0.0, 0.0, False, 200, 100, 0, 100),
        EloEstimate("bot2", 1150.0, 45.0, 23.0, False, 100, 60, 0, 40),
        EloEstimate("bot3", 1300.0, 45.0, 23.0, False, 100, 80, 0, 20),
        EloEstimate("prov_top", 1600.0, 140.0, 71.4, True, 10, 9, 0, 1),
    ]
    played: set[str] = set()
    first = choose_next_placement_opponent(ranked, played, candidate_elo=1000.0)
    assert first is not None
    # Non-provisional agents sorted: dummy_bot(700), bot1(900), rush_bot(1000), bot2(1150), bot3(1300) -> median is rush_bot
    assert first.agent_id == "rush_bot"
    played.add(first.agent_id)

    # Candidate sweeps rush_bot 20-0 -> interim Elo jumps high
    fixed = {a.agent_id: a.elo for a in ranked}
    cand_matches = [MatchRecord("cand", "rush_bot", "left") for _ in range(20)]
    cand_elo = estimate_single_agent_elo("cand", cand_matches, fixed, anchor_id="rush_bot")
    assert cand_elo > 1300.0

    second = choose_next_placement_opponent(ranked, played, candidate_elo=cand_elo)
    assert second is not None
    assert second.agent_id == "prov_top"


def test_refinement_stops_when_all_margins_below_target():
    ranked = [
        EloEstimate("rush_bot", 1000.0, 0.0, 0.0, False, 100, 50, 0, 50),
        EloEstimate("bot_a", 1050.0, 48.0, 24.5, False, 100, 55, 0, 45),
        EloEstimate("bot_b", 950.0, 49.5, 25.2, False, 100, 45, 0, 55),
    ]
    assert choose_refinement_pair(ranked, {}, target_margin=50.0, max_games_per_pair=100, anchor_id="rush_bot") is None

    # One agent still above target_margin -> selected with closest eligible opponent
    ranked_high = [
        EloEstimate("rush_bot", 1000.0, 0.0, 0.0, False, 100, 50, 0, 50),
        EloEstimate("bot_a", 1100.0, 82.0, 41.8, True, 20, 12, 0, 8),
        EloEstimate("bot_b", 1120.0, 60.0, 30.6, False, 50, 30, 0, 20),
        EloEstimate("bot_c", 900.0, 55.0, 28.0, False, 50, 20, 0, 30),
    ]
    pair = choose_refinement_pair(ranked_high, {}, target_margin=50.0, max_games_per_pair=100, anchor_id="rush_bot")
    assert pair is not None
    assert pair[0].agent_id == "bot_a"
    assert pair[1].agent_id == "bot_b"

    # If bot_a vs bot_b already reached max_games_per_pair, falls back to next closest (rush_bot)
    pair2 = choose_refinement_pair(
        ranked_high,
        {("bot_a", "bot_b"): 100},
        target_margin=50.0,
        max_games_per_pair=100,
        anchor_id="rush_bot",
    )
    assert pair2 is not None
    assert pair2[0].agent_id == "bot_a"
    assert pair2[1].agent_id == "rush_bot"
