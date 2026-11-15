"""The Autotune governor (py_modules/clawdeck/governor.py): its rules on frames written by hand, and what it does in
the simulator's games (tools/autotune_sim.py) and on a recording. Run: python3 -m pytest -q tests"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from clawdeck import governor as gov  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA = os.path.join(os.path.dirname(__file__), "data", "autotune")


def _load():
    spec = importlib.util.spec_from_file_location("autotune_sim", os.path.join(ROOT, "tools", "autotune_sim.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["autotune_sim"] = m
    spec.loader.exec_module(m)
    return m


sim = _load()


class Game:
    """Frames at a rate the test sets, handed to the governor as the plugin would: every look_s."""
    def __init__(self, cfg, fps=lambda watts: 60.0):
        self.cfg, self.fps, self.t, self.writes = cfg, fps, 0.0, []
        self.st, act = gov.start(cfg, 0.0)
        self.limit, self.look, self.draw, self.cap = act.limit_w, act.look_s, None, None

    def run(self, seconds, frames=True):
        end = self.t + seconds
        while self.t < end - 1e-9:
            t0, self.t = self.t, self.t + self.look
            ms = 1000.0 / self.fps(self.limit)
            n = int(self.t * 1000 / ms) - int(t0 * 1000 / ms) if frames else 0
            obs = gov.Obs(t=self.t, frames=[(self.t, ms)] * n, draw_w=self.draw, cap_w=self.cap)
            act = gov.step(self.st, obs, self.cfg)
            if act.limit_w is not None:
                self.limit = act.limit_w
                self.writes.append((round(self.t, 2), act.limit_w))
            self.look = act.look_s
        return self


def test_a_game_with_no_history_starts_mid_range_and_one_with_history_where_it_held():
    st, act = gov.start(gov.Cfg(floor=8, ceiling=30), 0.0)
    assert act.limit_w == 19 and st.mode == "hold"
    assert gov.start(gov.Cfg(floor=8, ceiling=30, start_w=14), 0.0)[1].limit_w == 14
    assert gov.start(gov.Cfg(floor=8, ceiling=20, start_w=27), 0.0)[1].limit_w == 20      # never over your limit
    # where to start next time: the level under which 80 % of the holding time lies; too little time says nothing
    assert gov.start_level({14: 300.0, 16: 100.0, 30: 50.0}, 8, 30) == 16
    assert gov.start_level({14: 20.0}, 8, 30) is None


def test_a_loss_under_the_full_limit_gets_the_full_limit_in_one_write():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 60.0).run(3)
    assert g.limit == 16 and g.writes == []
    g.fps = lambda w: 60.0 if w >= 30 else 45.0          # a heavier scene: only the full limit holds 60 now
    g.run(1.0)
    assert g.writes[0][1] == 30 and g.writes[0][0] <= 3.6            # within the next look and a few frames
    g.run(4)
    assert g.limit == 30 and 16 in g.st.fail                         # the frames came back: 16 W was the cause


def test_a_scene_that_is_heavy_at_every_limit_gets_its_limit_back():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 60.0).run(3)
    g.fps = lambda w: 45.0                               # 45 fps whatever the watts: the scene, not the limit
    g.run(4)
    assert [w for _, w in g.writes] == [30, 16] and 16 not in g.st.fail
    assert gov.goal(g.st, g.cfg) == pytest.approx(45 * 0.95, abs=0.5)   # Auto FPS: what the full limit gives


def test_auto_fps_off_a_target_out_of_reach_keeps_the_full_limit():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16, auto_fps=False), fps=lambda w: 45.0).run(40)
    assert g.limit == 30 and [w for _, w in g.writes] == [30]


def test_steps_down_start_just_over_the_draw_and_end_at_the_floor():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30))
    g.draw = 9.0                                         # a light game on its frame limit: 9 W of the 30 W
    g.run(30)
    assert [w for _, w in g.writes] == [11, 9, 8] and g.limit == 8   # not 28, 26, 24, ...


def test_a_step_down_that_loses_frames_is_taken_back_within_a_second_and_not_tried_again_soon():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=22), fps=lambda w: 60.0 if w >= 20 else 48.0)
    g.run(40)
    # 20 W holds, 18 W loses a fifth of the frames: more than 2 W usually cost, so the full limit at once, and back
    # to 20 W when the full limit shows the scene is the same
    assert [w for _, w in g.writes] == [20, 18, 30, 20]
    assert g.writes[2][0] - g.writes[1][0] <= 1.0
    # 18 W failed, and 19 W with it: a straight line from 48 fps at 18 W to 60 at 20 W puts 19 W under the goal too
    assert g.limit == 20 and set(g.st.fail) == {18, 19}
    g.run(60)
    assert len(g.writes) == 4                            # nothing more for RETRY_S


def test_the_guards_cap_is_obeyed_at_once_and_nothing_is_raised_when_it_goes():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=lambda w: 50.0 if w >= 24 else 40.0).run(3)
    g.cap = 22
    g.run(0.5)
    assert g.limit == 22 and g.writes[-1][1] == 22
    g.run(20)
    assert g.limit <= 22
    g.cap = None                                         # the guard gives the watts back
    n = len(g.writes)
    g.run(0.4)
    assert all(w <= 22 for _, w in g.writes[n:])         # nothing is raised just because it may be
    g.run(20)
    assert 30 in [w for _, w in g.writes[n:]]            # ... but what the full limit gives now is looked at
    assert g.st.ref == pytest.approx(50, abs=0.5)


def test_no_frames_decides_nothing():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 60.0).run(3)
    g.run(20, frames=False)                              # a pause: no frames at all
    assert g.limit == 16 and g.writes == []


def test_two_measured_limits_rule_out_the_levels_between_them():
    # 16 W gave 12.6 fps and 30 W 23.7 (an emulator: frames follow the watts): 22.5 fps needs 28.5 W, so 28 W is out
    assert gov._between(16, 12.6, 30, 23.7, 22.5) == 28
    # 19 W gave 44.8 fps and 30 W 48.5 (a flat answer): 46.1 fps needs 22.9 W, so 22 W is out and 23 W is worth trying
    assert gov._between(19, 44.8, 30, 48.5, 46.1) == 22
    assert gov._between(19, 44.8, 30, 44.8, 46.1) is None            # no gain from the watts: nothing to say


# ---- in the simulator's games ----------------------------------------------------------------------------------------
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


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_jump_of_the_need_is_answered_within_a_second_and_a_half(seed):
    """Midnight Club driving: within 20 s the game goes from holding 30 fps at 14 W to needing more than 30 W."""
    s = _score("emulator-need-jumps", 30, seed)
    assert s.longest_starved_s <= 1.5 and s.starved_pct <= 1


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_target_out_of_reach_is_not_a_reason_for_the_full_limit(seed):
    """Enshrouded where 30 W gives 48 fps and 22 W gives 46: a 60 fps target can't be held at any limit."""
    s = _score("out-of-reach", 60, seed)
    assert s.over_w <= 2.5 and s.starved_pct <= 1


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


def test_the_limit_stays_inside_floor_and_ceiling_in_every_scenario():
    for name, scenario in sim.SCENARIOS.items():
        for target in (None, 30 if name.startswith("emulator") else 60):
            game = sim.Game(scenario, floor=10, ceiling=25, goal_fps=target)
            game.trace = []
            sim.run(sim.New(target=target), game, seconds=240)
            assert all(10 <= row[1] <= 25 for row in game.trace), (name, target)


# ---- on a recording --------------------------------------------------------------------------------------------------
def test_replay_the_recorded_loss_is_answered_within_a_second_and_the_edge_before_it_is_seen():
    """Midnight Club parked, the limit stepped 30, 20, 15, 12 W by a recorder (the governor's own writes are not
    obeyed in a replay). 30 and 20 W hold the lock: no raise. 15 W is the edge (6 % slow frames): +1 W. 12 W loses the
    lock (24.9 fps): the full limit within a second of that write; today's target mode takes 2 s."""
    g = sim.New(target=30)
    rec = sim.run(g, sim.Recording.load(os.path.join(DATA, "mcla-parked.csv")))
    at = {w: s for s, w in rec.limits[1:]}
    raises = [(s, w, before) for (s, w), (_, before) in zip(rec.asked[1:], rec.asked) if w > before]
    assert not [r for r in raises if r[0] < at[15]]
    edge = [r for r in raises if at[15] < r[0] < at[12]]
    assert edge and all(w == before + 1 for _s, w, before in edge)
    full = [s for s, w, _b in raises if s >= at[12] and w == 30]
    assert full and full[0] - at[12] <= 1.0
