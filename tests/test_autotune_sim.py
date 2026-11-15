"""The Autotune simulator (tools/autotune_sim.py): its games against the recordings they are made from
(tests/data/autotune/, a Claw 8 EX, 2026-11-14), its score, and Autotune as it is today on its scenarios.
Run: python3 -m pytest -q tests"""
import bisect
import importlib.util
import os
import statistics
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA = os.path.join(os.path.dirname(__file__), "data", "autotune")


def _load():
    spec = importlib.util.spec_from_file_location("autotune_sim", os.path.join(ROOT, "tools", "autotune_sim.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["autotune_sim"] = m             # dataclasses look their module up by name
    spec.loader.exec_module(m)
    return m


sim = _load()


def _answer(ends, frame_ms, t_write, pace_ms, n, slow):
    """Seconds from a limit write until the median of the last n frames is (slow) or is no longer (not slow) more than
    5 % over the pace."""
    for k in range(bisect.bisect_left(ends, t_write), len(frame_ms)):
        if (statistics.median(frame_ms[max(0, k - n + 1):k + 1]) > pace_ms * 1.05) == slow:
            return ends[k] - t_write
    return None


def _ends(frame_ms):
    out, t = [], 0.0
    for ms in frame_ms:
        t += ms / 1000.0
        out.append(t)
    return out


# ---- the games are the recordings ------------------------------------------------------------------------------------
@pytest.mark.parametrize("recording, scene, levels", [("enshrouded-altar.csv", "enshrouded-altar", 9), ("mcla-parked.csv", "mcla", 6)])
def test_a_scene_table_is_what_its_recording_shows(recording, scene, levels):
    rec = sim.Recording.load(os.path.join(DATA, recording))
    got = rec.levels()[:levels]                  # the staircase; what follows it is the way back up
    assert len(got) == levels
    for watts, fps, _median, _slow, _seconds in got:
        assert fps == pytest.approx(sim.SCENES[scene].shown(watts), rel=0.015), f"{scene} at {watts:g} W"


@pytest.mark.parametrize("scene, watts", [("enshrouded-altar", 30), ("enshrouded-altar", 24), ("enshrouded-altar", 18),
                                          ("enshrouded-altar", 12), ("enshrouded-altar", 8), ("mcla", 30), ("mcla", 20),
                                          ("mcla", 12), ("mcla", 8), ("nfs-mw", 30), ("nfs-mw", 8)])
def test_the_game_shows_the_recorded_frame_rate_at_each_limit(scene, watts):
    game = sim.run(sim.Fixed(watts), sim.Game([(30, scene, 1.0)], seed=2))
    assert game.score().fps == pytest.approx(sim.SCENES[scene].shown(watts), rel=0.02)


def test_the_emulators_edge_is_scattered_slow_frames_with_the_median_on_pace():
    """Midnight Club at 15 W: 29.7 fps, the median frame on the 33.3 ms pace, 6.4 % of the frames more than 10 % slow.
    A governor that looks at the median alone doesn't see this level as a loss."""
    rec = sim.Recording.load(os.path.join(DATA, "mcla-parked.csv"))
    _w, fps, median, slow, _s = next(l for l in rec.levels() if l[0] == 15)
    assert fps == pytest.approx(29.7, abs=0.1) and median == pytest.approx(33.3, abs=0.2) and 0.04 < slow < 0.09
    game = sim.Game([(40, "mcla", 1.0)], seed=2)
    game.trace = []
    sim.run(sim.Fixed(15), game)
    ft = [row[3] for row in game.trace if row[0] > 2]
    assert 1000 * len(ft) / sum(ft) == pytest.approx(29.7, abs=0.5)
    assert statistics.median(ft) == pytest.approx(33.3, abs=0.4)
    assert 0.04 < sum(1 for x in ft if x > 1.1 * 33.33) / len(ft) < 0.09


def test_frames_answer_a_new_limit_as_fast_as_they_did_on_the_claw():
    """Recorded: 15 -> 12 W, the median of 15 frames is 5 % over the pace 0.35 s after the write; 8 -> 30 W, the
    median of 5 frames is back on the pace after 0.28 s. The simulated chip has to be in the same range."""
    rec = sim.Recording.load(os.path.join(DATA, "mcla-parked.csv"))
    writes = {w: s for s, w in rec.limits[1:]}
    ends = _ends(rec.frame_ms)
    assert 0.15 <= _answer(ends, rec.frame_ms, writes[12], 33.33, 15, True) <= 0.6
    assert 0.1 <= _answer(ends, rec.frame_ms, rec.limits[-1][0], 33.33, 5, False) <= 0.5
    for seed in (1, 2, 3):
        game = sim.Game([(60, "mcla", 1.0)], seed=seed)
        game.trace = []
        for until, watts in ((20, 15), (30, 12), (40, 8), (50, 30)):
            game.set_limit(watts)
            game.advance(until)
        ends, ft = [row[0] for row in game.trace], [row[3] for row in game.trace]
        assert 0.15 <= _answer(ends, ft, 20, 33.33, 15, True) <= 0.6
        assert 0.1 <= _answer(ends, ft, 40, 33.33, 5, False) <= 0.5


def test_the_jumping_need_of_the_scenario_is_the_recorded_one():
    """Midnight Club driving: the full 30 W held the 30 fps lock in one 20 s stretch and gave 23.5 fps in another,
    and 14 W went from 18.6 fps to holding the lock for part of a stretch. The scenario's heaviest place is that 23.5."""
    rec = sim.Recording.load(os.path.join(DATA, "mcla-driving.csv"))
    at_full = [fps for watts, fps, _m, _slow, secs in rec.levels() if watts == 30 and secs > 15]
    at_14 = [fps for watts, fps, _m, _slow, secs in rec.levels() if watts == 14 and secs > 15]
    assert min(at_full) == pytest.approx(23.5, abs=0.2) and max(at_full) == pytest.approx(30.0, abs=0.1)
    assert min(at_14) < 19 and max(at_14) > 24.5
    heaviest = min(work for _s, _scene, work in sim.SCENARIOS["emulator-need-jumps"])
    assert sim.SCENES["mcla"].shown(30, heaviest) == pytest.approx(min(at_full), rel=0.03)
    game = sim.Game(sim.SCENARIOS["emulator-need-jumps"])
    needs = {game.need(*game.place(t)) for t in range(10, 400, 20)}
    assert min(needs) == 14 and max(needs) == 28          # 28 W gives the heaviest place 95 % of what 30 W gives


# ---- the score -------------------------------------------------------------------------------------------------------
def test_the_score_of_a_limit_that_never_moves():
    full = sim.run(sim.Fixed(30), sim.Game(sim.SCENARIOS["emulator-parked"])).score()
    assert (full.need_w, full.over_w, full.starved_pct, full.lost_pct) == (14.0, 16.0, 0.0, 0.0)   # 16 W for nothing
    assert full.draw_w == 30.0                                         # RPCS3 takes the whole limit
    low = sim.run(sim.Fixed(8), sim.Game(sim.SCENARIOS["emulator-parked"])).score()
    assert low.over_w == 0.0 and low.starved_pct > 99 and low.longest_starved_s > 295
    assert low.lost_pct == pytest.approx(100 * (1 - 17.4 / 30), abs=1.5)                # 17.4 of 30 fps at 8 W
    light = sim.run(sim.Fixed(30), sim.Game(sim.SCENARIOS["light-capped"])).score()
    assert light.draw_w == pytest.approx(9.0, abs=0.1) and light.need_w == 8.0          # a limit it doesn't reach


def test_a_goal_under_what_the_full_limit_gives_is_what_the_score_asks_for():
    """Target mode: frames over the target are not owed. Enshrouded at the altar gives 55.7 fps at 30 W; a 40 fps
    target is held from 16 W."""
    free = sim.Game(sim.SCENARIOS["uncapped"])
    held = sim.Game(sim.SCENARIOS["uncapped"], goal_fps=40)
    scene = sim.SCENES["enshrouded-altar"]
    assert free.need(scene, 1.0) == 24 and held.need(scene, 1.0) == 15


def test_the_need_moves_with_the_scenario():
    game = sim.Game(sim.SCENARIOS["cap-need-moves"])
    needs = [game.need(*game.place(t)) for t in (10, 190, 370, 550, 730, 910)]
    assert needs == [21, 24, 22, 24, 21, 23]


# ---- Autotune as it is today -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("target", [None, 60])
def test_today_a_light_game_on_its_frame_limit_ends_at_the_floor(target):
    game = sim.run(sim.Today(target=target), sim.Game(sim.SCENARIOS["light-capped"], goal_fps=target))
    s = game.score()
    assert game.limit == 8 and s.starved_pct == 0 and s.fps == pytest.approx(60, abs=0.2)


def test_today_target_mode_holds_a_parked_emulator_near_its_need():
    for seed in (1, 2, 3):
        s = sim.run(sim.Today(target=30), sim.Game(sim.SCENARIOS["emulator-parked"], seed=seed, goal_fps=30)).score()
        assert s.limit_w <= 17 and s.starved_pct <= 1 and s.changes_per_min <= 2


def test_today_the_limit_stays_inside_floor_and_ceiling_in_every_scenario():
    for name, scenario in sim.SCENARIOS.items():
        for target in (None, 30 if name.startswith("emulator") else 60):
            game = sim.Game(scenario, floor=10, ceiling=25, goal_fps=target)
            game.trace = []
            sim.run(sim.Today(target=target), game, seconds=240)
            assert all(10 <= row[1] <= 25 for row in game.trace), (name, target)


@pytest.mark.xfail(strict=True, reason="today a raise is +2 to +6 W, one every 2 s: 6-7 s under the lock after a jump")
def test_a_jump_of_the_need_is_answered_within_a_second_and_a_half():
    """Midnight Club driving: within 20 s the game goes from holding 30 fps at 14 W to needing more than 30 W."""
    s = sim.run(sim.Today(target=30), sim.Game(sim.SCENARIOS["emulator-need-jumps"], goal_fps=30)).score()
    assert s.longest_starved_s <= 1.5


@pytest.mark.xfail(strict=True, reason="today a target out of reach keeps the full limit: 30 W where 22 W gives the same")
def test_a_target_out_of_reach_is_not_a_reason_for_the_full_limit():
    """Enshrouded where 30 W gives 48 fps and 22 W gives 46: a 60 fps target can't be held at any limit."""
    s = sim.run(sim.Today(target=60), sim.Game(sim.SCENARIOS["out-of-reach"], goal_fps=60)).score()
    assert s.over_w <= 2 and s.starved_pct <= 1


# ---- replay: recorded frames, whatever the governor asks -------------------------------------------------------------
def test_replay_today_sees_the_recorded_loss_and_nothing_before_it():
    """Midnight Club parked, the limit stepped 30, 20, 15, 12 W by a recorder: the lock holds until the step to 12 W
    (24.9 fps). Today's target mode raises 2 s after that write, and not before it."""
    rec = sim.run(sim.Today(target=30), sim.Recording.load(os.path.join(DATA, "mcla-parked.csv")))
    lost_at = next(s for s, w in rec.limits if w == 12)
    raises = [s for (s, w), (_, before) in zip(rec.asked[1:], rec.asked) if w > before]
    assert raises and raises[0] > lost_at
    assert raises[0] - lost_at <= 3.0
    assert rec.asked[-1][1] == 30                                      # 10 and 8 W follow: it ends at the full limit


def test_the_tool_prints_a_table(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["autotune_sim.py", "--scenario", "light-capped", "--seconds", "30"])
    assert sim.main() == 0
    out = capsys.readouterr().out
    assert "fixed 30 W" in out and "today, most frames" in out and "today, target 60" in out
