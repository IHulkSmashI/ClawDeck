"""Review 3: experiments and failing tests on governor.step alone (the harness of tests/test_governor.py)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))
from test_governor import Game  # noqa: E402
from clawdeck import governor as gov  # noqa: E402


def test_r3_steam_menu_over_a_30fps_game_moves_nothing():
    """R7: the Steam menu over a game for 30 s, then back: no step taken from the menu's frames. Here the menu shows
    60 fps over a game locked at 30 (a 2x rate, inside OFF_HI = 2.5, so it is judged as the game)."""
    menu = {"on": False}
    fps = lambda w, t: 60.0 if menu["on"] else (30.0 if w >= 14 else 2.08 * w)
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=fps).run(120)
    held, n = g.limit, len(g.writes)
    print("before the menu: limit", held, "writes", g.writes[-6:])
    menu["on"] = True
    g.run(30)
    print("menu writes", g.writes[n:], "ref", g.st.ref, "fail", g.st.fail)
    m = len(g.writes)
    menu["on"] = False
    g.run(60)
    print("after writes", g.writes[m:], "ref", g.st.ref, "limit", g.limit)
    print(g.notes[-12:])
    assert g.writes[n:m] == [], "the menu's frames moved the limit"


from test_governor import HOLDS_FROM_14  # noqa: E402


def _into_retest():
    """A level held for a while, one dip of the game's own: full limit, then the level once more (a retest probe)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=HOLDS_FROM_14).run(60)
    held, t0 = g.limit, g.t
    g.fps = lambda w, t: HOLDS_FROM_14(w) * (0.75 if t0 <= t < t0 + 0.8 else 1.0)
    while not (g.st.mode == "probe" and g.st.retest):
        g.run(0.1)
        assert g.t < t0 + 10
    return g, held


def test_r3_a_guard_cap_during_a_second_try_leaves_no_second_try_behind():
    """The guard's cap arrives while a level is tried once more (retest). governor.step goes to hold, but `retest`
    stays True, so the next ordinary step down that holds is booked as "the game's own dip" and ends the run of steps."""
    g, held = _into_retest()
    g.cap = held - 1                                    # the guard: under the level being tried
    g.run(0.2)
    assert g.st.mode == "hold"
    print("after the cap: mode", g.st.mode, "retest", g.st.retest)
    assert not g.st.retest, "a second try survived the guard's cap"


def test_r3_autotune_thermal_step_during_a_second_try_leaves_no_second_try_behind():
    """autotune.thermal_step sets gov.mode = "hold" itself (and the refused-write path does the same): retest/tried stay."""
    g, held = _into_retest()
    g.st.mode = "hold"                                  # what Autotune.thermal_step and a refused write do
    dips = len(g.st.dips)
    g.fps = HOLDS_FROM_14
    g.draw = None
    g.run(60)
    print("dips", dips, "->", len(g.st.dips), [n for t, n in g.notes if "own dip" in n])
    assert len(g.st.dips) == dips, "an ordinary step that held was booked as one of the game's own dips"


def test_r3_what_the_stale_second_try_does_next():
    g, held = _into_retest()
    g.cap = held - 1
    g.run(0.2)
    g.fps = lambda w: 60.0 if w >= 10 else 45.0          # the next step down holds
    n = len(g.notes)
    g.run(150)
    print("held", held, "notes:", g.notes[n:n + 8], "dips", len(g.st.dips), "good", g.st.good, "limit", g.limit)
    assert not any("own dip" in x for _, x in g.notes[n:]), "an ordinary step that held was booked as a dip of the game's own"


def test_r3_a_raise_cut_short_by_autotune_leaves_no_tried_level_behind():
    """A step down loses far more than its size explains: `recover` with `tried` = that level. Autotune.thermal_step
    (or a refused write) sets mode = "hold" itself. `tried` stays, and the next loss in hold is judged as that old
    step's: the old level is failed again and the limit goes back to the level before, not judged on its own."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=22), fps=lambda w: 60.0 if w >= 20 else 40.0)
    while not (g.st.mode == "recover" and g.st.tried is not None):
        g.run(0.1)
        assert g.t < 60
    tried = g.st.tried
    g.st.mode = "hold"                                  # Autotune.thermal_step / refused write
    g.run(20)
    print("tried left behind:", g.st.tried, "fail", g.st.fail)
    assert g.st.tried is None, f"tried={tried} W survived the forced hold"


def test_r3_a_target_out_of_reach_from_the_first_look_pins_the_start_level_for_two_minutes():
    """R1 (work plan): Midnight Club LA parked, RPCS3's 30 fps lock, target 60: at or under 17 W within 60 s. The
    first look at the full limit compares 30 fps with the 60 fps target as if the target had been held, takes it for
    "a cutscene", and keeps the start level (mid-range, 19 W) as a floor for REACH_FOR_S."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=lambda w: 30.0 if w >= 14 else 2.08 * w)
    g.draw = lambda w, t: float(w)                       # RPCS3 draws the whole limit
    g.run(60)
    print("limit at 60 s:", g.limit, "drop_floor", g.st.drop_floor, "writes", g.writes[:6])
    assert g.limit <= 17


def test_r3_without_a_package_power_reading_the_pin_never_ends():
    """The same with no RAPL energy reading (draw_w None: _next_down keeps drop_floor while `obs.draw_w is None`)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=lambda w: 30.0 if w >= 14 else 2.08 * w)
    g.run(900)
    print("limit at 900 s:", g.limit, "drop_floor", g.st.drop_floor)
    assert g.limit <= 17


def test_r3_a_deep_loss_gets_the_full_limit_once_not_full_low_full():
    """A game held at a low limit walks into a place that needs far more: at the held limit it gives under 35 % of its
    usual rate. The loss writes the full limit (recover); the next look still has the starved frames in its last 10,
    reads them as "too few frames to judge on" and drops the watch: back to the starving limit, then the full limit
    again half a second later."""
    heavy = {"on": False}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=12),
             fps=lambda w, t: (60.0 if w >= 10 else 6.0 * w) if not heavy["on"] else (60.0 if w >= 28 else 15.0))
    g.run(60)
    held, n = g.limit, len(g.writes)
    heavy["on"] = True
    g.run(3)
    print("held", held, "writes after the jump:", g.writes[n:], g.notes[-4:])
    assert [w for _, w in g.writes[n:]] == [30], g.writes[n:]


def test_r3_slow_frames_of_the_games_own_at_every_limit_do_not_walk_the_limit_up():
    """A 30 fps game held at its edge; it enters a place with 6 % of its frames 15 % slow at EVERY limit (its own
    uneven pacing, not the limit's). base_slow is the lowest share ever seen under the full limit (min(), never
    reset by a scene change), so the edge rule reads the new place as the limit's edge: +1 W every 10 s."""
    rough = {"on": False}
    def shape(i, w, t):
        return 1.15 if rough["on"] and i % 17 == 0 else 1.0
    g = Game(gov.Cfg(floor=8, ceiling=30, target=30), fps=lambda w: 30.0 if w >= 14 else 2.1 * w, shape=shape)
    g.draw = lambda w, t: float(w)
    g.run(120)
    held, n = g.limit, len(g.writes)
    rough["on"] = True
    g.run(120)
    ups = [(t, w) for t, w in g.writes[n:]]
    print("held", held, "base_slow", round(g.st.base_slow, 3), "writes in the rough place:", ups[:12], [x for x in g.notes[-6:]])
    looks = [w for _, w in ups if w == 30]                   # one look at the full limit says whose they are
    assert len(looks) <= 1 and max([w for _, w in ups if w != 30] + [held]) <= held + 1, ups
