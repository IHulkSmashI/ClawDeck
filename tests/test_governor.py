"""The Autotune governor (py_modules/clawdeck/governor.py): its rules, on frames written by hand (no simulator, no
recording). Run: python3 -m pytest -q tests"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from clawdeck import governor as gov  # noqa: E402


class Game:
    """Frames at a rate the test sets, handed to the governor as the plugin would: every look_s. fps(watts) or
    fps(watts, t); shape(i, watts, t) stretches frame i (1.0 = as the rate says) for hitches, dips and uneven pacing."""
    def __init__(self, cfg, fps=lambda watts: 60.0, shape=None):
        self.cfg, self.fps, self.shape, self.t, self.end, self.i, self.writes, self.notes = cfg, fps, shape, 0.0, 0.0, 0, [], []
        self.st, act = gov.start(cfg, 0.0)
        self.limit, self.look, self.draw, self.cap, self.thermal, self.mult = act.limit_w, act.look_s, None, None, False, 1.0

    def rate(self):
        try:
            return self.fps(self.limit, self.t)
        except TypeError:
            return self.fps(self.limit)

    def run(self, seconds, frames=True):
        end = self.t + seconds
        while self.t < end - 1e-9:
            self.t += self.look
            out = []
            while frames:
                ms = 1000.0 / self.rate() * (self.shape(self.i, self.limit, self.end) if self.shape else 1.0)
                if self.end + ms / 1000.0 > self.t:
                    break
                self.end += ms / 1000.0
                self.i += 1
                out.append((self.end, ms / self.mult))           # shown frames: `mult` of them per rendered one
            if not frames:
                self.end = self.t
            obs = gov.Obs(t=self.t, frames=out, draw_w=self.draw, cap_w=self.cap, thermal=self.thermal, multiplier=self.mult)
            act = gov.step(self.st, obs, self.cfg)
            if act.limit_w is not None:
                self.limit = act.limit_w
                self.writes.append((round(self.t, 2), act.limit_w))
            if act.note and (not self.notes or self.notes[-1][1] != act.note):
                self.notes.append((round(self.t, 2), act.note))
            self.look = act.look_s
        return self

    def since(self, t):
        return [w for at, w in self.writes if at >= t]


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
    # to 20 W when the full limit shows the scene is the same. Then the level between, which fails too
    assert [w for _, w in g.writes] == [20, 18, 30, 20, 19, 30, 20]
    assert g.writes[2][0] - g.writes[1][0] <= 1.0 and g.writes[5][0] - g.writes[4][0] <= 1.0
    assert g.limit == 20 and set(g.st.fail) == {18, 19}
    g.run(60)
    assert len(g.writes) == 7                            # nothing more for RETRY_S


def test_the_guards_cap_is_obeyed_at_once_and_nothing_is_raised_when_it_goes():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=lambda w: 50.0 if w >= 24 else 40.0).run(3)
    g.cap = 22
    g.run(0.5)
    assert g.limit == 22 and g.writes[-1][1] == 22
    g.run(20)
    assert g.limit <= 22
    g.cap = None                                         # the guard gives the watts back
    n = len(g.writes)
    g.run(20)
    assert 30 in [w for _, w in g.writes[n:]]            # what the full limit gives now is looked at ...
    assert g.st.ref == pytest.approx(50, abs=0.5) and g.limit >= 24   # ... and it gives 10 fps more: it stays up


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


# ---- what the review of 2026-11-15 broke, each with the scenario that broke it ---------------------------------------
HOLDS_FROM_14 = lambda w: 60.0 if w >= 14 else 60.0 * w / 14          # a 60 fps game that needs 14 W


def test_a_30_fps_video_does_not_become_the_reference_the_game_is_then_held_to():
    """A cutscene gives 30 fps at any limit. One look at the full limit said "the scene, not the limit", 30 fps became
    what the full limit gives, the limit went to the floor during the video, and the game ran at 34 fps of 60 for a
    minute afterwards. Now the level that held before the video is the lowest one while that lower reference stands,
    and frames above the reference at a lowered limit have the reference looked at again at once."""
    for seconds in (15, 40, 90):
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=HOLDS_FROM_14).run(60)
        held = g.limit
        assert 14 <= held <= 16
        g.fps = lambda w: 30.0
        g.run(seconds)
        assert g.limit >= held or g.limit == 30, (seconds, g.writes)
        assert min(g.since(60), default=held) >= held             # never under what held the game before
        g.fps = HOLDS_FROM_14
        t0 = g.t
        g.run(30)
        assert g.st.ref == pytest.approx(60, abs=1) and g.limit >= 14 and not gov.out_of_reach(g.st, g.cfg), seconds
        low = [(at, w) for at, w in g.writes if at >= t0 and w < 14]      # steps under what the game needs: each
        assert all(any(at < at2 <= at + 1.0 and w2 >= 14 for at2, w2 in g.writes) for at, w in low), (seconds, g.writes)


def test_a_fast_menu_does_not_pin_the_full_limit():
    """8 s of a 240 fps menu set the reference to 240; the game's 60 fps were then "a loading screen" for good, at the
    full limit. Frames that far from the usual rate are not judged, and when they stay they are the game."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=None, start_w=16), fps=HOLDS_FROM_14).run(60)
    g.fps = lambda w: 240.0
    g.run(8)
    g.fps = HOLDS_FROM_14
    g.run(120)
    assert g.st.mode in ("hold", "probe") and g.limit < 20 and g.st.ref == pytest.approx(60, abs=1)


def test_a_target_far_above_the_game_is_out_of_reach_not_a_loading_screen():
    """Target 120 with a game that gives 40 fps: 40 is under 35 % of 120, so every look was "not gameplay": the full
    limit for the whole session."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=None), fps=lambda w: 40.0 if w >= 12 else 40.0 * w / 12)
    g.run(120)
    assert g.st.ref == pytest.approx(40, abs=1) and g.limit <= 16 and gov.out_of_reach(g.st, g.cfg)


def test_a_loading_screen_is_not_judged_and_a_starved_scene_gets_the_full_limit():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=HOLDS_FROM_14).run(30)
    ref, fail = g.st.ref, dict(g.st.fail)
    g.fps = lambda w: 5.0                                   # far too few frames: a loading screen, or starved
    g.run(4)
    assert g.limit == 30 and g.st.ref == ref and g.st.fail == fail   # the full limit while they come; nothing learned
    g.fps = HOLDS_FROM_14
    g.run(1)
    n = len(g.writes)
    g.fps = lambda w: 400.0                                 # far too many for 3 s: a menu
    g.run(3)
    assert len(g.writes) == n and g.st.usual == pytest.approx(60, abs=1) and (g.st.ref or 60) < 100
    g.fps = lambda w: 20.0 if w >= 12 else 20.0 * w / 12    # ... but frames that stay are the game: a 20 fps stretch
    g.run(60)
    assert g.st.ref == pytest.approx(20, abs=1) and g.limit < 30


def test_a_watch_cut_short_by_a_loading_screen_is_dropped():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=HOLDS_FROM_14).run(30)
    held = g.limit
    g.fps = lambda w: 40.0 if w < 30 else 60.0              # a heavy moment: the full limit ...
    g.run(0.6)
    assert g.limit == 30 and g.st.mode == "recover"
    g.st.watch.clear()                                       # (no frame of the full limit was seen yet)
    g.run(20, frames=False)                                  # ... and then no frames for 20 s
    assert g.limit == held and g.st.mode == "hold" and held not in g.st.fail


def test_the_games_own_dips_are_learned_and_then_waited_out():
    """-20 % for 0.6 s every 12 s, at every limit. Each one was a clear loss: the full limit, "frames are back", the
    level failed, and the limit ended at the top. A loss at a limit that can't be its cause is the game's own; after
    two of them a loss counts only when it is still there a second later."""
    dips = lambda w, t: (60.0 if w >= 14 else 60.0 * w / 14) * (0.8 if t % 12.0 < 0.6 else 1.0)
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=dips).run(400)
    late = g.since(200)
    assert g.limit <= 18 and late.count(30) <= 1, g.writes[-12:]


def test_a_level_that_held_for_a_while_is_tried_once_more_before_it_is_given_up():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=HOLDS_FROM_14).run(60)
    held, t0 = g.limit, g.t
    g.fps = lambda w, t: HOLDS_FROM_14(w) * (0.75 if t0 <= t < t0 + 0.8 else 1.0)     # one dip of the game's own
    g.run(10)
    assert g.since(t0)[:2] == [30, held] and held not in g.st.fail and g.limit == held    # full limit, then once more
    g.fps = lambda w: 60.0 if w >= 20 else 45.0                                           # now the need really rose
    t1 = g.t
    g.run(10)
    assert held in g.st.fail and g.limit >= 20, g.since(t1)


def test_frame_time_spread_widens_what_counts_as_a_loss():
    """9 % frame-time spread moves the rate of 10 frames by 3-4 % all the time: every such wobble was a clear loss."""
    wobble = [1.0 + 0.04 * x for x in (1.0, -1.0, 0.3, -0.6, 1.2, -0.2, -1.1, 0.8, 0.1, -0.5, 0.9, -1.2, 0.4)]
    g = Game(gov.Cfg(floor=8, ceiling=30, target=None, start_w=30), fps=lambda w: min(50.0, 2.5 * w),
             shape=lambda i, w, t: wobble[(i // 10) % len(wobble)]).run(300)      # each run of 10 frames 0-5 % off
    assert g.st.noise > 0.015 and g.limit <= 24 and g.since(100).count(30) <= 2, g.writes[-10:]
    assert gov._spread([16.7] * 60) == 0 and gov._spread([11.7, 21.7] * 30) == pytest.approx(0, abs=1e-9)


def test_uneven_frame_pacing_is_read_as_its_rate():
    """Frames that alternate 11.7 and 21.7 ms are 60 fps. The median of 10 of them is either, and the 90th percentile
    is far from it: the rate was misread and the scene never "steady"."""
    assert gov._fps([11.7, 21.7] * 5) == pytest.approx(59.9, abs=0.1)
    assert gov._fps([16.7] * 9 + [190.0]) == pytest.approx(59.9, abs=0.1)        # a long frame of the game's own
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=HOLDS_FROM_14,
             shape=lambda i, w, t: 0.7 if i % 2 else 1.3).run(120)
    assert 14 <= g.limit <= 16, g.writes


def test_the_edge_rule_counts_slow_frames_of_their_own_time():
    """A level where every 12th frame is 30 % long while the median holds is the edge of the limit: not accepted. The
    same slow frames at every limit are the game's own. And a game that got faster doesn't make its earlier frames
    slow."""
    edge = lambda i, w, t: 1.3 if (w < 16 and i % 12 == 0) else 1.0
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=20), fps=lambda w: 60.0, shape=edge).run(60)
    assert g.limit == 16 and 14 in g.st.fail and any("slow frames" in n for _, n in g.notes)
    own = lambda i, w, t: 1.3 if i % 12 == 0 else 1.0
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=20), fps=lambda w: 60.0, shape=own).run(60)
    assert g.limit == 8 and not any("slow frames" in n for _, n in g.notes)
    faster = lambda w, t: (40.0 if int(t / 30) % 2 else 46.0) if w >= 12 else 30.0
    g = Game(gov.Cfg(floor=8, ceiling=30, target=None, start_w=16), fps=faster).run(300)
    assert not any("slow frames" in n for _, n in g.notes)
    assert gov._edge([33.3] * 70 + [40.0] * 5, 0.0) == 5 and gov._edge([33.3] * 70 + [40.0] * 5, 0.06) == 0


def test_a_small_loss_that_stays_goes_to_the_level_before_not_to_the_full_limit():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=18), fps=HOLDS_FROM_14).run(40)
    held, t0 = g.limit, g.t
    g.fps = lambda w: 60.0 if w > held else 56.0            # 1.8 % under the 57 that count as 60
    g.run(8)
    up = g.since(t0)
    assert up and up[0] in (held + 1, held + 2) and 30 not in up and held in g.st.fail


def test_another_scene_forgets_the_levels_that_failed():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=None, start_w=30), fps=lambda w: min(50.0, 2.5 * w)).run(60)
    assert g.st.fail
    g.fps = lambda w: min(80.0, 5.0 * w)                    # 60 % more frames at the same limit, for good
    g.run(8)
    assert not [w for w in g.st.fail if g.st.fail[w][1] > 1] and g.st.ref == pytest.approx(80, abs=2)


def test_more_frames_at_a_held_limit_has_the_full_limit_looked_at():
    """No target: the limit is held against what the full limit gave. When the held limit gives clearly more than
    then, the full limit gives more too, and the held one may cost more than the keep margin."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=None, start_w=30), fps=lambda w: min(50.0, 2.5 * w)).run(60)
    assert g.limit in (19, 20)
    g.fps = lambda w: 2.9 * w                               # a lighter place, where every watt buys frames
    t0 = g.t
    g.run(12)
    assert 30 in g.since(t0) and g.limit >= 29, g.since(t0)


def test_a_scene_that_is_not_steady_gets_no_step_down():
    jumpy = lambda w, t: 60.0 if int(t * 2) % 2 else 40.0   # half a second each
    g = Game(gov.Cfg(floor=8, ceiling=30, target=40, start_w=30), fps=jumpy).run(30)
    assert g.writes == []
    g = Game(gov.Cfg(floor=8, ceiling=30, target=40, start_w=30), fps=lambda w: 60.0).run(30)
    assert g.writes and g.limit < 30


def test_a_clock_cap_from_the_guard_stops_the_looks_at_the_full_limit_not_the_steps_down():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=None, start_w=30), fps=lambda w: min(50.0, 2.5 * w))
    g.thermal = True
    g.run(60)
    assert g.limit in (19, 20)                              # it steps down under the cap (it used to go only up)
    g.fps = lambda w: 2.9 * w
    t0 = g.t
    g.run(12)
    assert 30 not in g.since(t0)                            # ... and doesn't go to the full limit just to look


def test_after_a_heavier_scene_the_level_that_held_is_tried_first():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=HOLDS_FROM_14).run(60)
    home = g.limit
    g.fps = lambda w: 60.0 * w / 34                         # a much heavier place: under 60 at every limit
    g.run(30)
    assert g.limit > home
    g.fps = HOLDS_FROM_14
    t0 = g.t
    g.run(30)
    assert home in g.since(t0)[:4] and g.limit <= home, g.since(t0)       # a look at the full limit, then home


def test_failed_steps_are_approached_by_halving_and_wait_longer_each_time():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=lambda w: 60.0 if w >= 21 else 50.0).run(200)
    tried = [w for _, w in g.writes if w < 30]
    assert g.limit == 21 and 20 in g.st.fail and 21 not in g.st.fail
    downs = [w for w in tried if w < 21]
    assert len(downs) <= 3                                   # each failed level once (and its neighbour), not again


def test_a_step_that_fails_while_the_scene_changes_ends_at_the_full_limit():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=18), fps=HOLDS_FROM_14).run(4.9)
    g.fps = lambda w: 60.0 * w / 30                         # needs everything from here on
    t0 = g.t
    g.run(3)
    assert g.limit == 30 and g.since(t0)[-1] == 30 and max(at for at, w in g.writes if w == 30) - t0 <= 2.5


def test_the_edge_goes_up_one_watt_at_most_every_ten_seconds():
    edge = lambda i, w, t: 1.3 if (w < 20 and i % 8 == 0 and t > 20) else 1.0
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 60.0, shape=edge)
    g.st.quiet_until, g.st.base_slow = 1e9, 0.0              # no steps down in this test; no slow frames before
    g.run(45)
    ups = [(at, w) for at, w in g.writes if at > 20]
    assert [w for _, w in ups] == [17, 18, 19] or [w for _, w in ups] == [17, 18]
    assert all(b[0] - a[0] >= gov.EDGE_UP_S - 0.6 for a, b in zip(ups, ups[1:]))


def test_without_a_target_two_fps_are_given_up_at_low_frame_rates():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=None, start_w=30), fps=lambda w: 24.0 * (w / 30) ** 0.5).run(120)
    assert gov.goal(g.st, g.cfg) == pytest.approx(g.st.ref - 2.0, abs=0.05)       # 2 fps is more than 5 % here
    assert 24.0 * (g.limit / 30) ** 0.5 >= g.st.ref - 2.1 and g.limit < 30


def test_generated_frames_are_judged_as_the_rendered_ones():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=30, start_w=20), fps=lambda w: 30.0 if w >= 14 else 30.0 * w / 14)
    g.mult = 2.0                                             # 60 frames shown for 30 rendered
    g.run(60)
    assert 14 <= g.limit <= 16 and g.st.usual == pytest.approx(30, abs=0.5)


def test_a_raised_floor_is_obeyed_at_once():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=10), fps=lambda w: 60.0).run(2)
    g.cfg.floor = 15
    g.run(0.6)
    assert g.limit == 15
