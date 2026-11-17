"""The Autotune governor in the simulator's games (tools/autotune_sim.py) and on a recording (tests/data/autotune/).
Its rules on hand-made frames are in test_governor.py. Run: python3 -m pytest -q tests"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA = os.path.join(os.path.dirname(__file__), "data", "autotune")


def _load():
    spec = importlib.util.spec_from_file_location("autotune_sim", os.path.join(ROOT, "tools", "autotune_sim.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["autotune_sim"] = m
    spec.loader.exec_module(m)
    return m


sim = _load()


def _score(name, target, seed=1, seconds=None, repeat=1):
    g = sim.New(target=target)
    return sim.run(g, sim.Game(sim.SCENARIOS[name] * repeat, seed=seed, goal_fps=target), seconds).score()


@pytest.mark.parametrize("target", [None, 60])
def test_a_light_game_on_its_frame_limit_is_at_the_floor_within_half_a_minute(target):
    game = sim.Game(sim.SCENARIOS["light-capped"], goal_fps=target)
    sim.run(sim.New(target=target), game, seconds=30)
    assert game.limit == 8
    s = _score("light-capped", target)
    assert s.over_w <= 0.5 and s.starved_pct == 0 and s.fps == pytest.approx(60, abs=0.2)


@pytest.mark.parametrize("target", [30, 60, None])
def test_a_jump_of_the_need_is_answered_within_seconds(target):
    """Midnight Club driving: within 20 s the game goes from holding 30 fps at 14 W to needing more than 30 W. With
    its own 30 fps as the target, with a target it can't reach, and with none: no stretch of 3 s under what the full
    limit gives, in twelve games."""
    for seed in range(1, 13):
        game = sim.Game(sim.SCENARIOS["emulator-need-jumps"], seed=seed, goal_fps=min(target, 30) if target else None)
        s = sim.run(sim.New(target=target), game).score()
        assert s.longest_starved_s <= 3.0 and s.starved_pct <= 3.0, (target, seed, s)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_target_out_of_reach_is_not_a_reason_for_the_full_limit(seed):
    """Enshrouded where 30 W gives 48 fps and 22 W gives 46: a 60 fps target can't be held at any limit. The limit
    comes down to where 2 % of the frames are lost (a step has to leave 3 % over the goal of 5 % less)."""
    s = _score("out-of-reach", 60, seed)
    assert s.limit_w <= 28 and s.lost_pct <= 2.5 and s.starved_pct <= 1


@pytest.mark.parametrize("name, target", [("uncapped", None), ("cap-need-moves", 60), ("cap-need-moves", None)])
def test_a_gpu_bound_game_is_held_near_its_need_without_long_losses(name, target):
    for seed in (1, 2, 3):
        s = _score(name, target, seed)
        assert s.over_w <= 3 and s.starved_pct <= 1.5 and s.longest_starved_s <= 3


def test_a_parked_emulator_is_held_over_its_edge_not_on_it():
    """Today's target mode holds Midnight Club at 15.4 W with 6 % of the frames slow; the edge rule gives it a watt more
    and takes the slow frames away."""
    for seed in (1, 2, 3):
        s = _score("emulator-parked", 30, seed)
        assert s.over_w <= 3.5 and s.slow_pct <= 2 and s.starved_pct <= 0.5


@pytest.mark.parametrize("name, target", [("uncapped", None), ("out-of-reach", 60), ("emulator-parked", 30), ("light-capped", 60)])
def test_a_steady_scene_is_left_alone_after_the_first_minutes(name, target):
    first = sim.Game(sim.SCENARIOS[name] * 4, goal_fps=target)
    sim.run(sim.New(target=target), first, seconds=300)
    whole = sim.Game(sim.SCENARIOS[name] * 4, goal_fps=target)
    sim.run(sim.New(target=target), whole)
    assert (whole.changes - first.changes) / 15.0 <= 1.0             # changes a minute, minutes 5 to 20



@pytest.mark.parametrize("name", list(sim.SCENARIOS))
def test_the_chargers_30_w_stop_under_a_35_w_pick_costs_no_frames(name):
    """With a charger in, the package settles at 30 W whatever the limit says (a model: `Game(stop_w=30)`): a look at
    the full 35 W shows what the first minute gives. That costs nothing against the same pick without the stop, and no
    more frames than a 30 W pick. Means of six runs: one run's figures move by a few percent
    (emulator-need-jumps, target 30: 0.4 to 5.1 % under the line at a 35 W pick, with or without the stop; no target:
    the limit of one run 31.7 to 33.9 W, the two means 0.55 W apart over six runs and 0.18 W over 24)."""
    scenario = sim.SCENARIOS[name]
    for target in (None, 30 if name.startswith("emulator") else 60):
        def runs(**kw):
            return [sim.run(sim.New(target=target), sim.Game(scenario, goal_fps=target, seed=seed, **kw)).score() for seed in range(1, 7)]
        at30, at35, stop = runs(ceiling=30), runs(ceiling=35), runs(ceiling=35, stop_w=30.0)
        mean = lambda rs, what: sum(getattr(r, what) for r in rs) / len(rs)
        case = (name, target, [(r.limit_w, r.starved_pct) for r in at30], [(r.limit_w, r.starved_pct) for r in at35],
                [(r.limit_w, r.starved_pct) for r in stop])
        assert abs(mean(stop, "starved_pct") - mean(at35, "starved_pct")) <= 0.5, case
        assert abs(mean(stop, "limit_w") - mean(at35, "limit_w")) <= 1.0, case   # (the limit is the one without the stop)
        assert mean(stop, "starved_pct") <= mean(at30, "starved_pct") + 1.5, case


def test_the_limit_stays_inside_floor_and_ceiling_in_every_scenario():
    for name, scenario in sim.SCENARIOS.items():
        for target in (None, 30 if name.startswith("emulator") else 60):
            game = sim.Game(scenario, floor=10, ceiling=25, goal_fps=target)
            sim.run(sim.New(target=target), game, seconds=240)
            assert game.asked and all(10 <= w <= 25 for w in game.asked), (name, target)   # as asked, not as clamped


# ---- on a recording --------------------------------------------------------------------------------------------------
def test_replay_the_recorded_loss_is_answered_within_a_second_and_the_edge_before_it_is_seen():
    """Midnight Club parked, the limit stepped 30, 20, 15, 12 W by a recorder (the governor's own writes are not
    obeyed in a replay). 30 and 20 W hold the lock: no raise. 15 W is the edge (6 % slow frames, some of them three in a
    row): it asks for more. 12 W loses the lock (24.9 fps): the full limit within a second of that write; the target
    governor before this one took 2 s."""
    g = sim.New(target=30)
    rec = sim.run(g, sim.Recording.load(os.path.join(DATA, "mcla-parked.csv")))
    at = {w: s for s, w in rec.limits[1:]}
    raises = [(s, w, before) for (s, w), (_, before) in zip(rec.asked[1:], rec.asked) if w > before]
    assert not [r for r in raises if r[0] < at[15] and r[1] == 30]        # never the full limit while the lock holds
    assert [r for r in raises if at[15] < r[0] < at[12]]                  # the edge at 15 W: it asks for more
    asked = [w for s, w in rec.asked if s <= at[12] + 1.0]
    assert asked[-1] == 30                                                # a second into the loss: the full limit
