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
        self.thin = None

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
                if not self.thin or self.i % self.thin == 0:     # thin: a log that has one frame in `thin` only
                    out.append((self.end, ms / self.mult))       # shown frames: `mult` of them per rendered one
            if not frames:
                self.end = self.t
            draw = self.draw(self.limit, self.t) if callable(self.draw) else self.draw    # watts, or draw(limit, t)
            obs = gov.Obs(t=self.t, frames=out, draw_w=draw, cap_w=self.cap, thermal=self.thermal, multiplier=self.mult)
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
    g.run(3)
    assert 30 in [w for _, w in g.writes[n:]]            # what the full limit gives now is looked at, at once ...
    g.run(17)
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
    and the game's frames, far more than the video's, are the reference again once they stayed ADOPT_FAST_S (until
    then they move nothing: they may be a menu's)."""
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
        g.run(gov.SCENE_S - 2)
        assert g.since(t0) == [], (seconds, g.since(t0))              # the target is met where the limit is: nothing moves
        g.run(12)
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
    g.run(20)                                            # (held for HOME_S again: only RETRY_S stands against a second try)
    g.fps = lambda w: 60.0 if w >= 20 else 45.0                                           # now the need really rose
    t1 = g.t
    g.run(3)
    assert g.since(t1) == [30] and held in g.st.fail, g.since(t1)                         # no second try so soon
    g.run(7)
    assert g.limit >= 20, g.since(t1)


def test_frame_time_spread_widens_what_counts_as_a_loss():
    """9 % frame-time spread moves the rate of 10 frames by 3-4 % all the time: every such wobble was a clear loss."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=None, start_w=30), fps=lambda w: min(50.0, 2.5 * w),
             shape=lambda i, w, t: 1.06 if (i // 30) % 2 else 0.94).run(300)      # half a second 6 % slow, half 6 % fast
    assert g.st.noise > 0.03 and g.limit <= 24 and g.since(100).count(30) <= 1, g.writes[-10:]
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
    n, held = len(g.writes), g.limit
    g.run(gov.SCENE_S - 2)
    # as long as a menu may be: one look at the full limit (the draw is not known), no step down, nothing forgotten
    assert [w for _, w in g.writes[n:]] in ([], [30, held]) and g.limit == held and g.st.fail, g.writes[n:]
    g.run(6)
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
    lower = [w for w in g.since(t0) if w < 28]                             # after a look at the full limit: home at
    assert lower and lower[0] == home and g.limit <= home, g.since(t0)     # once, not 2 W at a time


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
    g.st.slow_at = 20.0                                      # ... as the full limit showed a moment ago
    g.run(45)
    ups = [(at, w) for at, w in g.writes if at > 20]
    assert [w for _, w in ups] == [17, 18, 19] or [w for _, w in ups] == [17, 18]
    assert all(b[0] - a[0] >= 10 - 0.6 for a, b in zip(ups, ups[1:]))


def test_a_watt_that_changes_nothing_has_the_full_limit_say_whose_slow_frames_they_are():
    """Slow frames at a held limit, and as many a watt higher. The limit's (none at the full limit; the edge is some
    watts up): the limit ends over the edge. The game's own in this place (as many at the full limit): one short look,
    then the limit stays (a watt more every ten seconds went up to your limit; review 3, 2026-11-16)."""
    for edge_w, share in ((20, 8), (18, 17), (23, 12)):
        edge = lambda i, w, t: 1.3 if (w < edge_w and i % share == 0 and t > 20) else 1.0
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 60.0, shape=edge)
        g.st.base_slow = 0.0
        g.run(120)
        assert edge_w <= g.limit <= edge_w + 3, (edge_w, share, g.limit, g.writes)
    for held_w, share, pace in ((16, 8, 1.3), (16, 17, 1.15), (12, 12, 1.2)):
        own = lambda i, w, t: pace if (i % share == 0 and t > 20) else 1.0
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=held_w), fps=lambda w: 60.0, shape=own)
        g.st.quiet_until, g.st.base_slow = 1e9, 0.0
        g.run(150)
        late = [(at, w) for at, w in g.writes if at > 20]
        looks = [at for at, w in late if w == 30]
        assert len(looks) <= 1 and g.limit <= held_w + 1, (held_w, share, late)
        assert all(w <= held_w + 1 for _, w in late if w != 30), late



def test_a_menu_over_the_game_takes_no_step_down_whatever_it_shows():
    """A menu or a map over a 30 fps game whose target of 60 is out of reach, for up to ADOPT_FAST_S: 40 to 90 fps at
    any limit. The menu's 60 fps took the limit to the floor, and the game came back at 19 fps (review 3, 2026-11-16).
    No step down from its frames; under the target one look at the full limit; the game back: the limit before."""
    for menu_fps in (40.0, 45.0, 60.0, 72.0, 90.0):
        for seconds in (4, 10, 20, 28):
            menu = {"on": False}
            fps = lambda w, t: menu_fps if menu["on"] else (30.0 if w >= 14 else 2.08 * w)
            g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=fps).run(120)
            held, n = g.limit, len(g.writes)
            menu["on"] = True
            g.run(seconds)
            case = (menu_fps, seconds, g.writes[n:])
            assert all(w >= held for _, w in g.writes[n:]) and len([w for _, w in g.writes[n:] if w == 30]) <= 1, case
            if menu_fps >= 60:
                assert g.writes[n:] == [], case                  # the target is met: nothing to look for
            menu["on"] = False
            m = len(g.writes)
            g.run(60)
            case = (menu_fps, seconds, g.writes[n:])
            low = [(at, w) for at, w in g.writes[m:] if w < held]    # a level that failed two minutes ago, once more:
            assert all(w == held - 1 and any(at < at2 <= at + 1.0 and w2 >= held for at2, w2 in g.writes) for at, w in low), case
            assert g.limit == held and g.st.ref == pytest.approx(30, abs=1), case



@pytest.mark.parametrize("target, game, menus", [
    (60, lambda w: 45.0 if w >= 16 else 45.0 * w / 16, (55.0, 60.0, 80.0)),      # the target out of reach
    (60, lambda w: min(60.0, 60.0 * w / 14), (75.0, 90.0, 120.0)),               # in reach, the menu not frame-limited
    (None, lambda w: min(50.0, 2.5 * w), (60.0, 70.0, 100.0)),                   # most frames
    (30, lambda w: min(30.0, 30.0 * w / 12), (40.0, 60.0))])
def test_a_menu_takes_no_step_down_in_any_mode(target, game, menus):
    for menu_fps in menus:
        for seconds in (4, 10, 20, 28):
            for menu_w in (None, 9.0):                           # the draw unknown, or known: the menu needs 9 W
                menu = {"on": False}
                g = Game(gov.Cfg(floor=8, ceiling=30, target=target), fps=lambda w, t: menu_fps if menu["on"] else game(w))
                if menu_w:
                    g.draw = lambda w, t: min(float(w), menu_w) if menu["on"] else float(w)
                g.run(150)
                held, n = g.limit, len(g.writes)
                menu["on"] = True
                g.run(seconds)
                m = len(g.writes)
                menu["on"] = False
                g.run(60)
                case = (menu_fps, seconds, menu_w, held, g.writes[n:])
                assert all(w >= held for _, w in g.writes[n:m]) and all(w >= held - 1 for _, w in g.writes[m:]), case
                assert abs(g.limit - held) <= 1, case


def test_the_game_back_under_its_target_at_the_limit_a_long_video_needed_gets_the_full_limit_at_once():
    """A 30 fps video for much longer than REACH_FOR_S: the limit goes down to what the video needs. The game is back
    and gives 40 of its 60 fps there: far more frames than the video's, as a menu's would be, but under the target at
    a limit that holds the chip. One look at the full limit after ABOVE_S, not after ADOPT_FAST_S."""
    for need_w in (12, 14, 18):
        game = lambda w: min(60.0, 60.0 * w / need_w)
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=need_w + 2), fps=game).run(60)
        g.fps = lambda w: 30.0
        g.run(120 + 240)
        low = g.limit
        assert low < need_w, (need_w, low)
        g.fps = game
        t0 = g.t
        g.run(2.0 + 6)
        assert g.limit >= need_w and g.st.ref == pytest.approx(60, abs=1), (need_w, low, g.since(t0))



@pytest.mark.parametrize("target", [None, 90, 120])
@pytest.mark.parametrize("draw_known", [False, True])
def test_far_more_frames_at_a_limit_that_holds_them_back_get_the_full_limit_after_a_look(target, draw_known):
    """Far more frames than before are a menu's for ADOPT_FAST_S, but not when the limit holds them back: the game back
    from a 30 fps video at the limit the video needed (46 of 120 fps for 31 s, cloud fuzz seed 51, 2026-11-16), or a
    lighter place that wants more watts. One look at the full limit after ABOVE_S says it, with or without a target
    (a target these frames reach needs no look)."""
    for need_w in (16, 20, 26):
        game = lambda w: 120.0 * min(1.0, w / need_w)
        g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30), fps=game)
        if draw_known:
            g.draw = lambda w, t: float(w)
        g.run(90)
        g.fps = lambda w: 30.0                                        # the video, for longer than the floor stands
        g.run(120 + 240)
        low = g.limit
        assert low < need_w - 2, (need_w, low)
        g.fps = game
        t0 = g.t
        g.run(2.0 + 8)
        assert game(g.limit) >= 114, (need_w, low, g.limit, g.notes[-4:])
    # a lighter place after a heavier one, both the game
    heavy, light = (lambda w: 40.0 * min(1.0, w / 14)), (lambda w: 80.0 * min(1.0, w / 24))
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=20), fps=heavy)
    if draw_known:
        g.draw = lambda w, t: float(w)
    g.run(150)
    held = g.limit
    assert 14 <= held <= 16
    g.fps = light                                                     # 47 to 53 fps here: far more than 40
    g.run(2.0 + 8)
    assert light(g.limit) >= 76, (held, g.limit, g.notes[-4:])




@pytest.mark.parametrize("target", [None, 120, 240])
@pytest.mark.parametrize("draw_known", [False, True])
def test_far_too_many_frames_to_judge_at_a_limit_that_holds_them_back_get_a_look_too(target, draw_known):
    """After a short video the level that held before it is on the chip, and a heavier, faster place gives over 2.5
    times the video's frames there: not judged for ADOPT_FAST_S, and 21 % under the need all that time (cloud fuzz seed
    117, 2026-11-16). One look at the full limit after ABOVE_S, as for far more frames that are judged."""
    for need_w in (22, 26):
        after = lambda w: 200.0 * min(1.0, w / need_w)
        g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=20), fps=lambda w: 60.0 * min(1.0, w / 12))
        if draw_known:
            g.draw = lambda w, t: float(w)
        g.run(150)
        held = g.limit
        assert 12 <= held <= 14
        g.fps = lambda w: 30.0
        g.run(40)
        assert g.limit == held or g.limit == 30, g.notes[-3:]
        g.fps = after                                               # 109 to 127 fps at 12 to 14 W
        g.run(2.0 + 8)
        assert after(g.limit) >= 190, (need_w, held, g.limit, g.notes[-4:])



@pytest.mark.parametrize("burst_s", [3.0, 6.0, 9.0])
def test_the_look_for_far_more_frames_waits_until_the_chip_is_down_at_the_limit(burst_s):
    """After a video the chip's averaging window lets the package draw over the limit for some seconds, and the game
    gives its full rate meanwhile. A look in that time found as many frames at the full limit and went back, and the
    game ran 12 % under at the limit for 23 s afterwards (cloud fuzz seed 233, 2026-11-16)."""
    game = lambda w: 120.0 * min(1.0, w / 12)
    g = Game(gov.Cfg(floor=8, ceiling=17, target=None, start_w=17), fps=lambda w, t: game(w))
    g.draw = lambda w, t: min(float(w), 12.0)
    g.run(60)
    g.fps, g.draw = (lambda w, t: 30.0), (lambda w, t: 6.0)
    g.run(120 + 240)
    low, t0 = g.limit, g.t
    assert low <= 9, low
    drawn = lambda w, t: min(12.0, w + 4.0) if t < t0 + burst_s else min(float(w), 12.0)
    g.fps, g.draw = (lambda w, t: game(drawn(w, t))), drawn
    g.run(burst_s + 2.0 + 8)
    assert game(g.limit) >= 114, (low, g.limit, [n for n in g.notes if n[0] >= t0])



@pytest.mark.parametrize("target", [None, 90])
@pytest.mark.parametrize("draw_known", [False, True])
def test_steps_down_stop_when_the_frames_jump_far_above_what_they_were_before_the_step(target, draw_known):
    """The game comes back from a long video in the middle of the way down: every further step "held" against the
    video's 30 fps, down to the floor, with the game 20 % under what the full limit gives for 10 to 17 s (cloud fuzz
    seeds 2, 122, 194, 2026-11-16). Far more frames than before the step are not the scene the step was for: no further
    step, and one look at the full limit."""
    for need_w in (22, 27):
        game = lambda w: 90.0 * min(1.0, w / need_w)
        g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30), fps=game)
        if draw_known:
            g.draw = lambda w, t: float(w)
        g.run(60)
        g.fps = lambda w: 30.0
        for _ in range(4000):                                        # ... until a step down is being watched
            if g.st.mode == "probe" and g.limit <= need_w - 4:
                break
            g.run(0.1)
        assert g.st.mode == "probe", (need_w, g.notes[-3:])
        g.fps, t0 = game, g.t
        g.run(gov.DWELL_S + 4)
        assert game(g.limit) >= 85 and min(g.since(t0)) >= need_w - 8, (need_w, g.since(t0), [n for n in g.notes if n[0] >= t0])



def _seconds_under(g, t0, t1, fps, line):
    """Seconds between t0 and t1 at a limit that gives less than `line` fps."""
    at, w, total = t0, [x for a, x in g.writes if a < t0][-1], 0.0
    for a, x in [(a, x) for a, x in g.writes if t0 <= a < t1] + [(t1, None)]:
        total += (a - at) if fps(w) < line else 0.0
        at, w = a, x if x is not None else w
    return total


@pytest.mark.parametrize("target", [None, 90])
@pytest.mark.parametrize("before_w, after_w", [(14, 14), (30, 20), (30, 26), (22, 16)])   # 30: held one watt under the full limit
def test_frames_that_stayed_after_a_video_are_the_game_before_any_step_down(target, before_w, after_w):
    """After a video the game is back at the level that held before it, with all its frames there. The look of the
    wait finds as many at the full limit and takes no reference; when the frames had stayed ADOPT_FAST_S, the video's
    30 fps was still what the full limit was believed to give, and the steps down were judged against it: 10 to 17 s
    at 20 % under (cloud fuzz seeds 2, 122, 193, 194, 2026-11-16). Also from one watt under the full limit."""
    before, after = (lambda w: 60.0 * min(1.0, w / before_w)), (lambda w: 60.0 * min(1.0, w / after_w))
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30), fps=before).run(150)
    held = g.limit
    assert before_w - 1 <= held <= before_w + 2, held
    g.fps = lambda w: 30.0
    g.run(40)
    assert g.limit in (held, 30), g.notes[-3:]
    g.fps, t0 = after, g.t
    g.run(120)
    lost = _seconds_under(g, t0, g.t, after, 57.0)
    assert lost <= 3.0 and g.st.ref == pytest.approx(60, abs=1.5), (held, lost, [n for n in g.notes if n[0] >= t0][:12])



@pytest.mark.parametrize("target", [None, 60])
@pytest.mark.parametrize("fps_before", [46.0, 30.0])
def test_far_more_frames_that_stayed_at_the_full_limit_are_the_reference_before_any_step_down(target, fps_before):
    """A lighter place after a heavy one, at the full limit: far more frames, so nothing moves for ADOPT_FAST_S. When
    they have stayed, they are what the full limit gives: the first step down is judged against them, not against the
    heavy place's frame rate (cloud fuzz seed 29, 2026-11-16: 46 fps known, 59 fps at the full 20 W, steps down to
    14 W against 44 fps, 7.6 s at 47 to 56 fps)."""
    heavy, light = (lambda w: fps_before * min(1.0, w / 20) ** 2), (lambda w: 59.0 * min(1.0, w / 19))
    g = Game(gov.Cfg(floor=8, ceiling=20, target=target, start_w=20), fps=heavy).run(100)
    assert g.limit == 20 and g.st.ref == pytest.approx(fps_before, abs=1.0)
    g.fps, t0 = light, g.t
    g.run(120)
    lost = _seconds_under(g, t0, g.t, light, 55.5)
    assert lost <= 1.5 and g.st.ref == pytest.approx(59, abs=1.5), (lost, g.st.ref, [n for n in g.notes if n[0] >= t0][:8])
    assert g.limit < 20, "the lighter place gets a lower limit"


def test_a_menu_with_far_too_many_frames_to_judge_gets_one_look_and_the_game_its_limit_back():
    """A menu at 3 times the game's rate at any limit (the draw not known): one look at the full limit, which gives as
    many: back, and nothing of the game forgotten."""
    for seconds in (4, 10, 20, 28):
        game = lambda w: min(40.0, 40.0 * w / 14)
        g = Game(gov.Cfg(floor=8, ceiling=30, target=None, start_w=20), fps=game).run(150)
        held, n, fail = g.limit, len(g.writes), dict(g.st.fail)
        g.fps = lambda w: 120.0
        g.run(seconds)
        assert [w for _, w in g.writes[n:]] in ([], [30, held]), (seconds, g.writes[n:])
        g.fps = game
        g.run(30)
        assert g.limit == held and g.st.ref == pytest.approx(40, abs=1) and set(fail) <= set(g.st.fail), (seconds, g.writes[n:])


def test_a_frame_rate_that_swings_across_far_more_gets_one_such_look_in_half_a_minute():
    """A rate that goes over and under "far more than the game gave" every few seconds starts the wait again each time;
    the look at the full limit that belongs to the wait does not come more often than every ADOPT_FAST_S."""
    for period in (7.0, 12.0, 20.0):
        g = Game(gov.Cfg(floor=8, ceiling=30, target=None, start_w=20), fps=lambda w: min(40.0, 40.0 * w / 14)).run(150)
        t0 = g.t
        g.fps = lambda w, t: min(40.0, 40.0 * w / 14) * (1.3 if (t - t0) % period < period - 1 else 1.0)
        g.run(180)
        looks = [at for at, note in g.notes if at >= t0 and note.startswith("more frames at")]
        assert all(b - a >= gov.SCENE_S for a, b in zip(looks, looks[1:])), (period, looks)


def test_a_menu_that_the_limit_holds_back_gets_the_full_limit_and_the_game_its_limit_back():
    """A map or a menu that is not frame-limited, at a limit that holds it back: the look finds more at the full limit,
    and the limit stays up while it lasts. The game back: its limit again, and what the full limit gives it."""
    for seconds in (10, 20, 28):
        game, menu = (lambda w: 40.0 * min(1.0, w / 14)), (lambda w: 150.0 * min(1.0, w / 25))
        g = Game(gov.Cfg(floor=8, ceiling=30, target=None, start_w=20), fps=game).run(150)
        held = g.limit
        g.fps = menu
        g.run(seconds)
        assert g.limit == 30, (seconds, g.notes[-3:])
        g.fps = game
        g.run(90)
        assert abs(g.limit - held) <= 1 and g.st.ref == pytest.approx(40, abs=1), (seconds, held, g.limit, g.notes[-5:])


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


def test_one_dip_at_the_full_limit_does_not_move_the_reference():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=None, start_w=30), fps=lambda w: 60.0)
    g.st.quiet_until = 1e9                                   # no steps down in this test
    g.run(10)
    assert g.st.ref == pytest.approx(60, abs=0.5)
    t0 = g.t
    g.fps = lambda w, t: 40.0 if t < t0 + 0.6 else 60.0      # one dip of the game's own
    seen = []
    for _ in range(8):
        g.run(0.5)
        seen.append(g.st.ref)
    assert min(seen) > 57, seen                              # the median of the last looks, not the last look


def test_steps_that_fail_one_after_the_other_wait_longer_each_time():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=lambda w: 60.0 if w >= 30 else 55.0).run(40)
    downs = [(at, w) for at, w in g.writes if w < 30]
    backs = [at for at, w in g.writes if w == 30]
    assert [w for _, w in downs] == [28, 29]                 # 2 W down, then halfway: both lose frames
    assert downs[1][0] - backs[0] >= gov.AFTER_LOSS_S - 1.1  # 5 s after the first, and
    assert g.st.quiet_until - backs[1] >= 2 * gov.AFTER_LOSS_S - 1.1     # 10 s after the second


def test_after_six_steps_down_in_a_row_the_rules_of_holding_get_a_turn():
    g = Game(gov.Cfg(floor=4, ceiling=40, target=60, start_w=40), fps=lambda w: 60.0).run(40)
    at = [t for t, w in g.writes]
    gaps = [round(b - a, 1) for a, b in zip(at, at[1:])]
    assert len(at) >= 8 and max(gaps[:5]) <= 2.7 and gaps[5] >= gov.AFTER_LOSS_S       # 2.5 s a step, then a pause



# ---- second review (2026-11-15): what broke, as tests ----------------------------------------------------------------
def test_a_target_out_of_reach_from_the_first_look_does_not_pin_the_start_level():
    """The full limit gives 46 fps, 12 W already do: the first look at the full limit read as "much less than the 60
    before" and set a floor at the start level that never went away (19 W for the whole session, and saved for the
    next). The target was never held, so that first look is no drop (review 3): the way down starts at once."""
    for full in (46.0, 31.0, 55.0):
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=lambda w: full if w >= 12 else full * w / 12)
        g.draw = lambda w, t: float(w)                               # it draws what it gets (an emulator that spins)
        g.run(60)
        assert g.limit <= 13 and g.st.mode == "hold" and g.st.drop_floor is None, (full, g.limit, g.writes)
        g.run(400)
        assert 12 <= g.limit <= 13 and g.st.mode == "hold", (full, g.limit)


def _video_game(video_from, video_to):
    """A 60 fps game on a frame limit that needs 16 W and gives 30 fps at 8 W; a 30 fps video (6 W at any limit)."""
    video = lambda t: video_from <= t < video_to
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60),
             fps=lambda w, t: 30.0 if video(t) else min(60.0, 30.0 + 30.0 * (w - 8) / 8.0))
    g.draw = lambda w, t: 6.0 if video(t) else min(float(w), 16.0)
    return g



@pytest.mark.parametrize("target", [None, 60])
def test_after_a_drop_of_what_the_full_limit_gives_no_step_down_for_confirm_s(target):
    """At the full limit, a video: what the full limit gives is 15 % less from one look to the next. A cutscene of a few
    seconds looks the same, so no step down is tried for CONFIRM_S; after it the way down starts."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30), fps=lambda w: 60.0 * (w / 30) ** 3)   # needs it all
    g.draw = lambda w, t: float(w)
    g.run(40)
    assert g.limit == 30
    g.fps, g.draw = (lambda w: 30.0), (lambda w, t: 6.0)
    t0 = g.t
    g.run(18)                                                         # (the design's 20 s, not the constant: it is the rule)
    assert g.since(t0) == [], g.since(t0)
    g.run(40)
    assert g.limit < 30, g.notes[-3:]


def test_a_short_video_keeps_the_level_that_held_before_it():
    g = _video_game(150, 190)
    g.run(150)
    held = g.limit
    assert 16 <= held <= 18
    g.run(40)                                                        # the video: half the frames at every limit
    assert all(w >= held for t, w in g.writes if t >= 150)           # nothing under it for REACH_FOR_S
    g.run(60)
    assert g.limit <= held + 2 and g.st.ref > 57                     # the game is back, at its frame rate
    assert g.st.drop_floor is None and g.st.ref_high is None         # ... and the floor is gone with the video



@pytest.mark.parametrize("target", [None, 60])
def test_a_heavier_place_after_a_video_gets_the_look_when_the_package_has_drawn_the_whole_limit_for_bound_s(target):
    """After a video the game is in a heavier place: at the limit that held before, more frames than the video's and
    fewer than the game's. The package draws all of the limit, which it did not in the video: that is the sign for the
    look, BOUND_S after it began, as without the far more frames (cloud fuzz seeds 82, 150, 2026-11-16: the wait
    for far more frames put the look at 5 s, with 51 of 60 fps until then)."""
    need = [11.0]
    game = lambda w: 60.0 * min(1.0, w / need[0])
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30), fps=game)
    g.draw = lambda limit, t: min(limit, need[0])
    g.run(150)
    held = g.limit
    assert 11 <= held <= 13, held
    g.fps, g.draw = (lambda w: 30.0), (lambda limit, t: min(limit, 6.0))
    g.run(60)
    assert g.limit == held, g.notes[-3:]
    need[0], g.fps, g.draw, t0 = held + 3.0, game, (lambda limit, t: min(limit, need[0])), g.t
    g.run(8)
    looks = [t for t, w in g.writes if t >= t0 and w == 30]
    assert looks and looks[0] - t0 <= 3.6, (looks, [n for n in g.notes if n[0] >= t0][:4])




def _lighter_place(thermal=False):
    """A game that needs 14 W for its 40 fps, the package at the limit all the time,
    held at 14 W with 13 W tried and failed; then a place that gives twice the frames with 24 W. -> the game, the time
    of the change."""
    g = Game(gov.Cfg(floor=8, ceiling=30, start_w=30), fps=lambda w: 40.0 * min(1.0, w / 14))
    g.draw = lambda limit, t: float(limit)
    while not (g.st.mode == "hold" and g.limit == 14 and 13 in g.st.fail and g.t - g.st.changed_at >= 3) and g.t < 60:
        g.run(0.5)
    assert g.limit == 14 and g.t - g.st.ref_at < 60 - 30, (g.t, g.limit, g.st.ref_at)    # no look for 30 s but this one
    g.fps, g.thermal = (lambda w: 80.0 * min(1.0, w / 24)), thermal
    return g, g.t


def test_far_more_frames_get_no_look_under_the_guards_clock_cap():
    """Under the guard's clock cap the full limit does not give what it gives without: no look for far more frames
    while the cap stands, and the look when it is gone."""
    g, t0 = _lighter_place(thermal=True)
    g.run(20)
    assert g.since(t0) == [], [n for n in g.notes if n[0] >= t0][:4]
    g.thermal = False
    g.run(8)
    assert 30 in g.since(t0 + 20), [n for n in g.notes if n[0] >= t0][:4]



@pytest.mark.parametrize("draw_known", [False, True])
def test_after_a_drop_of_the_reference_the_full_limit_is_looked_at_every_reach_s(draw_known):
    """A heavy stretch (24 fps at the full limit, of a target of 30) and the limit that holds it; then a place that
    reaches the target with a few watts more and gives 2 % more at the held limit: no loss, and less than any other look asks for.
    For REACH_FOR_S after the drop the full limit is looked at every REACH_S, and that look finds it (the simulator's
    emulator-need-jumps at a 35 W pick: 19 s at 28 of 30 fps until that look, 2026-11-16)."""
    light, heavy = (lambda w: 30.0 * min(1.0, w / 14)), (lambda w: 24.0 * min(1.0, w / 24))
    g = Game(gov.Cfg(floor=8, ceiling=30, target=30, start_w=30), fps=light)
    if draw_known:
        g.draw = lambda limit, t: float(limit)
    g.run(60)
    assert g.limit <= 16
    g.fps = heavy
    g.run(60)
    held = g.limit
    assert 22 <= held <= 28 and g.st.ref == pytest.approx(24, abs=0.5), (held, g.st.ref, g.notes[-4:])
    back = lambda w: 30.0 * min(1.0, w / (held * 30.0 / (heavy(held) * 1.02)))    # 2 % more at the held limit
    g.fps, t0 = back, g.t
    g.run(30 + 5)
    assert 30 in g.since(t0) and g.st.ref == pytest.approx(back(30), abs=0.5) and back(30) > 24 * 1.05, (g.limit, g.st.ref, [n for n in g.notes if n[0] >= t0][:5])



@pytest.mark.parametrize("target", [None, 60])
def test_a_menus_frames_at_the_full_limit_are_never_the_reference(target):
    """A game that gets 30 fps from the full limit; a menu over it at 60 fps for 20 s. Neither in the menu nor in the
    seconds after it is the menu's frame rate what the full limit is believed to give (it would make the game's own
    30 fps a drop: a floor, and no step down for CONFIRM_S)."""
    game = lambda w: 30.0 * min(1.0, w / 30) ** 4
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30), fps=game).run(60)
    assert g.limit == 30 and g.st.ref == pytest.approx(30, abs=0.5)
    refs = []
    for fps, seconds in ((lambda w: 60.0, 20), (game, 10)):
        g.fps = fps
        for _ in range(seconds * 2):
            g.run(0.5)
            refs.append(g.st.ref)
    assert max(refs) < 33 and g.st.drop_floor is None, (max(refs), g.st.drop_floor)


def test_a_look_that_finds_as_many_frames_in_a_menu_leaves_the_reference_alone():
    """A menu at 60 fps over a 30 fps game, the package at the limit: one look at the full limit. As many frames there:
    back, and the game's reference stays (the menu's 60 fps as the reference made the game's 30 fps a loss)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, start_w=30), fps=lambda w: 30.0 * min(1.0, w / 20))
    g.draw = lambda limit, t: float(limit)
    g.run(90)
    held, t0 = g.limit, g.t
    assert 19 <= held <= 22 and g.st.ref == pytest.approx(30, abs=0.5)
    g.fps = lambda w: 60.0
    g.run(20)
    assert g.since(t0) == [30, held], (g.since(t0), [n for n in g.notes if n[0] >= t0][:4])
    assert g.st.ref == pytest.approx(30, abs=0.5)


def test_one_watt_under_the_full_limit_gets_no_look_while_the_frames_say_nothing():
    """What one watt more gives is not worth a look: at one watt under the full limit there is none, also not the one
    of every CHECK_MAX_S."""
    g = Game(gov.Cfg(floor=8, ceiling=30, start_w=30), fps=lambda w: 60.0 * min(1.0, w / 30)).run(120)
    t0 = g.t
    assert g.limit == 29, g.notes[-3:]
    g.run(gov.CHECK_MAX_S + 120)
    assert 30 not in g.since(t0), [n for n in g.notes if n[0] >= t0][:4]


def test_after_a_long_video_the_full_limit_is_looked_at_when_the_package_draws_the_whole_limit():
    """After REACH_FOR_S the lower reference is the game and the limit goes down. When the game is back, the low limit
    may give just what the video gave (30 fps at 8 W): no loss shows. But the package now draws all of the limit, which
    it did not when the reference was taken: the full limit is looked at within seconds (it stayed at half the frame
    rate for the rest of the session otherwise)."""
    g = _video_game(150, 400)
    g.run(400)
    assert g.limit <= 10                                             # down during the long video
    g.run(6)
    assert any("draws all of" in n for t, n in g.notes if t >= 400)
    assert 30 in g.since(400)
    g.run(60)
    assert g.limit >= 16 and g.st.ref > 57


def test_a_loading_screen_does_not_lower_the_reference():
    """No frames and a package far under the limit for some seconds. The pace is still that of the frames before
    (rendered with the limit holding the game back) while the look's draw says the limit holds nothing back: the looks
    as the loading screen began and ended took that pace as the reference (55.8 -> 53.8 -> 51.6 fps over three
    loading screens, and the limit followed it down). Here as the plugin hands frames on: stamped at the look's time,
    so three looks without frames are not yet "no frames"."""
    cfg = gov.Cfg(floor=8, ceiling=30)
    g = Game(cfg, fps=lambda w: 56.0 if w >= 24 else 56.0 - (24 - w))        # a frame less for each watt under 24
    g.draw = lambda w, t: float(w)
    g.run(90)
    st, ref = g.st, g.st.ref
    assert 21 <= g.limit <= 23 and abs(ref - 56.0) < 0.5 and st.mode == "hold"
    ms, t = 1000.0 / g.rate(), g.t

    def look(frames, draw):
        nonlocal t
        t += 0.5
        gov.step(st, gov.Obs(t=t, frames=[(t - (frames - 1 - i) * ms / 1000.0, ms) for i in range(frames)], draw_w=draw), cfg)
    for _ in range(3):
        look(13, g.limit - 5.0)                     # the loading screen begins in this look: half its frames, half its draw
        for _ in range(9):
            look(0, 12.0)                           # the loading screen
        look(13, g.limit - 5.0)                     # play again, half a look of it
        for _ in range(20):
            look(27, float(g.limit))
    assert st.ref == pytest.approx(ref, rel=0.005)


def test_a_visit_to_a_fast_map_changes_nothing_and_a_long_stay_is_the_game():
    """A map at 144 fps in a 40 fps game: over 2.5 times the usual rate is not judged. After 5 s it was taken for the
    game (all that was known dropped, the full limit for 20 s on the way back); its frames in the pace read as a game
    that gives more than the reference. More frames harm nothing: 30 s before it is the game, and the pace starts
    again from the game's frames when it is over."""
    in_map = lambda t: 200 <= t < 208 or t >= 400
    g = Game(gov.Cfg(floor=8, ceiling=30), fps=lambda w, t: 144.0 if in_map(t) else (40.0 if w >= 14 else 40.0 * w / 14))
    g.draw = lambda w, t: 9.0 if in_map(t) else min(float(w), 14.0)
    g.run(200)
    held, ref, fail = g.limit, g.st.ref, dict(g.st.fail)
    assert held <= 15
    g.run(30)                                                        # 8 s in the map and back
    assert g.since(200) == [] and g.st.ref == pytest.approx(ref, rel=0.02) and set(fail) <= set(g.st.fail)
    g.run(170 + gov.SCENE_S + 10)                               # ... and a stay of over half a minute
    assert g.st.usual > 100


def test_a_faster_menu_does_not_read_as_slow_frames_at_the_limit():
    """A 60 fps menu for 3 s in a 40 fps game (1.5 times: judged). With its frames at the end of the last 10 s the mean
    frame time falls and every frame of the game is "slow" against it, while the medians of both halves stand still:
    the edge rule gave a watt at every visit."""
    menu = lambda t: any(a <= t < a + 3 for a in (120, 180, 240))
    g = Game(gov.Cfg(floor=8, ceiling=30, target=40), fps=lambda w, t: 60.0 if menu(t) else (40.0 if w >= 13 else 40.0 * w / 13))
    g.draw = lambda w, t: 9.0 if menu(t) else min(float(w), 13.0)
    g.run(300)
    assert not any("slow frames" in n for _, n in g.notes)


def test_a_slow_game_gets_the_frames_a_look_at_the_full_limit_needs():
    """A look at the full limit was judged after a second: 20 frames of a 20 fps game, some from before the watts had
    arrived, and the reference came out too low (22.5 for 24.5). It waits for 3 N frames, at most 3 s."""
    g = Game(gov.Cfg(floor=8, ceiling=30), fps=lambda w: 20.0 if w >= 20 else 20.0 * w / 20)
    g.draw = lambda w, t: float(w)
    g.run(60)
    looks = [(t, n) for t, n in g.notes if "the full 30 W" in n or "measuring what" in n]
    assert looks
    t0 = looks[0][0]
    after = min(t for t, n in g.notes if t > t0)
    assert after - t0 >= 3 * gov.N / 20.0                            # 30 frames at 20 fps


def test_frame_times_that_are_not_frame_times_are_left_out():
    st, _ = gov.start(gov.Cfg(floor=8, ceiling=30, target=60), 0.0)
    for k in range(40):
        frames = [(k * 0.5 + 0.1, 0.0), (k * 0.5 + 0.2, -3.0), (k * 0.5 + 0.3, float("nan"))] + \
                 [(k * 0.5 + 0.3 + i / 60.0, 16.7) for i in range(10)]
        gov.step(st, gov.Obs(t=(k + 1) * 0.5, frames=frames), gov.Cfg(floor=8, ceiling=30, target=60))
    assert all(ms > 0 for ms in st.pace) and len(st.pace) == gov.PACE_FRAMES


def test_the_guards_cap_under_the_floor_wins():
    g = Game(gov.Cfg(floor=15, ceiling=30, target=60))
    g.run(5)
    g.cap = 12
    g.run(5)
    assert g.limit == 12                                             # under your floor: the guard's step is for safety
    g.cap = None
    g.run(30)
    assert g.limit >= 15


# ---- rules that no test noticed when they were broken (second review's mutation run) --------------------------------
def _needs(need):
    """60 fps from `need()` watts up, in proportion below."""
    return lambda w, t: 60.0 if w >= need() else 60.0 * w / need()


def _cliff(need, low=48.0):
    """60 fps from `need()` watts up, `low` under it."""
    return lambda w, t: 60.0 if w >= need() else low


def test_no_write_goes_over_the_guards_cap_when_a_step_is_undone():
    """A step down is being watched when the guard caps the limit just over it: the limit to go back to is over the
    cap now."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=_cliff(lambda: 20))
    for _ in range(600):
        g.run(0.1)
        if g.st.mode == "probe" and g.st.prev == 20:
            break
    assert g.st.mode == "probe" and g.limit < 20
    g.cap = 19
    t0 = g.t
    g.run(10)
    assert g.since(t0) and max(g.since(t0)) <= g.cap


def test_a_clear_loss_in_a_step_is_undone_within_a_few_frames_not_at_the_end_of_the_watch():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=_cliff(lambda: 17))
    g.run(60)
    down = [(t, w) for t, w in g.writes if w < 17]
    assert down
    t, w = down[-1]
    back = min(at for at, x in g.writes if at > t and x > w)
    assert back - t <= 0.4                                           # SETTLE_S and five frames; the watch is 2.5 s


def test_frames_that_stay_lost_after_a_step_was_undone_get_the_full_limit():
    """The scene got heavier while the step was watched: back at the limit before, frames are still lost."""
    need = {"w": 20}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=_cliff(lambda: need["w"], low=52.0))
    for _ in range(600):
        g.run(0.1)
        if g.st.mode == "probe" and g.st.prev == 20:
            break
    assert g.st.mode == "probe"
    need["w"], t0 = 26, g.t
    g.run(3)
    assert g.since(t0 + 0.05)[:2] == [20, 30]                        # undone, and all of it ...
    undone, full = [t for t, w in g.writes if t >= t0 + 0.05][:2]
    assert full - undone <= gov.BACK_S / 2 + 0.25                    # ... on the first ten frames after half a second


def test_a_small_loss_that_stays_goes_back_to_the_level_that_held_before():
    need = {"w": 14}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=lambda w, t: 60.0 if w >= need["w"] else 60.0 - 0.8 * (need["w"] - w))
    g.run(90)
    held, safe = g.limit, g.st.safe
    assert g.st.mode == "hold" and safe is not None and safe > held + 1
    need["w"], t0 = held + 5, g.t                                    # 4 fps less here: under the goal, not by much
    g.run(8)
    assert g.since(t0)[0] == safe


def test_the_full_limit_is_looked_at_again_soon_after_steps_down_from_it():
    """The reference is from before the steps: a scene that got heavier during them would be held against it."""
    g = Game(gov.Cfg(floor=8, ceiling=30), fps=lambda w: 56.0 if w >= 24 else 56.0 - (24 - w))
    g.draw = lambda w, t: float(w)
    g.run(60)
    looks = [t for t, n in g.notes if "checking what the full" in n]
    ends = [t for t, n in g.notes if n.startswith("holding at") or "back to" in n]
    assert looks and ends and min(t for t in looks if t > ends[0]) - ends[0] <= 4.0


def test_more_frames_at_a_held_limit_than_when_the_reference_was_taken_has_the_full_limit_looked_at():
    """A lighter scene: this limit gives 6 % more than it did. The full limit gives more too, and the levels that
    failed in the heavier scene say nothing here."""
    lift = {"x": 1.0}
    g = Game(gov.Cfg(floor=8, ceiling=30), fps=lambda w, t: lift["x"] * (60.0 if w >= 26 else 60.0 - (26 - w)))
    g.draw = lambda w, t: float(w)
    g.run(40)
    assert g.st.mode == "hold" and g.limit < 26 and g.st.fail
    g.run(max(0.0, g.st.ref_at + 20 - g.t))
    lift["x"], t0, ref, failed = 1.07, g.t, g.st.ref, dict(g.st.fail)
    g.run(15)                                                        # well inside CHECK_S of the last look
    assert any("checking what the full" in n for t, n in g.notes if t >= t0)
    assert g.st.ref > ref * 1.05 and all(g.st.fail.get(w) != v for w, v in failed.items())


def test_a_held_limit_that_gives_more_than_the_reference_has_the_full_limit_looked_at():
    """The reference is from a moment that was not the game (a video at half the frame rate): this limit shows more
    frames than the full limit is believed to give."""
    g = Game(gov.Cfg(floor=8, ceiling=30), fps=_needs(lambda: 16))
    g.draw = lambda w, t: float(w)
    g.run(60)
    assert g.st.mode == "hold"
    g.st.ref, g.st.ref_at, g.st.ref_here, g.st.quiet_until, t0 = 40.0, g.t, None, g.t + 60, g.t
    g.run(2.0 + 4)
    assert any("checking what the full" in n for t, n in g.notes if t >= t0) and g.st.ref > 57


def test_a_frame_rate_that_moved_a_little_has_the_full_limit_looked_at_after_a_minute():
    """4 % more at the held limit than when the reference was taken: not enough for a look at once (5 %), enough for
    one CHECK_S after the last (the other look is every 10 minutes)."""
    lift = {"x": 1.0}
    g = Game(gov.Cfg(floor=8, ceiling=30), fps=lambda w, t: lift["x"] * (60.0 if w >= 26 else 60.0 - (26 - w)))
    g.draw = lambda w, t: float(w)
    g.run(40)
    g.run(max(0.0, g.st.ref_at + 20 - g.t))
    lift["x"], t0, last = 1.04, g.t, g.st.ref_at
    g.run(30)
    assert not any("checking what the full" in n for t, n in g.notes if t >= t0)          # not yet
    g.run(25)
    looks = [t for t, n in g.notes if t >= t0 and "checking what the full" in n]
    assert looks and 60 <= looks[0] - last <= 60 + 15


def test_no_look_at_the_full_limit_while_the_package_is_well_under_the_limit():
    """A limit the package doesn't use holds nothing back: what it shows is the reference, and a look at the full
    limit would only cost watts."""
    g = Game(gov.Cfg(floor=8, ceiling=30))
    g.draw = 5.0
    g.run(60)
    assert g.limit == 8
    t0 = g.t
    g.run(gov.CHECK_MAX_S + 60)
    assert g.since(t0) == []


def test_looks_that_are_free_do_not_add_up_across_looks_that_are_not():
    """The reference is the median of FREE_LOOKS looks in a row. Four of them in a lighter moment, then looks in which
    the limit holds the game back, then one more: not five."""
    cfg = gov.Cfg(floor=8, ceiling=30)
    g = Game(cfg, fps=lambda w: 60.0 if w >= 26 else 60.0 - (26 - w))
    g.draw = lambda w, t: float(w)
    g.run(40)
    st, ref, t = g.st, g.st.ref, g.t
    st.quiet_until, st.ref_at = t + 120, t                           # no step and no look of its own meanwhile

    def look(fps, draw, n=1):
        nonlocal t
        for _ in range(n):
            t += 0.5
            k = round(fps * 0.5)
            gov.step(st, gov.Obs(t=t, frames=[(t - (k - 1 - i) / fps, 1000.0 / fps) for i in range(k)], draw_w=draw), cfg)
    look(59.0, 10.0, n=5)                                            # a lighter moment: 60 frames of it, then four looks
    assert len(st.free_fps) == 4 and st.ref == ref
    look(g.rate(), float(g.limit), n=3)                              # the limit holds the game back again
    look(59.0, 10.0, n=3)                                            # ... and two more free looks
    assert len(st.free_fps) == 2 and st.ref == ref and st.limit == g.limit


def test_a_stretch_that_stays_is_the_game_and_what_was_known_before_is_dropped():
    need = {"fps": 60.0}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=lambda w, t: need["fps"] if w >= 17 else need["fps"] * 0.8)
    g.run(60)
    assert g.st.fail and g.st.usual == pytest.approx(60.0, abs=1)
    need["fps"] = 18.0                                               # a part of the game at 18 fps, at every limit
    g.run(gov.ADOPT_S + 8)
    assert g.st.usual == pytest.approx(18.0, abs=1) and g.st.home is None
    assert not any(w for w, (retry, n) in g.st.fail.items() if retry < g.t + gov.RETRY_S - 30)   # none from before


def test_a_game_whose_frame_rate_wobbles_is_not_taken_for_one_that_loses_frames():
    """Runs of ten frames at 56 and at 45 fps in turn, at every limit: on average the 50 fps target, and every other
    look shows ten frames 5 % under the goal. The thresholds follow the wobble (at most fourfold)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=50), fps=lambda w: 56.0, shape=lambda i, w, t: 1.0 if (i // 10) % 2 else 56.0 / 45.0)
    g.run(180)
    late = [w for t, w in g.writes if t >= 60]
    assert g.st.noise > 0.05 and not any(w >= 25 for w in late)      # no "all the watts at once" once the wobble is known


def test_a_step_down_is_watched_from_when_it_binds():
    """PL1 is an average: a cut made while the package ran over the limit binds late (2.3 s measured). The frames of
    the first seconds are not the step's: judged on them, 18 W "held" and the next step followed, with the loss still
    to come."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60))

    def in_force(t):                                                 # the watts the chip really gives: a cut 2.6 s late
        if len(g.writes) >= 2 and g.writes[-1][1] < g.writes[-2][1] and t - g.writes[-1][0] < 2.6:
            return g.writes[-2][1]
        return g.limit
    g.fps = lambda w, t: 60.0 if in_force(t) >= 26 else 50.0
    g.draw = lambda w, t: float(in_force(t))
    g.run(120)
    assert g.limit == 26 and g.st.mode == "hold"
    low = [(t1, a, t2, b) for (t1, a), (t2, b) in zip(g.writes, g.writes[1:]) if a < 26]
    assert [a for _, a, _, _ in low] == [25]                         # 30 -> 25 W: halfway to the 19 W that failed
    for t1, a, t2, b in low:
        assert b >= 26 and t2 - t1 <= gov.BIND_S + 1.0               # the level under the need was never built upon


def test_a_second_try_that_holds_counts_as_one_of_the_games_own_dips():
    """Two dips two minutes apart, each answered with the full limit and the level once more, which holds: the game
    dips by itself. The third dip, 15 s later, is waited out without a write."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=HOLDS_FROM_14).run(60)
    held, t0 = g.limit, g.t
    dips = [t0, t0 + 130, t0 + 145]
    g.fps = lambda w, t: HOLDS_FROM_14(w) * (0.75 if any(a <= t < a + 0.8 for a in dips) else 1.0)
    g.run(140)
    assert g.since(t0 + 125) == [30, held] and len(g.st.dips) == 2
    t2 = g.t
    g.run(20)
    assert g.since(t2) == [] and g.limit == held


def _loss_answered_after(dips_ago):
    """A level that holds, the game's own dips this long ago, then a need that rises for good: seconds until the full
    limit is written."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=HOLDS_FROM_14).run(40)
    t0 = g.t
    g.st.dips.extend(t0 - ago for ago in dips_ago)
    g.fps = lambda w: 60.0 if w >= 20 else 45.0
    g.run(4)
    assert g.since(t0)[0] == 30
    return [t for t, w in g.writes if t >= t0][0] - t0


def test_a_game_that_dips_gets_a_second_of_patience_and_the_dips_are_forgotten_after_five_minutes():
    assert _loss_answered_after(()) <= 0.6                           # ten frames and the next look
    assert 1.0 <= _loss_answered_after((20, 10)) <= 1.6              # PATIENT_S
    assert _loss_answered_after((310, 301)) <= 0.6                   # DIPS_S
    assert _loss_answered_after((310, 10)) <= 0.6                    # one dip in five minutes is not "dips by itself"


def test_a_long_loss_at_the_full_limit_is_a_heavier_scene_not_a_dip():
    """A dip is over within DIP_MAX_S. Ten seconds of fewer frames at the full limit are a place in the game: counted
    as dips, two of them made the governor wait a second before every answer from then on."""
    slow = [(20.0, 30.0), (50.0, 60.0)]
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30, auto_fps=False),
             fps=lambda w, t: (60.0 if w >= 30 else 50.0) * (0.8 if any(a <= t < b for a, b in slow) else 1.0))
    g.run(70)
    assert len(g.st.dips) == 0
    slow += [(80.0, 80.6), (95.0, 95.6)]                             # two short ones are
    g.run(40)
    assert len(g.st.dips) == 2


def test_the_thresholds_follow_the_games_wobble_up_to_four_times_and_no_further():
    """A game whose rate of ten frames wobbles by 20 %: 2.5 times that would be "no loss under 50 %". A loss of 16 %
    of the goal is still a clear one (the full limit at once), one of 5 % that stays is still answered (the level
    before)."""
    def after(fps):
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=HOLDS_FROM_14).run(40)
        t0, held = g.t, g.limit
        g.st.noises.extend([0.2] * g.st.noises.maxlen)               # as learned from a wild game
        g.st.edge_at = t0                                            # (the slow-frames rule has just had its turn)
        g.fps = lambda w: 60.0 if w >= 20 else fps
        g.run(3)
        return held, [(round(t - t0, 2), w) for t, w in g.writes if t >= t0]
    held, writes = after(48.0)                                       # 16 % under the 57 fps goal
    assert writes[0][1] == 30 and writes[0][0] <= 0.6, writes
    held, writes = after(54.0)                                       # 5 % under it
    assert writes and held < writes[0][1] < 30 and writes[0][0] <= 2.5, writes


def _raise_then(kind):
    """A level that holds, then the need rises. After the raise to the full limit the frames come back as `kind`
    says. The governor's first verdict on the raise, and the state after it."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=HOLDS_FROM_14).run(30)
    held, t0 = g.limit, g.t

    def since_raise(t):
        up = [at for at, w in g.writes if at >= t0 and w == 30]
        return t - up[-1] if up and g.limit == 30 else None

    def fps(w, t):
        s = since_raise(t)
        if s is None:
            return 45.0
        return {"late": 60.0 if s >= 1.5 else 45.0,                  # the frames are back when all the watts are there
                "climb": min(60.0, 45.0 + 7.5 * s),                  # they climb for 2 s
                "tail": 60.0 if s >= 0.5 else 58.4}[kind]            # back at once, the first half second 2.7 % short
    g.fps = fps
    if kind == "late":                                               # the package gets the watts over 1.5 s
        g.draw = lambda w, t: float(w) if since_raise(t) is None else min(30.0, held + 10.0 * since_raise(t))
    for _ in range(100):
        g.run(0.1)
        if g.st.mode == "hold" and g.t > t0 + 1:
            break
    return g, held, [n for t, n in g.notes if t >= t0][-1]


def test_a_raise_is_judged_when_the_watts_have_arrived():
    """PL1 is an average: the package took 1.5 s to draw the full limit, and the frames came back with it. Judged a
    second after the write, the full limit "gave 45 fps": the scene, not the limit, and back down."""
    g, held, verdict = _raise_then("late")
    assert verdict == f"{held} W was too low (45 fps, 60 at 30 W)" and g.limit == 30
    assert g.st.ref == pytest.approx(60.0, abs=0.2) and held in g.st.fail


def test_a_raise_is_judged_when_the_frame_rate_has_stopped_climbing():
    """No draw reading, and the frames climb for 2 s after the raise: a verdict after one second took 51 fps for what
    the full limit gives."""
    g, held, verdict = _raise_then("climb")
    assert verdict == f"{held} W was too low (45 fps, 60 at 30 W)"
    assert g.st.ref == pytest.approx(60.0, abs=0.2) and not gov.out_of_reach(g.st, g.cfg)


def test_what_the_full_limit_gives_is_read_from_the_later_half_of_a_long_watch():
    """The first frames after a raise are still on their way up. Over the whole watch the full limit gave 59.3 fps: a
    60 fps target "out of reach"."""
    g, held, verdict = _raise_then("tail")
    assert g.st.ref == pytest.approx(60.0, abs=0.2) and not gov.out_of_reach(g.st, g.cfg)


def test_frames_that_collapse_before_a_step_binds_get_the_full_limit_at_once():
    """The package runs a watt over its limit, so a cut binds 2.6 s late, and the governor waits for it. A heavier
    scene that begins in that wait is not the step's doing, but it needs the watts now, not when the wait is over."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=22))
    heavy = {"at": None}

    def in_force(t):
        if len(g.writes) >= 2 and g.writes[-1][1] < g.writes[-2][1] and t - g.writes[-1][0] < 2.6:
            return g.writes[-2][1]
        return g.limit
    g.fps = lambda w, t: (60.0 if w >= 30 else 40.0) if heavy["at"] is not None and t >= heavy["at"] else HOLDS_FROM_14(in_force(t))
    g.draw = lambda w, t: in_force(t) + 1.0
    for _ in range(600):
        g.run(0.1)
        if g.st.mode == "probe" and g.st.prev < 22 and g.t - g.st.wrote_at < 0.3:
            break
    assert g.st.mode == "probe" and g.st.prev < 30
    heavy["at"] = g.t + 0.3
    g.run(3)
    assert g.since(heavy["at"])[0] == 30
    assert [t for t, w in g.writes if t >= heavy["at"]][0] - heavy["at"] <= 0.6      # ten frames and a look, not BIND_S


def test_the_early_look_at_the_full_limit_also_follows_steps_that_end_by_holding_and_more_than_six_of_them():
    """Steps down from the full limit that end at the floor without a failure (three of them, and eleven with a pause
    after the sixth): the reference is from before them all, and is looked at a second after the last."""
    for ceiling in (14, 30):
        g = Game(gov.Cfg(floor=8, ceiling=ceiling, start_w=ceiling), fps=lambda w: 56.0)
        g.draw = lambda w, t: float(w)
        g.run(60)
        ends = [t for t, n in g.notes if n == "holding at 8 W"]
        looks = [t for t, n in g.notes if "checking what the full" in n]
        assert ends and looks and 0 < min(t for t in looks if t > ends[0]) - ends[0] <= 2.0, (ceiling, g.notes)
        assert len(looks) == 1                                       # ... once


def test_a_long_pause_does_not_make_what_is_known_old():
    """The reference is looked at again when it is ten minutes old. Twelve minutes in a pause menu without frames are
    not twelve minutes of the game: back in it, the limit that held is kept and the full limit is not looked at."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=None, start_w=16), fps=HOLDS_FROM_14).run(90)
    assert g.limit == 14
    g.run(700, frames=False)
    back = g.t
    g.run(8)
    assert 30 not in g.since(back) and g.limit >= 13


def test_a_look_at_the_full_limit_with_too_few_frames_to_judge_on_ends_there():
    """A frame log that loses most of its rows: a frame every 1.3 s, each as long as the game's. No judgement of what
    the full limit gives is possible; after 3 s the look ends at the full limit (and does not go on ten times a second
    for as long as the log stays thin)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 60.0).run(3)
    g.fps = lambda w: 60.0 if w >= 30 else 45.0
    while not g.writes:
        g.run(0.1)
    assert g.limit == 30 and g.look == gov.WATCH_S
    g.thin = 80
    g.run(4)
    assert g.limit == 30 and g.st.mode == "hold" and g.look == gov.LOOK_S


def test_a_scene_that_draws_the_whole_limit_is_looked_at_again_also_after_a_look_at_the_full_limit():
    """The reference came from a look at the full limit that began while the package drew 1 W less than its limit. The
    next scene gives the same 50 fps at 16 W, draws all 16 W and would give 70 fps at 30 W: no loss shows, the draw
    does. The full limit is looked at within seconds, not at the next check a minute or ten later."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=None, start_w=16), fps=lambda w: 50.0 if w >= 16 else 50.0 * w / 16)
    g.draw = lambda w, t: min(float(w), 16.0) - 1.0
    g.run(60)
    assert g.limit == 16
    g.cap = 29                                           # the guard's cap comes and goes: the reference is looked at
    g.run(0.5)
    g.cap = None
    n = len(g.writes)
    while len(g.writes) < n + 2:
        g.run(0.1)
    assert [w for _, w in g.writes[n:]] == [30, 16]
    g.fps = lambda w: 50.0 + 20.0 * (w - 16) / 14 if w >= 16 else 50.0 * w / 16
    g.draw = lambda w, t: float(w)
    at = g.t
    g.run(5)
    assert 30 in g.since(at) and any("draws all of" in n for t, n in g.notes if t >= at)
    g.run(20)
    assert g.limit >= 28 and g.st.ref > 66


def test_back_from_a_menu_the_game_is_judged_on_frames_of_the_game_alone():
    """A 20 fps game (target 60, out of reach) held at 14 W; 4 s of a 60 fps menu. The first look after the menu has
    frames of both: with them in the pace the game "gave more than the full limit does", and the full limit was looked
    at. For every moment in a look at which the menu can end: no write."""
    for frame in range(18, 30):
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 20.0 if w >= 14 else 20.0 * w / 14)
        menu = 60.0 + frame / 60.0
        g.shape = lambda i, w, at: 1 / 3 if menu <= at < menu + 4.0 else 1.0
        g.run(60)
        held = g.limit
        g.run(30)
        assert g.since(60) == [] and g.limit == held, frame


def test_steam_menu_over_a_30fps_game_moves_nothing():
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


def _into_retest():
    """A level held for a while, one dip of the game's own: full limit, then the level once more (a retest probe)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=HOLDS_FROM_14).run(60)
    held, t0 = g.limit, g.t
    g.fps = lambda w, t: HOLDS_FROM_14(w) * (0.75 if t0 <= t < t0 + 0.8 else 1.0)
    while not (g.st.mode == "probe" and g.st.retest):
        g.run(0.1)
        assert g.t < t0 + 10
    return g, held


def test_a_guard_cap_during_a_second_try_leaves_no_second_try_behind():
    """The guard's cap arrives while a level is tried once more (retest). governor.step goes to hold, but `retest`
    stays True, so the next ordinary step down that holds is booked as "the game's own dip" and ends the run of steps."""
    g, held = _into_retest()
    g.cap = held - 1                                    # the guard: under the level being tried
    g.run(0.2)
    assert g.st.mode == "hold"
    print("after the cap: mode", g.st.mode, "retest", g.st.retest)
    assert not g.st.retest, "a second try survived the guard's cap"


def test_a_raised_floor_during_a_second_try_leaves_no_second_try_behind():
    g, held = _into_retest()
    g.cfg = gov.Cfg(floor=held + 1, ceiling=30, target=60, start_w=16)   # the floor: over the level being tried
    g.run(0.2)
    assert g.limit == held + 1 and g.st.mode == "hold" and not g.st.retest


def test_autotune_thermal_step_during_a_second_try_leaves_no_second_try_behind():
    """Autotune.thermal_step and a refused write end the watch with governor.drop: the second try goes with it."""
    g, held = _into_retest()
    gov.drop(g.st)                                      # what Autotune.thermal_step and a refused write do
    g.fps = lambda w: 60.0 if w >= 10 else 45.0          # the next step down holds
    n = len(g.notes)
    g.run(150)
    assert not any("own dip" in x for _, x in g.notes[n:]), "an ordinary step that held was booked as a dip of the game's own"


def test_what_the_stale_second_try_does_next():
    g, held = _into_retest()
    g.cap = held - 1
    g.run(0.2)
    g.fps = lambda w: 60.0 if w >= 10 else 45.0          # the next step down holds
    n = len(g.notes)
    g.run(150)
    print("held", held, "notes:", g.notes[n:n + 8], "dips", len(g.st.dips), "good", g.st.good, "limit", g.limit)
    assert not any("own dip" in x for _, x in g.notes[n:]), "an ordinary step that held was booked as a dip of the game's own"


def test_a_raise_cut_short_by_autotune_leaves_no_tried_level_behind():
    """A step down loses far more than its size explains: `recover` with `tried` = that level. Autotune.thermal_step
    (or a refused write) sets mode = "hold" itself. `tried` stays, and the next loss in hold is judged as that old
    step's: the old level is failed again and the limit goes back to the level before, not judged on its own."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=22), fps=lambda w: 60.0 if w >= 20 else 40.0)
    while not (g.st.mode == "recover" and g.st.tried is not None):
        g.run(0.1)
        assert g.t < 60
    gov.drop(g.st)                                      # Autotune.thermal_step / refused write
    assert g.st.mode == "hold" and g.st.tried is None


def test_a_target_out_of_reach_from_the_first_look_pins_the_start_level_for_two_minutes():
    """R1 (work plan): Midnight Club LA parked, RPCS3's 30 fps lock, target 60: at or under 17 W within 60 s. The
    first look at the full limit compares 30 fps with the 60 fps target as if the target had been held, takes it for
    "a cutscene", and keeps the start level (mid-range, 19 W) as a floor for REACH_FOR_S."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=lambda w: 30.0 if w >= 14 else 2.08 * w)
    g.draw = lambda w, t: float(w)                       # RPCS3 draws the whole limit
    g.run(60)
    print("limit at 60 s:", g.limit, "drop_floor", g.st.drop_floor, "writes", g.writes[:6])
    assert g.limit <= 17


def test_without_a_package_power_reading_the_pin_never_ends():
    """The same with no RAPL energy reading (draw_w None: _next_down keeps drop_floor while `obs.draw_w is None`)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=lambda w: 30.0 if w >= 14 else 2.08 * w)
    g.run(900)
    print("limit at 900 s:", g.limit, "drop_floor", g.st.drop_floor)
    assert g.limit <= 17


def test_a_deep_loss_gets_the_full_limit_once_not_full_low_full():
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


def test_slow_frames_of_the_games_own_at_every_limit_do_not_walk_the_limit_up():
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


@pytest.mark.parametrize("every", [17, 8])
@pytest.mark.parametrize("target", [30, None])
@pytest.mark.parametrize("draws_the_limit", [True, False])
def test_under_the_guards_clock_cap_the_games_own_slow_frames_get_one_watt_and_no_look(every, target, draws_the_limit):
    """The look that says whose the slow frames are is off while the guard caps the CPU's clock. The watt more was
    not: a watt every 10 s, up to the full limit, in the minutes in which the chip is hot. When the cap goes, the look."""
    rough = {"on": False}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target), fps=lambda w: 30.0 if w >= 14 else 2.1 * w,
             shape=lambda i, w, t: 1.15 if rough["on"] and i % every == 0 else 1.0)
    g.draw = (lambda w, t: float(w)) if draws_the_limit else None
    g.run(120)
    held, t0 = g.limit, g.t
    rough["on"], g.thermal = True, True
    g.run(600)
    assert max(g.since(t0) + [held]) <= held + 1, g.writes
    t1, g.thermal = g.t, False
    g.run(120)
    ups = g.since(t1)
    assert ups.count(30) == 1 and max([w for w in ups if w != 30] + [held]) <= held + 1, ups


@pytest.mark.parametrize("cut", ["the guard's cap", "no frames", "Autotune ends the watch"])
def test_a_look_for_slow_frames_that_is_cut_short_says_nothing_in_the_next_look(cut):
    """Slow frames under 23 W, the limit's: a watt more, then the look for whose they are. The look is cut short and
    the slow frames end. The next look at the full limit was judged with the share of slow frames the cut one had
    left: "30 W has none of the slow frames", a limit that holds booked as failed, and the full limit kept."""
    rough = {"on": True}
    g = Game(gov.Cfg(floor=8, ceiling=30, start_w=16),
             shape=lambda i, w, t: 1.3 if rough["on"] and w < 23 and i % 12 == 0 and t > 20 else 1.0)
    g.st.base_slow = 0.0
    while not (g.st.mode == "check" and g.st.edge_look is not None):
        g.run(0.1)
        assert g.t < 200
    held = g.st.prev
    if cut == "the guard's cap":
        g.cap = held
        g.run(1.0)
        g.cap = None                                    # the watts are back: what they give is looked at
    elif cut == "no frames":
        g.run(2.0, frames=False)
    else:
        gov.drop(g.st)                                  # the guard's step, a refused write
        g.st.limit = g.limit = held                     # ... and what is on the chip
    assert g.st.mode == "hold" and g.limit == held
    rough["on"], g.st.ref_at, t0, n = False, -1e9, g.t, len(g.notes)     # (the next look at the full limit is due)
    g.run(30)
    assert [w for w in g.since(t0) if w == 30] and not [x for _, x in g.notes[n:] if "slow frames" in x], g.notes[n:]
    assert held not in g.st.fail or g.st.fail[held][0] < t0


@pytest.mark.parametrize("ratio", [1.3, 2.0, 4.0])
@pytest.mark.parametrize("target", [60, None])
@pytest.mark.parametrize("draws_the_limit", [True, False])
def test_a_menu_that_opens_between_two_looks_is_never_the_reference(ratio, target, draws_the_limit):
    """A menu over a game on a 30 fps lock, opened at any moment of the governor's half second. The look for "more
    frames here than the full limit is believed to give" (5 %) fired on a pace that was part menu, before the menu's
    wait (15 %) had begun, and what the full limit gave in the menu became the reference: the game's own frames were
    then a loss, or the held limit was booked as failed and the full limit kept (47 s at 30 W for a need of 14 W)."""
    for k in range(0, 30, 2):
        opens = 120.0 + k / 60.0
        g = Game(gov.Cfg(floor=8, ceiling=30, target=target), fps=lambda w: 30.0 if w >= 14 else 2.08 * w,
                 shape=lambda i, w, at: 1.0 / ratio if opens <= at < opens + 20.0 else 1.0)
        g.draw = (lambda w, t: float(w)) if draws_the_limit else None
        g.run(119.5)
        while g.t < opens + 20.0:
            g.run(0.5)
            assert g.st.ref < 33, (k, g.st.ref, g.notes[-3:])


def test_a_lighter_scene_that_frees_the_limit_gets_no_look_at_the_full_limit():
    """Most frames, a game that draws the whole limit. A minute after the last look at the full limit the scene gets a
    little lighter: 4 % more frames and the package 3 W under the limit. The limit holds nothing back now (the pace is
    the reference within a few looks): no look at the full limit. Without `binds` in the rule, the "moved 3 %, a
    minute since the last look" clause fires in the looks before the free reference is taken."""
    lift = {"x": 1.0, "free": False}
    g = Game(gov.Cfg(floor=8, ceiling=30), fps=lambda w, t: lift["x"] * (60.0 if w >= 26 else 60.0 - (26 - w)))
    g.draw = lambda w, t: float(w) - (3.0 if lift["free"] else 0.0)
    g.run(40)
    g.run(max(0.0, g.st.ref_at + 62 - g.t))
    assert g.st.mode == "hold" and g.limit < 26
    lift["x"], lift["free"], t0 = 1.04, True, g.t
    g.run(20)
    looks = [(t, n) for t, n in g.notes if t >= t0 and "checking what the full" in n]
    assert looks == [], looks
