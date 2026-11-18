"""The Autotune governor (py_modules/clawdeck/governor.py): its rules, on frames written by hand (no simulator, no
recording). Run: python3 -m pytest -q tests"""
import math
import os
import random
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
    st, act = gov.start(gov.Cfg(floor=8, ceiling=30, target=120), 0.0)
    assert act.limit_w == 19 and st.mode == "hold"
    assert gov.start(gov.Cfg(floor=8, ceiling=30, target=120, start_w=14), 0.0)[1].limit_w == 14
    assert gov.start(gov.Cfg(floor=8, ceiling=20, target=120, start_w=27), 0.0)[1].limit_w == 20      # never over your limit
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
    assert gov.goal(g.st, g.cfg) == pytest.approx(45 * (1 - gov.SUSTAINED), abs=0.2)  # out of reach: what the full limit gives


def test_a_target_out_of_reach_keeps_the_full_limit_or_a_lower_one_that_gives_its_frames():
    """Your 60 out of reach: 55.8 fps from 27 W up, 2 % less a watt under it. No watts for frames under the target
    (D11): 27 W, which gives the full limit's frames. (Held: 26 W, 1.8 % less.)"""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=lambda w: 55.8 if w >= 27 else 55.8 * (1 - 0.02 * (27 - w)))
    g.draw = lambda w, t: float(w)
    g.run(600)
    assert g.limit == 27, (g.limit, g.notes[-3:])


@pytest.mark.parametrize("wobble, held", [(0.0, 20), (0.03, 30)])
def test_a_target_out_of_reach_gets_no_step_down_while_the_game_wobbles_more_than_one_percent(wobble, held):
    """48 fps from 20 W up, your 60 out of reach. A watch can't tell 1 % in a frame rate that moves 3 % by itself (an
    emulator held 28 W for 20 s, 3 % under the full limit): the full limit stays. A steady game steps down."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=lambda w: 48.0 if w >= 20 else 40.0,
             shape=lambda i, w, t: 1.0 + wobble * math.sin(i * 0.37))
    g.run(300)
    assert g.limit == held, (g.limit, g.notes[-3:])


@pytest.mark.parametrize("fall_s, end, share", [(2, 26, 0.97), (4, 25, 0.97), (8, 18, 0.97), (8, 18, 0.90)])
def test_a_place_that_gets_heavier_under_the_look_keeps_the_full_limit(fall_s, end, share):
    """Walking into a heavier place: the frame rate falls for some seconds at every limit. The look at the full limit
    sees it fall, so what the limit before gave when it lost is of a lighter moment: no way back on it. (The limit went
    back, lost again at once, and so on: 8 writes in 7 s on the Claw, 10 in 10 s here.)"""
    base = lambda t: 37.0 if t < 60 else max(end, 37.0 - (37.0 - end) * (t - 60) / fall_s)
    g = Game(gov.Cfg(floor=8, ceiling=32, target=45, start_w=32), fps=lambda w, t: base(t) * (1.0 if w >= 28 else share))
    g.draw = lambda w, t: min(float(w), 27.0)
    g.run(59)
    assert g.limit == 28
    g.run(fall_s + 21)
    assert g.since(60) == [32]


@pytest.mark.parametrize("fall_s, end", [(15, 18), (8, 30)])
def test_a_place_that_gets_heavier_slowly_keeps_the_full_limit_at_its_second_loss(fall_s, end):
    """... too slowly to show in one look: the look says "the scene", the limit goes back and loses again within
    AFTER_LOSS_S. Then the full limit stays (14 writes in 16 s before)."""
    base = lambda t: 37.0 if t < 60 else max(end, 37.0 - (37.0 - end) * (t - 60) / fall_s)
    g = Game(gov.Cfg(floor=8, ceiling=32, target=45, start_w=32), fps=lambda w, t: base(t) * (1.0 if w >= 28 else 0.97))
    g.draw = lambda w, t: min(float(w), 27.0)
    g.run(60 + fall_s + 20)
    assert g.since(60) == [32, 28, 32]


@pytest.mark.parametrize("gap, writes", [(4.0, [30, 16, 30]), (8.0, [30, 16, 30, 16])])
def test_a_second_fall_of_the_scene_gets_its_own_look_after_five_seconds(gap, writes):
    """Two falls of the scene at every limit, `gap` apart: within AFTER_LOSS_S of the answer "the scene" the second one
    keeps the full limit, later it is a fall like the first and the limit goes back."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 60.0).run(3)
    g.fps = lambda w: 45.0
    g.run(gap)
    g.fps = lambda w: 36.0
    g.run(8)
    assert [w for _, w in g.writes] == writes, g.writes



@pytest.mark.parametrize("gap", [1.0, 2.5, 3.0, 4.0])
def test_a_second_fall_within_five_seconds_keeps_the_full_limit_also_while_the_draw_climbs(gap):
    """As above, with the package draw climbing to a raised limit over 2.5 s (the chip takes the watts in that time),
    the second fall `gap` s after the way back: within AFTER_LOSS_S of the answer it keeps the full limit. The
    climbing draw moved the start of the watch, and the window was 2.5 s."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 60.0)

    def draw(limit, t):
        if not g.writes:
            return float(limit)
        at, w = g.writes[-1]
        before = g.writes[-2][1] if len(g.writes) >= 2 else 16
        return float(w) if w <= before else before + (w - before) * min(1.0, (t - at) / 2.5)
    g.draw = draw
    g.run(3)
    g.fps = lambda w: 45.0
    while not any("back to 16" in n for _, n in g.notes):
        g.run(0.1)
    g.run(gap)
    g.fps = lambda w: 36.0
    g.run(10)
    assert [w for _, w in g.writes] == [30, 16, 30], g.writes


@pytest.mark.parametrize("at", [3.25, 3.35, 3.44])
def test_a_video_that_begins_between_two_looks_gets_its_limit_back(at):
    """A 30 fps video at every limit begins a moment before a look: the last ten frames are part game, part video, the
    full limit gives 30, and the limit goes back with a band of that mixed rate. The video's own 30 fps are a loss
    against it; the second look finds the 30 fps of the first: no heavier place, back again (fuzz seed 55: the full
    limit for the 100 s of the video)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 60.0,
             shape=lambda i, w, t: 2.0 if t >= at else 1.0).run(14)
    assert [w for _, w in g.writes] == [30, 16, 30, 16], g.writes


@pytest.mark.parametrize("period, seconds", [(300, 900), (420, 300)])
def test_one_burst_of_the_games_own_slow_frames_does_not_fail_a_step(period, seconds):
    """A game on its 60 fps limit from 10 W, with 6 frames in a row 30 % long every `period` frames at every limit. A
    step whose watch has one such burst failed for slow frames, at every level (14 to 22 W held for a need of 10 W;
    walking in Enshrouded: 26 W for a need of 20 W). One burst is the game's own, as at a held limit."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=lambda w: 60.0 if w >= 10 else 6.0 * w,
             shape=lambda i, w, t: 1.3 if i % period < 6 else 1.0)
    g.draw = lambda w, t: float(min(w, 12))
    g.run(seconds)
    assert g.limit <= 11 and not [n for _, n in g.notes if "slow frames; back" in n], (g.limit, g.notes[-4:])


def test_one_burst_of_slow_frames_apart_in_a_steps_watch_does_not_fail_it():
    """A game on its 60 fps limit from 10 W, with every 5th frame 12 % long for half a second every 10 s at every
    limit: none in a row, so each counts, and all in one burst (within a third of the watch): the game's own."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=lambda w: 60.0 if w >= 10 else 6.0 * w,
             shape=lambda i, w, t: 1.12 if 1.0 <= t % 10 < 1.5 and i % 5 == 0 else 1.0)
    g.draw = lambda w, t: float(min(w, 12))
    g.run(600)
    assert g.limit <= 11 and not [n for _, n in g.notes if "slow frames" in n], (g.limit, g.notes[-4:])


@pytest.mark.parametrize("target", [60, None])
@pytest.mark.parametrize("period", [150, 300, 420, 600])
@pytest.mark.parametrize("burst, stretch", [(3, 1.3), (6, 1.3), (10, 1.15)])
def test_the_games_own_bursts_of_slow_frames_at_random_times_do_not_walk_the_limit_up(burst, stretch, period, target):
    """As above with the bursts at random times, `period` frames apart on average: two of them often fall in one
    watch or in the 240 frames of a held limit. They failed level after level for slow frames: 16.7 W on average for
    a need of 10 W (Enshrouded at the Flame altar: 26 W for a need of 18 W, the installed Autotune 24.7 W)."""
    rng, left = random.Random(period * burst), [0, -1, 1.0]

    def shape(i, w, t):
        if i != left[1]:
            left[1] = i
            if left[0] == 0 and rng.random() < 1.0 / period:
                left[0] = burst
            left[2] = stretch if left[0] else 1.0
            left[0] = max(0, left[0] - 1)
        return left[2]
    g = Game(gov.Cfg(floor=8, ceiling=30, target=float(target or 120), start_w=30),
             fps=lambda w: 60.0 if w >= 10 else 6.0 * w, shape=shape)
    g.draw = lambda w, t: float(min(w, 12))
    g.run(300)
    assert not [n for _, n in g.notes if "slow frames" in n] and not [w for w, f in g.st.fail.items() if f[3]], g.notes


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


# ---- what the review of 2026-11-15 broke, each with the scenario that broke it ---------------------------------------
HOLDS_FROM_14 = lambda w: 60.0 if w >= 14 else 60.0 * w / 14          # a 60 fps game that needs 14 W


def test_a_video_changes_nothing_for_the_game_that_comes_back():
    """A cutscene gives 30 fps at any limit. One look at the full limit says "the scene, not the limit". The levels
    that failed in the game still count in the video (the full limit gives fewer frames there, not more: no lighter
    scene), so the limit stays where the game needs it and the game is back with all its frames (the limit used to
    go to the floor in the video, and the game ran at 34 fps of 60 for a minute afterwards)."""
    for seconds in (15, 40, 90, 300):
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=HOLDS_FROM_14).run(60)
        held = g.limit
        assert 14 <= held <= 16
        g.fps = lambda w: 30.0
        g.run(seconds)
        assert g.since(60) == [30, held], (seconds, g.since(60))        # one look, and back
        g.fps, t0 = HOLDS_FROM_14, g.t
        g.run(30)
        assert _seconds_under(g, t0, g.t, HOLDS_FROM_14, 57.0) <= 1.0, (seconds, g.since(t0))
        assert g.limit == held and g.st.ref == pytest.approx(60, abs=1) and not gov.out_of_reach(g.st, g.cfg), seconds


@pytest.mark.parametrize("target", [60, 120])
@pytest.mark.parametrize("draw_known", [False, True])
def test_after_a_video_longer_than_what_failed_counts_the_game_gets_the_full_limit_within_seconds(target, draw_known):
    """What failed in the game counts in a video for RETRY_MAX_S; after that the limit goes down to what the video
    needs. The game back there gives far more frames than the video did, and fewer than it can: the full limit is
    looked at as fast as a loss is answered, on the first 2 N frames that agree."""
    for need_w in (14, 20, 26):
        game = lambda w: 60.0 * min(1.0, w / need_w)
        g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30), fps=game)
        if draw_known:
            g.draw = lambda w, t: float(w)
        g.run(90)
        g.fps = lambda w: 30.0
        g.run(gov.RETRY_MAX_S + 240)
        low = g.limit
        assert low < need_w - 2, (need_w, low, g.notes[-4:])
        for phase in (0.0, 0.2, 0.4):                                # the game comes back at any moment of a look
            g.run(20 + phase)
            g.fps, t0 = game, g.t
            g.run(20)
            lost = _seconds_under(g, t0, g.t, game, 57.0)
            assert lost <= 2.5 and game(g.limit) >= 57, (need_w, phase, lost, [n for n in g.notes if n[0] >= t0][:6])
            g.fps = lambda w: 30.0
            g.run(gov.RETRY_MAX_S + 240)


def test_a_fast_menu_does_not_pin_the_full_limit():
    """8 s of a 240 fps menu set the reference to 240; the game's 60 fps were then "a loading screen" for good, at the
    full limit. Frames that far from the usual rate are not judged, and when they stay they are the game."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=16), fps=HOLDS_FROM_14).run(60)
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
    assert len(g.writes) == n and g.st.usual == pytest.approx(60, abs=1)
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
    g = Game(gov.Cfg(floor=8, ceiling=30, target=50, start_w=30), fps=lambda w: min(50.0, 2.5 * w),
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
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=16), fps=faster).run(300)
    assert not any("slow frames" in n for _, n in g.notes)
    some = ([33.3] * 14 + [40.0]) * 5 + [33.3] * 10
    assert gov._edge(some, 0.0) == 5 and gov._edge(some, 0.06) == 0
    assert gov._edge([33.3] * 70 + [40.0] * 5, 0.0) == 0 and gov._edge([33.3] * 40 + [40.0] * 40, 0.0) == 0    # a slower stretch
    assert gov._edge([33.3] * 80 + [36.0, 38.0] * 20, 0.0) == 0                                  # (one that wobbles)


def test_the_edge_rule_counts_slow_frames_alone_and_in_pairs_not_three_in_a_row():
    """The edge of the limit makes its slow frames one or two at a time (Midnight Club: Los Angeles driving at 14 W:
    runs of 1 and 2); three or more in a row are a burst of the game's own."""
    pairs = lambda i, w, t: 1.12 if (w < 16 and i % 36 in (0, 1)) else 1.0
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=20), fps=lambda w: 60.0, shape=pairs).run(60)
    assert g.limit == 16 and 14 in g.st.fail and any("slow frames" in n for _, n in g.notes), (g.limit, g.notes)
    assert gov._slow_frames(([33.3] * 14 + [40.0] * 2) * 5 + [33.3] * 10) == (10, 70)
    assert gov._slow_frames(([33.3] * 13 + [40.0] * 3) * 5 + [33.3] * 10) == (0, 70)
    assert gov._slow_frames([33.3] * 20 + [40.0] * 2 + [33.3] * 10) == (2, 12)       # (a pair at the end of the judged)
    assert gov._edge(([33.3] * 43 + [40.0]) * 5 + [33.3] * 20, 0.0) == 0             # 2.3 %: under EDGE
    assert gov._edge(([33.3] * 23 + [40.0]) * 9 + [33.3] * 24, 0.0) == 9             # 4.1 %
    cluster = [40.0 if 400 <= i < 591 and i % 10 == 0 else 33.3 for i in range(600)]
    far = [40.0 if 60 <= i < 63 else f for i, f in enumerate(cluster)]
    assert gov._burst(cluster) and gov._burst(far)                  # (three in a row far from one burst: the game's too)


def test_a_small_loss_that_stays_gets_the_full_limit_and_ends_just_over_the_need():
    """The need rises by a watt: 56 fps at the held limit, 1.8 % under the 57 that count as 60. No clear loss on ten
    frames; the pace is under the goal and stays there. The full limit says it is the limit's, and the way down from
    there ends a watt or two over the level that lost."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=18), fps=HOLDS_FROM_14).run(40)
    held, t0 = g.limit, g.t
    g.fps = lambda w: 60.0 if w > held else 56.0
    g.run(90)
    first = next((at, w) for at, w in g.writes if at >= t0)
    assert first[1] == 30 and first[0] - t0 <= gov.DWELL_S + 2.5, g.writes[-8:]
    assert held in g.st.fail and g.limit in (held + 1, held + 2), (held, g.limit, g.since(t0))
    assert _seconds_under(g, t0, g.t, g.fps, 57.0) <= gov.DWELL_S + 2.5 + 3 * gov.DWELL_S, g.since(t0)


def test_another_scene_forgets_the_levels_that_failed():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w: min(50.0, 2.5 * w)).run(60)
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
    then, the full limit gives more too, and the held one may cost more than the margin."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w: min(50.0, 2.5 * w)).run(60)
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



@pytest.mark.parametrize("target", [60, 70])
def test_watts_the_guard_gives_back_get_no_second_try_of_the_level_its_cap_held(target):
    """The game needs 20 W. The guard caps the limit at 14 W, then gives 2 W back every 30 s. A loss at the level
    the cap held is the cap's, not a dip of the game's own: each rise gets one look at the new top, not a second try
    of that level (3 writes and a failed step each)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target), fps=lambda w: 60.0 if w >= 20 else 60.0 - 3.0 * (20 - w))
    g.draw = lambda w, t: float(w)
    g.run(120)
    t0 = g.t
    for cap in (14, 16, 18, 20, 22, None):
        g.cap = cap
        g.run(30)
        assert cap is None or max([g.limit] + [w for at, w in g.writes if at >= g.t - 30]) <= cap
    assert not [n for t, n in g.notes if t >= t0 and "once more" in n], [n for n in g.notes if n[0] >= t0]
    assert len([w for at, w in g.writes if t0 <= at < t0 + 90]) <= 6, g.since(t0)


@pytest.mark.parametrize("under", [16, 14, 12])
def test_the_end_of_the_clock_cap_during_a_step_down_forgets_what_the_step_would_have_put_back(under):
    """Under the guard's CPU clock cap the game gives 45 fps from 12 W; without it 60 fps from 16 W (target 60). The
    cap ends while a step down under `under` W is watched, and Autotune forgets what was measured under it. The
    watch's end put the band and the ratio from under the cap back: the limit stood under the need for up to 575 s."""
    capped = {"on": True}

    def fps(w):
        return min(45.0 if capped["on"] else 60.0, 3.75 * w)
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=fps)
    g.draw = lambda w, t: float(w)
    g.thermal = True
    g.run(30)
    for _ in range(3000):
        if g.st.mode == "probe" and g.limit < under:
            break
        g.run(0.1)
    assert g.st.mode == "probe" and g.limit < under, (g.limit, g.notes[-3:])
    capped["on"], g.thermal = False, False
    gov.forget(g.st)                                         # autotune.thermal_clock(None)
    short = 0
    for _ in range(300):
        g.run(1)
        short += fps(g.limit) < 57.0
    assert short <= 10, (short, g.limit, g.notes[-6:])


@pytest.mark.parametrize("back_to", [None, 24])
def test_watts_the_guard_gives_back_during_a_step_down_end_its_watch(back_to):
    """Target 120 out of reach: 50 fps at 30 W, 1 % less a watt under it. The guard's cap of 16 W is lifted (to 30 W,
    or to 24 W) while a step under 16 W is watched. What the watch compared against was measured under the cap: its
    end put that band and ratio back, and the limit stood under the goal for 99 s."""
    def fps(w):
        return 50.0 * 0.99 ** (30 - w)
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=fps)
    g.draw = lambda w, t: float(w)
    g.cap = 16
    g.run(20)
    while not (g.st.mode == "probe" and g.limit < 16 and g.t - g.st.wrote_at > 0.5) and g.t < 300:
        g.run(0.1)
    assert g.st.mode == "probe" and g.limit < 16, (g.limit, g.notes[-6:])
    g.cap = back_to
    line = fps(back_to or 30) * (1 - gov.TOL)
    under = 0.0
    for _ in range(3000):
        under += 0.1 * (fps(g.limit) < line)
        g.run(0.1)
    assert under <= 5, (under, g.limit, g.notes[-6:])


@pytest.mark.parametrize("nth", [1, 2, 3])
def test_the_end_of_the_clock_cap_during_a_step_down_does_not_judge_the_step_against_the_target(nth):
    """Target 120 out of reach: 50 fps from 16 W, 1.5 % less a watt under it; the guard's CPU clock cap changes no
    frame. It ends while the `nth` step down is watched. The watch went on against the target (nothing known of the
    full limit any more): a level that lost nothing failed, and the full limit was written twice, not once."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w: 50.0 * 0.985 ** max(0, 16 - w))
    g.draw = lambda w, t: float(w)
    g.thermal = True
    g.run(30)
    steps = set()
    while len(steps) < nth and g.t < 300:
        g.run(0.1)
        if g.st.mode == "probe":
            steps.add(g.st.wrote_at)
    assert len(steps) == nth, (steps, g.notes[-6:])
    g.run(0.5)
    t0 = g.t
    g.thermal = False
    gov.forget(g.st)                                         # autotune.thermal_clock(None)
    g.run(60)
    assert len([w for w in g.since(t0) if w == 30]) <= 1, [n for n in g.notes if n[0] >= t0]  # (one look: nothing known)


@pytest.mark.parametrize("dip", [0.6, 1.0])
def test_watts_the_guard_gives_back_over_a_level_it_did_not_hold_leave_its_second_try(dip):
    """Target 60, the game needs 10 W (the floor), held at 10 W under a cap of 20 W that never held it. The guard
    gives 2 W back; 20 s later the game dips by itself for `dip` s at every limit: the level gets its second try
    and holds again, as without the guard (it was "10 W was too low" and 11 to 12 W for a need of 10)."""
    low = {"on": False}
    g = Game(gov.Cfg(floor=10, ceiling=30, target=60, start_w=10), fps=lambda w: 45.0 if low["on"] else 60.0)
    g.draw = lambda w, t: float(w)
    g.cap = 20
    g.run(120)
    g.cap = 22
    g.run(20)
    t0 = g.t
    low["on"] = True
    g.run(dip)
    low["on"] = False
    g.run(60)
    assert g.limit == 10 and [n for t, n in g.notes if t >= t0 and "holds again" in n], [n for n in g.notes if n[0] >= t0]


@pytest.mark.parametrize("menu_s, menu_w, lock", [(5, 9.0, 60), (20, 9.0, 60), (20, 17.5, 60), (20, 9.0, 30)])
def test_a_menu_at_the_games_own_frame_rate_takes_no_step_down_before_its_frames_are_counted_free(menu_s, menu_w, lock):
    """A game on its frame limit (`lock` fps) that needs 20 W and draws its need; its menu at the same frame rate draws
    `menu_w` W. The menu's first looks, before a pace of frames all drawn under the limit, took a step down: down to
    the floor in the menu's first seconds, and the game came back at it."""
    menu = {"on": False}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=lock, start_w=30),
             fps=lambda w: float(lock) if menu["on"] or w >= 20 else lock - lock / 20 * (20 - w))
    g.draw = lambda w, t: menu_w if menu["on"] else float(min(w, 20))
    g.run(240)
    held, t0 = g.limit, g.t
    menu["on"] = True
    g.run(menu_s)
    menu["on"] = False
    assert [w for at, w in g.writes if at >= t0 and w < held] == [], (held, [n for n in g.notes if n[0] >= t0][:6])


def test_a_clock_cap_from_the_guard_stops_the_looks_at_the_full_limit_not_the_steps_down():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w: min(50.0, 2.5 * w))
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


def test_a_step_down_has_to_leave_the_wobble_over_the_goal():
    for under, held in ((57.6, 20), (59.0, 8)):              # 1 % over the goal of 57: not kept; 3.5 % over: kept
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=22), fps=lambda w: 60.0 if w >= 20 else under).run(200)
        assert g.limit == held, (under, g.limit, g.notes[-3:])
    for under, held in ((47.2, 20), (47.8, 8)):              # your target out of reach: the goal is 1 % under the 48
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=lambda w: 48.0 if w >= 20 else under).run(200)
        assert g.limit == held, (under, g.limit, g.notes[-3:])                          # fps the full limit gives
    # 0.8 % a watt, less than counts as a loss from one level to the next: what the full limit gives is the measure
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w: 50.0 * 0.992 ** (30 - w)).run(200)
    assert g.limit == 29, (g.limit, g.notes[-3:])


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


def test_a_watt_that_changes_nothing_has_the_full_limit_say_whose_slow_frames_they_are():
    """Slow frames at a held limit, and as many a watt higher, twice. The limit's (none at the full limit; the edge is
    some watts up): the limit ends over the edge. The game's own in this place (as many at the full limit): one short
    look, then the limit stays (a watt more every ten seconds went up to your limit)."""
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
        assert len(looks) <= 1 and g.limit <= held_w + gov.STEP_W, (held_w, share, late)
        assert all(w <= held_w + gov.STEP_W for _, w in late if w != 30), late



def test_a_menu_over_the_game_takes_no_step_down_whatever_it_shows():
    """A menu or a map over a 30 fps game whose target of 60 is out of reach, for up to half a minute: 40 to 90 fps at
    any limit. The menu's 60 fps took the limit to the floor, and the game came back at 19 fps.
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
    (120, lambda w: min(50.0, 2.5 * w), (60.0, 70.0, 100.0)),                    # no target set
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


@pytest.mark.parametrize("target", [90, 120])
@pytest.mark.parametrize("draw_known", [False, True])
def test_far_more_frames_at_a_limit_that_holds_them_back_get_the_full_limit_after_a_look(target, draw_known):
    """Far more frames than before are a menu's for half a minute, but not when the limit holds them back: the game back
    from a 30 fps video at the limit the video needed (46 of 120 fps for 31 s, cloud fuzz seed 51, 2026-11-16), or a
    lighter place that wants more watts. One look at the full limit says it, whatever the target
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




@pytest.mark.parametrize("target", [120, 240])
@pytest.mark.parametrize("draw_known", [False, True])
def test_far_too_many_frames_to_judge_at_a_limit_that_holds_them_back_get_a_look_too(target, draw_known):
    """After a short video the level that held before it is on the chip, and a heavier, faster place gives over 2.5
    times the video's frames there: not judged for half a minute, and 21 % under the need all that time (cloud fuzz seed
    117, 2026-11-16). One look at the full limit, as for far more frames that are judged."""
    for need_w in (22, 26):
        after = lambda w: 200.0 * min(1.0, w / need_w)
        g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=20), fps=lambda w: 60.0 * min(1.0, w / 12))
        if draw_known:
            g.draw = lambda w, t: float(w)
        g.run(140)                                                  # (at 150 s a failed level is being tried again)
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
    g = Game(gov.Cfg(floor=8, ceiling=17, target=120, start_w=17), fps=lambda w, t: game(w))
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



@pytest.mark.parametrize("target", [120, 90])
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
        # (the step is taken back before its watch has ended)
        assert [t for t, w in g.writes if t >= t0 and w == 30][0] - t0 <= 1.5, [x for x in g.writes if x[0] >= t0]



def _seconds_under(g, t0, t1, fps, line):
    """Seconds between t0 and t1 at a limit that gives less than `line` fps."""
    at, w, total = t0, [x for a, x in g.writes if a < t0][-1], 0.0
    for a, x in [(a, x) for a, x in g.writes if t0 <= a < t1] + [(t1, None)]:
        total += (a - at) if fps(w) < line else 0.0
        at, w = a, x if x is not None else w
    return total


@pytest.mark.parametrize("target", [120, 90])
@pytest.mark.parametrize("before_w, after_w", [(14, 14), (30, 20), (30, 26), (22, 16)])   # 30: held one watt under the full limit
def test_frames_that_stayed_after_a_video_are_the_game_before_any_step_down(target, before_w, after_w):
    """After a video the game is back at the level that held before it, with all its frames there. The look of the
    wait finds as many at the full limit and takes no reference; when the frames had stayed half a minute, the video's
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



@pytest.mark.parametrize("target", [120, 60])
@pytest.mark.parametrize("fps_before", [46.0, 30.0])
def test_far_more_frames_that_stayed_at_the_full_limit_are_the_reference_before_any_step_down(target, fps_before):
    """A lighter place after a heavy one, at the full limit: far more frames, so nothing moves for half a minute. When
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
        g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=20), fps=game).run(150)
        held, n, fail = g.limit, len(g.writes), dict(g.st.fail)
        g.fps = lambda w: 120.0
        g.run(seconds)
        assert [w for _, w in g.writes[n:]] in ([], [30, held]), (seconds, g.writes[n:])
        g.fps = game
        g.run(30)
        assert g.limit == held and g.st.ref == pytest.approx(40, abs=1) and set(fail) <= set(g.st.fail), (seconds, g.writes[n:])


def test_a_frame_rate_that_swings_across_far_more_gets_one_such_look_in_half_a_minute():
    """A rate that goes over and under "far more than the game gave" every few seconds starts the wait again each time;
    the look at the full limit that belongs to the wait does not come more often than twice a minute."""
    for period in (7.0, 12.0, 20.0):
        g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=20), fps=lambda w: min(40.0, 40.0 * w / 14)).run(150)
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
        g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=20), fps=game).run(150)
        held = g.limit
        g.fps = menu
        g.run(seconds)
        assert g.limit == 30, (seconds, g.notes[-3:])
        g.fps = game
        g.run(90)
        assert abs(g.limit - held) <= 1 and g.st.ref == pytest.approx(40, abs=1), (seconds, held, g.limit, g.notes[-5:])


@pytest.mark.parametrize("after", [150, 160, 200])
@pytest.mark.parametrize("seconds", [10, 30])
def test_back_from_a_menu_that_got_the_full_limit_the_game_has_its_limit_within_seconds_and_no_look(seconds, after):
    """A menu over a 30 fps game with a target of 60, held back by the game's limit (42 fps there, 60 at the full
    limit): the menu gets the full limit. The game back: the scene its limit held in is no new one, so that limit comes
    back without the 30 s a new scene waits, and the step down from the full limit is its own look (the frames before
    it were the full limit's): two writes in all."""
    menu = {"on": False}
    game = lambda w: 30.0 if w >= 14 else 30.0 * w / 14
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=lambda w, t: min(60.0, 3.0 * w) if menu["on"] else game(w))
    g.draw = lambda w, t: float(w)
    g.run(after)                                         # (16, 26 and 66 s after the limit's last change)
    held, t0, menu["on"] = g.limit, g.t, True
    assert held == 14
    g.run(seconds)
    assert g.since(t0) == [30]
    menu["on"] = False
    g.run(10)
    assert g.since(t0) == [30, held], [n for n in g.notes if n[0] >= t0]
    g.run(110)
    assert g.since(t0) == [30, held], [n for n in g.notes if n[0] >= t0]


def test_a_limit_that_held_for_twenty_seconds_is_the_one_a_game_gets_back_after_a_menu():
    menu = {"on": False}
    game = lambda w: 30.0 if w >= 14 else 30.0 * w / 14
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=lambda w, t: min(60.0, 3.0 * w) if menu["on"] else game(w))
    g.draw = lambda w, t: float(w)
    g.run(46)
    t0, menu["on"] = g.t, True
    assert g.limit == 14 and 25 < t0 - g.writes[-1][0] < 30
    g.run(10)
    menu["on"] = False
    g.run(10)
    assert g.since(t0) == [30, 14], [n for n in g.notes if n[0] >= t0]


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
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w: 60.0)
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


def test_after_three_steps_down_in_a_row_the_rules_of_holding_get_a_turn():
    g = Game(gov.Cfg(floor=4, ceiling=40, target=60, start_w=40), fps=lambda w: 60.0).run(40)
    at = [t for t, w in g.writes]
    gaps = [round(b - a, 1) for a, b in zip(at, at[1:])]
    assert gov.CHAIN == 3 and len(at) >= 8 and max(gaps[:2]) <= 2.7 and gaps[2] >= gov.AFTER_LOSS_S   # 2.5 s a step, then a pause



# ---- second review (2026-11-15): what broke, as tests ----------------------------------------------------------------
def test_a_target_out_of_reach_from_the_first_look_does_not_pin_the_start_level():
    """The full limit gives 46 fps, 12 W already do: the first look at the full limit read as "much less than the 60
    before" and set a floor at the start level that never went away (19 W for the whole session, and saved for the
    next). The target was never held, so that first look is no drop: the way down starts at once."""
    for full in (46.0, 31.0, 55.0):
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=lambda w: full if w >= 12 else full * w / 12)
        g.draw = lambda w, t: float(w)                               # it draws what it gets (an emulator that spins)
        g.run(60)
        assert g.limit <= 13 and g.st.mode == "hold", (full, g.limit, g.writes)
        g.run(400)
        assert 12 <= g.limit <= 13 and g.st.mode == "hold", (full, g.limit)


def _video_game(video_from, video_to):
    """A 60 fps game on a frame limit that needs 16 W and gives 30 fps at 8 W; a 30 fps video (6 W at any limit)."""
    video = lambda t: video_from <= t < video_to
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60),
             fps=lambda w, t: 30.0 if video(t) else min(60.0, 30.0 + 30.0 * (w - 8) / 8.0))
    g.draw = lambda w, t: 6.0 if video(t) else min(float(w), 16.0)
    return g



@pytest.mark.parametrize("target", [120, 60])
def test_a_video_takes_no_step_down_from_a_game_that_needs_the_full_limit(target):
    """At the full limit, a video: half the frames from one look to the next, and the package far under the limit.
    The levels that failed in the game count in the video: no step down, and the game is back with all its watts."""
    game = lambda w: 60.0 * (w / 30) ** 3                             # needs it all
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30), fps=game)
    g.draw = lambda w, t: float(w)
    g.run(40)
    assert g.limit == 30 and g.st.fail
    g.fps, g.draw = (lambda w: 30.0), (lambda w, t: 6.0)
    t0 = g.t
    g.run(120)
    assert g.since(t0) == [], g.since(t0)
    g.fps, g.draw = game, (lambda w, t: float(w))
    g.run(20)
    assert g.limit == 30 and _seconds_under(g, t0, g.t, game, 57.0) == 0


def test_a_short_video_keeps_the_level_that_held_before_it():
    g = _video_game(150, 190)
    g.run(149)
    held = g.limit
    assert 16 <= held <= 18
    g.run(41)                                                        # the video: half the frames at every limit
    assert all(w >= held for t, w in g.writes if t >= 149)           # nothing under it: what failed in the game counts
    g.run(60)
    assert g.limit == held and g.st.ref > 57                         # the game is back, at its frame rate
    assert _seconds_under(g, 190, g.t, lambda w: min(60.0, 30.0 + 30.0 * (w - 8) / 8.0), 57.0) <= 1.0



@pytest.mark.parametrize("target", [120, 60])
def test_a_heavier_place_after_a_video_gets_the_look_when_the_package_draws_the_whole_limit(target):
    """After a video the game is in a heavier place: at the limit that held before, more frames than the video's and
    fewer than the game's. The package draws all of the limit, which it did not in the video: that is the sign for the
    look, seconds after it began, as without the far more frames (cloud fuzz seeds 82, 150, 2026-11-16: the wait
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
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w: 40.0 * min(1.0, w / 14))
    g.draw = lambda limit, t: float(limit)
    while not (g.st.mode == "hold" and g.limit == 14 and 13 in g.st.fail and g.t - g.st.changed_at >= 3) and g.t < 60:
        g.run(0.5)
    assert g.limit == 14, (g.t, g.limit)
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



@pytest.mark.parametrize("target", [120, 60])
def test_a_menu_over_a_game_that_needs_the_full_limit_changes_nothing(target):
    """A game that gets 30 fps from the full limit; a menu over it at 60 fps for 20 s. No step down from the menu's
    frames (no step in a scene that has not stayed SCENE_S), and none in the seconds after it."""
    game = lambda w: 30.0 * min(1.0, w / 30) ** 4
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30), fps=game).run(60)
    assert g.limit == 30 and g.st.ref == pytest.approx(30, abs=0.5)
    t0 = g.t
    g.fps = lambda w: 60.0
    g.run(20)
    g.fps = game
    g.run(10)
    assert g.since(t0) == [] and g.st.ref == pytest.approx(30, abs=0.5), g.since(t0)


def test_a_menu_at_a_limit_that_holds_the_chip_gets_one_look_and_the_game_its_limit_back():
    """A menu at 60 fps over a 30 fps game, the package at the limit: one look at the full limit. As many frames
    there: back. The game back: the band this limit had before the menu is known again, without a look, and the limit
    is not booked as failed."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w: 30.0 * min(1.0, w / 20))
    g.draw = lambda limit, t: float(limit)
    g.run(90)
    held, t0, game = g.limit, g.t, g.fps
    assert 19 <= held <= 22 and g.st.ref == pytest.approx(30, abs=0.5)
    g.fps = lambda w: 60.0
    g.run(20)
    assert g.since(t0) == [30, held], (g.since(t0), [n for n in g.notes if n[0] >= t0][:4])
    g.fps = game
    g.run(20)
    assert g.since(t0) == [30, held] and held not in g.st.fail and g.st.ref == pytest.approx(30, abs=0.5)


def test_one_watt_under_the_full_limit_gets_no_look_while_the_frames_say_nothing():
    """What one watt more gives is not worth a look: at one watt under the full limit there is none in twelve minutes."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w: 60.0 * min(1.0, w / 30) ** 0.25).run(120)
    t0 = g.t
    assert g.limit == 29, g.notes[-3:]
    g.run(720)
    assert 30 not in g.since(t0), [n for n in g.notes if n[0] >= t0][:4]


@pytest.mark.parametrize("target, looks", [(60, 0), (120, 2)])
def test_the_full_limit_is_asked_by_the_clock_only_every_ten_minutes_and_only_for_a_target_out_of_reach(target, looks):
    """A game that holds 60 fps at 14 W and shows nothing, for twenty minutes. With target 60 the frames are the
    target: the full limit is not written once. With a target out of reach the goal is what the full limit gave: it
    is asked again every ten minutes (a scene that got lighter under a step shows in no frame at the held limit) and
    not more often (it was asked after every run of steps too: on the Claw those looks were 9 of the 30 changes in
    three minutes of Enshrouded, and each found the frames it had). The other writes are the next tries of the levels
    under 14 W."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30), fps=HOLDS_FROM_14).run(100)
    t0 = g.t
    assert g.limit == 14
    g.run(1200)
    assert g.limit == 14 and set(g.since(t0)) <= {12, 13, 14, 30}, g.since(t0)
    asked = [t for t, n in g.notes if t >= t0 and "the full" in n and "checking" in n]
    assert len(asked) == looks == g.since(t0).count(30), [n for n in g.notes if n[0] >= t0]
    assert all(b - a >= gov.CHECK_MAX_S for a, b in zip(asked, asked[1:]))


@pytest.mark.parametrize("at", [1.0, 1.5, 2.0])
def test_a_step_that_held_while_the_frames_were_not_steady_is_followed_by_ten_quiet_seconds(at):
    """One half second of 4 % fewer frames while the first step down is watched: the step holds, and the next one waits
    10 s from there (2.5 s of watch and 10 s)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=HOLDS_FROM_14)
    while not g.writes:
        g.run(0.1)
    t1 = g.writes[0][0]
    g.fps = lambda w, t: HOLDS_FROM_14(w) * (0.96 if t1 + at <= t < t1 + at + 0.5 else 1.0)
    g.run(20)
    assert g.writes[1][1] < g.writes[0][1] and 12.0 <= g.writes[1][0] - t1 <= 14.0, g.writes[:3]


@pytest.mark.parametrize("every, steps", [(2.5, False), (4.0, True)])
def test_no_step_down_while_a_half_second_of_the_last_three_was_slower(every, steps):
    """A game that makes 4 % fewer frames for half a second every 2.5 s is never steady for 3 s: no step down. With
    such a half second every 4 s there are 3 steady seconds between two of them: the way down goes on."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=20),
             fps=lambda w, t: HOLDS_FROM_14(w) * (0.96 if int(t / 0.5) % int(every / 0.5) == 0 else 1.0)).run(120)
    assert bool([w for t, w in g.writes if w < 20]) == steps, g.writes[:8]


def test_a_loading_screen_does_not_lower_the_reference():
    """No frames and a package far under the limit for some seconds. The pace is still that of the frames before
    (rendered with the limit holding the game back) while the look's draw says the limit holds nothing back: the looks
    as the loading screen began and ended took that pace as the reference (55.8 -> 53.8 -> 51.6 fps over three
    loading screens, and the limit followed it down). Here as the plugin hands frames on: stamped at the look's time,
    so three looks without frames are not yet "no frames"."""
    cfg = gov.Cfg(floor=8, ceiling=30, target=120)
    g = Game(cfg, fps=lambda w: 56.0 if w >= 24 else 56.0 - (24 - w))        # a frame less for each watt under 24
    g.draw = lambda w, t: float(w)
    g.run(90)
    st, ref = g.st, g.st.ref
    assert g.limit == 24 and abs(ref - 56.0) < 0.5 and st.mode == "hold"
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


def test_a_visit_to_a_fast_map_costs_one_look_and_nothing_that_was_known_and_a_long_stay_is_the_game():
    """A map at 144 fps in a 40 fps game: over 2.5 times the usual rate is not judged. After 5 s it was taken for the
    game (all that was known dropped, the full limit for 20 s on the way back); its frames in the pace read as a game
    that gives more than the reference. More frames harm nothing: 30 s before it is the game, and the pace starts
    again from the game's frames when it is over. Back in the game the limit that held is held again, after one look
    at the full limit: the target is out of reach, and the place after a map may be another one with this frame rate
    (fuzz seed 298: 400 s at 24 fps with the target's 30 at the full limit)."""
    in_map = lambda t: 200 <= t < 208 or t >= 400
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120), fps=lambda w, t: 144.0 if in_map(t) else (40.0 if w >= 14 else 40.0 * w / 14))
    g.draw = lambda w, t: 9.0 if in_map(t) else min(float(w), 14.0)
    g.run(200)
    held, ref, fail = g.limit, g.st.ref, dict(g.st.fail)
    assert held <= 15
    g.run(30)                                                        # 8 s in the map and back
    assert g.since(200) in ([], [30, held]) and g.st.ref == pytest.approx(ref, rel=0.02) and set(fail) <= set(g.st.fail)
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
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120), fps=lambda w: 20.0 if w >= 20 else 20.0 * w / 20)
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
    assert max(g.since(t0) + [g.limit]) <= g.cap


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


@pytest.mark.parametrize("target", [120, 60])
@pytest.mark.parametrize("fall, answered", [(0.005, False), (0.02, True), (0.08, True)])
def test_a_fall_that_stays_inside_the_kept_margin_is_no_loss(target, fall, answered):
    """56 fps at the full limit and at the limit that is held. The game's own frame rate then falls at every limit (the
    time of day in Enshrouded). By 0.5 %: over the goal of when the limit was judged (1 % under what the full limit
    gave), so nothing is written. By 2 %: the full limit is asked, since a lower limit keeps no watts that cost frames
    (D11). By 8 %: the full limit at once."""
    own = {"x": 1.0}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30),
             fps=lambda w: own["x"] * (56.0 if w >= 24 else 56.0 - 4 * (24 - w)))
    g.draw = lambda w, t: float(w)
    g.run(120)
    t0, own["x"] = g.t, 1 - fall
    assert g.limit == 24
    g.run(60)
    assert (30 in g.since(t0)) == answered, (g.since(t0), [n for n in g.notes if n[0] >= t0])


@pytest.mark.parametrize("by, slope", [(0.06, 0.02), (0.08, 0.03), (0.10, 0.04)])
def test_a_scene_that_got_lighter_under_a_step_is_found_at_the_next_look_by_the_clock(by, slope):
    """50 fps at the full 30 W and 2 to 4 % less for every watt under it. During the first step down the scene gets 6
    to 10 % lighter: the step shows about as many frames as before it, so its cost goes into the ratio as none, and
    the steps after it hold on that (5.9 to 7.8 % under what the full limit gives, with no frame to say so). Ten
    minutes after the full limit was last measured it is asked again."""
    light = {"on": False}

    def fps(w):
        light["on"] = light["on"] or w < 30
        return (50.0 * (1 + by) if light["on"] else 50.0) * (1 - slope) ** (30 - w)
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=fps)
    g.draw = lambda w, t: float(w)
    g.run(500)
    assert fps(g.limit) < fps(30) * (1 - gov.SUSTAINED)     # (the case: held where the margin is not kept)
    g.run(400)
    assert fps(g.limit) >= fps(30) * (1 - gov.SUSTAINED) and [n for t, n in g.notes if "min since the full" in n]



def test_the_look_at_a_games_start_does_not_say_a_menu_came_before_it():
    """No target (120) and a game on a frame limit of 60 that draws 9 W: the steps down need no look,
    and at 8 W the package draws the limit. What the full limit gives was never measured, and it is asked once. No
    menu came before."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120), fps=lambda w: 60.0)
    g.draw = lambda w, t: min(float(w), 9.0)
    g.run(60)
    looks = [n for t, n in g.notes if "checking" in n]
    assert looks and not [n for n in looks if "menu" in n], g.notes[:8]


@pytest.mark.parametrize("early", [1.0, 0.5])
def test_the_look_by_the_clock_waits_while_the_package_draws_less_than_the_limit(early):
    """Out of reach (120), held at 28 W. `early` s before the ten minutes are up the package draws 3 W under the limit
    (a lighter moment, a menu coming): the limit can't hold the game back there, so the clock's look waits."""
    under = {"on": False}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w: 50.0 * 0.97 ** max(0, 28 - w))
    g.draw = lambda w, t: float(w) - (3.0 if under["on"] else 0.0)
    g.run(300)
    g.run(g.st.full_at + gov.CHECK_MAX_S - early - g.t)
    assert g.limit == 28
    under["on"] = True
    t0 = g.t
    g.run(60)
    assert g.since(t0) == [], [n for n in g.notes if n[0] >= t0]


def test_a_loss_right_after_the_clocks_look_is_answered_as_any_other():
    """Out of reach (120), held at 28 W; the ten-minute look says 28 W costs what it did. Right after it the scene gets
    10 % heavier at every limit: a loss at 28 W, the full limit, and back, since the full limit gives no more.
    (Only an answer to a loss counts for the rule that keeps the full limit at a second loss within AFTER_LOSS_S.)"""
    heavy = {"on": False}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30),
             fps=lambda w: 50.0 * 0.97 ** max(0, 28 - w) * (0.9 if heavy["on"] else 1.0))
    g.draw = lambda w, t: float(w)
    g.run(300)
    g.run(g.st.full_at + gov.CHECK_MAX_S - g.t)
    while not [n for t, n in g.notes if t >= 600 and "back to 28 W" in n]:
        g.run(0.1)
    assert [n for t, n in g.notes if "min since the full" in n] and g.limit == 28, g.notes[-3:]
    heavy["on"] = True
    t0 = g.t
    g.run(30)
    assert g.since(t0) == [30, 28], [n for n in g.notes if n[0] >= t0]       # (not 30, 29, 28)


def test_a_pace_the_full_limit_did_not_beat_is_another_places_after_half_a_minute():
    """Held at 14 W in a place that gives 50 fps. A lighter place for 15 s (60 fps, the full limit gives no more),
    back for five minutes, then a place where 14 W gives 58 fps and the full limit 75: the 60 fps of minutes before
    say nothing of it."""
    places = {"1": lambda w: 50.0 * min(1.0, w / 14), "2": lambda w: 60.0 * min(1.0, w / 14), "3": lambda w: 75.0 * min(1.0, w / 18.1)}
    at = {"x": "1"}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=16), fps=lambda w: places[at["x"]](w))
    g.draw = lambda w, t: float(w)
    g.run(120)
    at["x"] = "2"
    g.run(15)
    at["x"] = "1"
    g.run(300)
    at["x"], t0 = "3", g.t
    g.run(60)
    assert g.since(t0)[0] == 30 and places["3"](g.limit) >= 75 * 0.95, (g.since(t0), [n for n in g.notes if n[0] >= t0])



@pytest.mark.parametrize("path, video_w, video_s", [
    ("a step down", 6.1, 20.0), ("a step down", 7.4, 60.0), ("a look in the video", 6.1, 60.0),
    ("a look in the video", 7.4, 60.0), ("a failed step in the video", 7.1, 60.0), ("a failed step in the video", 8.4, 60.0)])
def test_a_game_after_an_intro_video_that_drew_under_the_limit_is_not_held_at_the_videos_frame_rate(path, video_w, video_s):
    """A 30 fps intro video with the package under the limit: a step goes to a level where it draws 2 W under (the band
    is free), and the governor ends at a lower one where it draws 0.6 to 1.9 W under: by a step down, back after a look
    for a dip in the video, or back after a failed step. The game then gives 30 fps there and 60 fps at twice the
    watts, with the package at the limit: a fall, at most 30 s at a limit under 57 fps. The free mark did not go with
    the step, the way back or the band set aside after the look: the game stayed at the video's frame rate until the
    full limit's 10-minute look (553 s under 57 fps)."""
    held = 9 if path == "a failed step in the video" else 8

    def fps(w, t):
        if t >= video_s:
            return min(60.0, 60.0 * w / (2 * held))
        if path == "a look in the video" and 30.0 <= t < 33.0:
            return 25.0
        return 27.0 if path == "a failed step in the video" and w < held else 30.0
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60.0, start_w=None), fps=fps)
    g.draw = lambda w, t: min(float(w), video_w) if t < video_s else float(w)
    g.run(video_s + 300)
    writes, under, t = sorted(g.writes), 0.0, video_s
    while t < g.t:
        under += 0.05 if fps([w for at, w in writes if at <= t][-1], t) < 57.0 else 0.0
        t += 0.05
    assert under < 30.0, (round(under, 1), writes)



@pytest.mark.parametrize("video_w", [6.5, 7.0])
@pytest.mark.parametrize("game_fps", [10.0, 20.0])
def test_a_clear_fall_at_a_band_taken_with_the_package_under_the_limit_gets_the_full_limit_as_soon_as_elsewhere(video_w,
                                                                                                                  game_fps):
    """A 30 fps intro video with the package under the limit: the steps end at the 8 W floor with the band free. The
    game then gives 10 or 20 fps at 8 W with the package at the limit: a clear fall. The full limit comes no later
    than after the same video with the package at 8 W (no free band). The drop of the free band ran first and the fall
    waited for the frames after it (0.7 s, against 0.4 s without the mark: cloud fuzz seed 204)."""
    def full_after(drawn):
        fps = lambda w, t: 30.0 if t < 60.0 else min(60.0, game_fps * w / 8.0)
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60.0, start_w=None), fps=fps)
        g.draw = lambda w, t: min(float(w), drawn) if t < 60.0 else float(w)
        g.run(90)
        assert [w for t, w in sorted(g.writes) if t < 60.0][-1] == 8
        return min(t for t, w in g.writes if t >= 60.0 and w == 30) - 60.0
    assert full_after(video_w) <= full_after(8.0), (full_after(video_w), full_after(8.0))


@pytest.mark.parametrize("video_s", [20.0, 60.0])
@pytest.mark.parametrize("at_floor", [12.0, 20.0])
@pytest.mark.parametrize("drawn", [6.1, 7.0, 8.0])
def test_a_clear_fall_at_a_band_taken_with_the_package_under_the_limit_books_nothing_for_that_limit(video_s, at_floor,
                                                                                                   drawn):
    """A 30 fps intro video for 20 or 60 s: the steps end at the 8 W floor, with the package at 6.1 or 7 W (the band is
    free) or at 8 W. The game then gives 12 or 20 fps at 8 W and 27 at 30 W, with the package at the limit: a clear
    fall, and the full limit. Where the band was free, the game never held at 8 W: 8 W is not tried once more and not
    booked as failed (with it booked, the first step down from 30 W went to 19 W, and the steps after it held 29 W
    where the game gave 26 of 27 fps: 2.5 % of the frames lost, cloud fuzz seed 127). Where the package drew the
    limit, the fall is judged as at any held limit: 8 W failed."""
    fps = lambda w, t: 30.0 if t < video_s else min(27.0, at_floor + (27.0 - at_floor) * (w - 8) / 20)
    g = Game(gov.Cfg(floor=8, ceiling=30, target=30.0, start_w=None), fps=fps)
    g.draw = lambda w, t: min(float(w), drawn) if t < video_s else float(w)
    g.run(video_s + 120)
    after = [w for t, w in sorted(g.writes) if t >= video_s]
    assert [w for t, w in sorted(g.writes) if t < video_s][-1] == 8 and after[0] == 30, g.writes
    assert (8 in after or 8 in g.st.fail) == (drawn == 8.0), (after, sorted(g.st.fail), g.notes)


def test_a_free_bands_level_that_the_game_after_a_video_failed_is_not_tried_again_at_once():
    """Target 30: a 30 fps video (the package at 6 W at every limit) until 100 s: the steps end at the 8 W floor, the
    band free. The game then gives 2 fps a watt up to 60 at 30 W, the package at the limit: 8 W was too low, not
    booked. 8 W was the level held in this scene (the pace counts at your target): the step down 5 s later went
    to 8 W again and lost again (1.5 s under 28.5 fps)."""
    video = lambda t: t < 100.0
    g = Game(gov.Cfg(floor=8, ceiling=30, target=30.0, start_w=30),
             fps=lambda w, t: 30.0 if video(t) else min(60.0, 2.0 * w))
    g.draw = lambda w, t: min(float(w), 6.0) if video(t) else float(w)
    g.run(160)
    after = [w for t, w in sorted(g.writes) if t >= 100.0]
    assert after[0] == 30 and 8 not in after, (after, [n for t, n in g.notes if 99.0 <= t <= 110.0])


@pytest.mark.parametrize("at_floor, drawn", [(27.0, 6.1), (27.0, 7.0), (30.0, 6.1), (30.0, 7.0), (27.0, 8.0)])
def test_a_game_at_the_videos_frame_rate_under_your_target_books_nothing_for_the_videos_limit(at_floor, drawn):
    """A 30 fps intro video: the steps end at the 8 W floor, with the package at 6.1 or 7 W (the band is free) or at
    8 W. The game then gives 27 or 30 fps at 8 W and 60 at 16 W, target 60, with the package at the limit. Where the
    band was free: the full limit at once, and 8 W is not tried again or booked as failed (at the video's 30 fps the
    band saw no fall, and the fall rule judged 8 W as a limit that held the game). Where the package drew the limit,
    the fall is judged as at any held limit."""
    fps = lambda w, t: 30.0 if t < 60.0 else min(60.0, at_floor * w / 8.0)
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60.0, start_w=None), fps=fps)
    g.draw = lambda w, t: min(float(w), drawn) if t < 60.0 else float(w)
    g.run(150)
    after = [w for t, w in sorted(g.writes) if t >= 60.0]
    assert [w for t, w in sorted(g.writes) if t < 60.0][-1] == 8 and after[0] == 30, g.writes
    assert (8 in after or 8 in g.st.fail) == (drawn == 8.0), (after, sorted(g.st.fail), g.notes)


@pytest.mark.parametrize("every", [40.0, 60.0, 90.0])
@pytest.mark.parametrize("dip_s", [0.6, 1.0])
def test_a_games_own_short_dips_at_a_free_band_get_one_look_and_are_learned(dip_s, every):
    """A game on its 60 fps frame limit with the package at 6 W: the band at the 8 W floor is free. Every 40, 60 or 90 s
    it dips by itself for 0.6 or 1 s to 40 fps at every limit, with the package at the limit. A dip gets the full limit
    and its verdict: 8 W once more, which holds, and the dip is booked. Once the game counts as one that dips, a dip
    gets no write (the full limit and 10 s held at it for each dip: 90 to 210 s over 8 W in 840 s)."""
    dip = lambda t: t >= 60.0 and (t % every) < dip_s
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60.0, start_w=None), fps=lambda w, t: 40.0 if dip(t) else 60.0)
    g.draw = lambda w, t: float(w) if dip(t) else min(float(w), 6.0)
    g.run(900)
    writes = sorted(g.writes) + [(g.t, None)]
    over = sum(b - max(a, 60.0) for (a, w), (b, _) in zip(writes, writes[1:]) if w > 8 and b > 60.0)
    assert len(g.st.dips) >= 2 and over <= 60.0, (over, g.st.dips, g.notes)


@pytest.mark.parametrize("dip_s, free_w", [(0.6, 6.0), (0.6, 6.5), (1.0, 6.0), (1.0, 6.5), (1.5, 6.5), (2.0, 6.5)])
@pytest.mark.parametrize("every", [20.0, 30.0])
def test_a_games_own_dips_every_20_or_30_s_at_a_free_band_keep_the_floor(dip_s, every, free_w):
    """As above, with a dip every 20 or 30 s, of 0.6 to 2 s, and the package at 6 or 6.5 W out of the dips. A dip came
    before the level had held for HOME_S, or within RETRY_S of its last try: the level was given up, the full limit
    stayed, and the next dip caught its steps down (240 to 826 s over 8 W in 840 s). The full limit lost the frames
    too (they climbed at it) and the package draws less than all of the level: the level once more."""
    dip = lambda t: t >= 60.0 and (t % every) < dip_s
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60.0, start_w=None), fps=lambda w, t: 40.0 if dip(t) else 60.0)
    g.draw = lambda w, t: float(w) if dip(t) else min(float(w), free_w)
    g.run(900)
    writes = sorted(g.writes) + [(g.t, None)]
    over = sum(b - max(a, 60.0) for (a, w), (b, _) in zip(writes, writes[1:]) if w > 8 and b > 60.0)
    assert over <= 90.0, (over, g.notes[-12:])


@pytest.mark.parametrize("every, burst_s, need, base, own_dip", [
    (10.0, 1.0, 12.0, 7.0, False), (20.0, 1.0, 12.0, 7.0, False), (10.0, 0.5, 11.0, 7.4, False),
    (20.0, 1.5, 14.0, 6.0, False), (10.0, 1.0, 12.0, 7.0, True)])
def test_a_heavier_moment_that_the_full_limit_answers_at_once_is_the_limits(every, burst_s, need, base, own_dip):
    """A game on its 60 fps frame limit at 6 to 7.4 W, and every 10 or 20 s a burst of 0.5 to 1.5 s that needs 11 to
    14 W (45 fps under it). The full limit gives 60 fps from its first frames, and the burst is over before the
    verdict, the package under the level. Taken for a dip of the game's own, the level stayed: 40 to 82 s under 57 fps.
    Also after a dip of the game's own at 30 s, which the full limit had too: that look's mark ends with it."""
    inb = lambda t: t >= 60.0 and (t % every) < burst_s
    own = lambda t: own_dip and 30.0 <= t < 31.0
    fps = lambda w, t: 40.0 if own(t) else ((60.0 if w >= need else 45.0) if inb(t) else 60.0)
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60.0, start_w=None), fps=fps)
    g.draw = lambda w, t: float(w) if own(t) else (min(float(w), need) if inb(t) else min(float(w), base))
    g.run(900)
    writes = sorted(g.writes)
    at = lambda t: [w for a, w in writes if a <= t][-1]
    under = sum(0.05 for i in range(1200, 18000) if fps(at(i * 0.05), i * 0.05) < 57.0)
    assert under <= 3.0 and len(g.st.dips) <= 1 + own_dip, (under, g.st.dips, g.notes[-6:])


def test_slow_frames_that_the_full_limit_does_not_have_fail_the_level_and_book_no_dip():
    """A 30 fps lock held at 29 W of 30 W. From 100 s the place is lighter (30 fps from 27 W, the package at 27.5 W at
    most) with slow frames at every limit under 30 W (every 17th frame 15 % long). The look for slow frames finds none
    at the full limit: the level failed, the full limit stays. Not tried once more for 2.5 s, which cannot show a 6 %
    share, and not booked as the game's own dip (3 times in 100 s, the package under the level at the full limit)."""
    need = lambda t: 29.0 if t < 100.0 else 27.0
    g = Game(gov.Cfg(floor=8, ceiling=30, target=30.0, start_w=29), fps=lambda w, t: 30.0 * min(1.0, w / need(t)),
             shape=lambda i, w, t: 1.15 if (t >= 100.0 and w < 30 and i % 17 == 0) else 1.0)
    g.draw = lambda w, t: min(float(w), 29.0 if t < 100.0 else 27.5)
    g.run(100)
    assert g.limit == 29
    g.run(100)
    said = [n for t, n in g.notes if t >= 100.0]
    assert not [n for n in said if "once more" in n] and len(g.st.dips) == 0, (said[:6], list(g.st.dips))


def test_a_menu_that_opens_at_the_end_of_a_steps_watch_is_no_dip_of_the_games_own():
    """No target: 19 fps from 13 W, the package at the limit. A menu (72 fps at every limit, 9 W) opens 0.2 s before
    the end of the watch of the step 15 -> 13 W: more frames than before the step, a look, the menu's 72 fps at the
    full limit with the package under 13 W. Not 13 W once more and booked as the game's own dip."""
    m0 = 12.7
    menu = lambda t: m0 <= t < m0 + 40.0
    g = Game(gov.Cfg(floor=8, ceiling=25, target=120.0, start_w=None),
             fps=lambda w, t: 72.0 if menu(t) else 19.0 * min(1.0, w / 13.0))
    g.draw = lambda w, t: 9.0 if menu(t) else float(w)
    g.run(m0 + 10.0)
    assert len(g.st.dips) == 0, ([n for t, n in g.notes if t >= m0 - 0.5], list(g.st.dips))


def test_a_dip_that_the_full_limit_sees_gets_one_look():
    """Held at the 8 W floor with the band free (60 fps, the package at 6 W). A dip of the game's own for 2 s to 40 fps
    at every limit, with the package at the limit; the look that sees the loss reads 6.5 W (the mean of the last
    half second, mostly before the dip). The full limit gives 40 fps: back to 8 W, the dip still on. The band taken
    there kept the free mark, the package drew all of 8 W, and the dip got a second look in the same 2 s."""
    dip = lambda t: 60.0 <= t < 62.0
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60.0, start_w=None), fps=lambda w, t: 40.0 if dip(t) else 60.0)
    g.draw = lambda w, t: (6.5 if t < 60.5 else float(w)) if dip(t) else min(float(w), 6.0)
    g.run(59)
    assert (g.limit, g.st.free_w) == (8, 8)
    g.run(7)
    assert g.since(59.0) == [30, 8], (g.since(59.0), g.notes[-4:])


def test_the_guards_cap_at_a_free_band_starts_no_look():
    """Held at the 8 W floor with the band free (a 30 fps video, the package at 7 W). The guard caps at 7 W, and the
    package draws all of it: the band is dropped, and 30 fps is a clear fall against your target. The cap is the full
    limit there: no look starts and nothing is held."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60.0, start_w=None), fps=lambda w, t: 30.0)
    g.draw = lambda w, t: min(float(w), 7.0)
    g.run(80)
    assert (g.st.limit, g.st.free_w) == (8, 8)
    g.cap = 7
    g.run(3.0)
    said = [n for at, n in g.notes if at >= 80.0]
    assert said == ["the temperature guard allows 7 W"] and g.st.quiet_until < 80.0, (said, g.st.quiet_until)


@pytest.mark.parametrize("case, video_w", [("a light place", 7.0), ("the video slows", 6.5), ("the video slows", 7.0),
                                           ("the video slows", 7.4)])
def test_a_looks_band_taken_under_a_free_limit_says_nothing_of_a_place_that_draws_the_limit(case, video_w):
    """Held at the 8 W floor with the band free (a 30 fps video, the package under the limit). Then a look whose verdict
    takes the band of another place at 8 W, with the package still under the limit: a light place at 40 fps, or the
    video at 25 fps. Then the game, which the limit holds back with the package at the limit: a fall, at most 30 s
    under 57 fps (the look's band without the free mark held it until the 10-minute look: 561 s under)."""
    start = 100.0 if case == "a light place" else 60.0

    def fps(w, t):
        if t >= start:
            return min(60.0, (40.0 if case == "a light place" else 25.0) * w / 8.0)
        return 30.0 if t < (60.0 if case == "a light place" else 40.0) else 40.0 if case == "a light place" else 25.0
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60.0, start_w=None), fps=fps)
    g.draw = lambda w, t: min(float(w), video_w) if t < start else float(w)
    g.run(start + 600)
    writes, under, t = sorted(g.writes), 0.0, start
    while t < g.t:
        under += 0.05 if fps([w for at, w in writes if at <= t][-1], t) < 57.0 else 0.0
        t += 0.05
    assert under < 30.0, (round(under, 1), writes)


def test_a_videos_band_says_nothing_of_a_place_that_has_its_frame_rate_and_draws_the_limit():
    """Held at 20 W. A video (30 fps, the package at 8 W), a loading screen, a light place for 20 s (75 fps at 10 W),
    then a heavy one: 30 fps at 20 W with the package at the limit, 45 at the full 30 W. The frame rate is the one the
    video had at this limit, and the video's band was set aside 20 s before: but that band was taken with the package
    under the limit. Nothing is known for the heavy place: a fall, and the full limit is written at once (the band
    came back as if the limit had been judged there: 480 s at 29 fps with 45 at the full limit, fuzz seed 60)."""
    place = {"fps": lambda w: 60.0 * min(1.0, w / 20), "draw": lambda w: float(w)}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w: place["fps"](w))
    g.draw = lambda w, t: place["draw"](w)
    g.run(100)
    held = g.limit
    assert held in (19, 20)
    place.update(fps=lambda w: 30.0, draw=lambda w: 8.0)
    g.run(20)
    g.run(2, frames=False)
    place.update(fps=lambda w: 75.0, draw=lambda w: 10.0)
    g.run(20)
    assert g.limit == held and g.st.was is not None and g.st.was[0] == pytest.approx(30.0, rel=0.01)
    place.update(fps=lambda w: min(45.0, 30.0 * w / held), draw=lambda w: float(w))
    t0 = g.t
    g.run(60)
    said = [n for t, n in g.notes if t >= t0]
    assert g.since(t0)[0] == 30 and "under" in said[0] and g.notes[len(g.notes) - len(said)][0] - t0 <= 2, said   # a fall
    assert place["fps"](g.limit) >= 45 * 0.95


def test_the_full_limit_is_not_asked_where_the_package_does_not_draw_the_limit():
    """A video at 30 fps that draws 6 W, target 40: at the 8 W floor the package is 2 W under the limit, give or take
    0.1 W. Nothing is asked of the full limit there. It was, and its answer (30 fps at every limit) stood for the
    game after the video: 28.8 fps at 8 W for 63 s with 40 in reach (fuzz seed 251). The game draws all of the limit:
    the full limit at once."""
    place = {"fps": lambda w: 30.0, "draw": lambda w, t: 6.0 + 0.1 * (int(t * 2) % 2)}
    g = Game(gov.Cfg(floor=8, ceiling=25, target=40, start_w=17), fps=lambda w: place["fps"](w))
    g.draw = lambda w, t: place["draw"](w, t)
    g.run(80)
    assert g.limit == 8 and 25 not in g.since(0), g.notes
    place.update(fps=lambda w: min(68.0, 28.76 + 3.34 * (w - 8)), draw=lambda w, t: float(w))
    t0 = g.t
    g.run(30)
    first = [(t, w) for t, w in g.writes if t >= t0][0]
    assert first[1] == 25 and first[0] - t0 <= 3 and place["fps"](g.limit) >= 38, (g.since(t0), [n for n in g.notes if n[0] >= t0])



@pytest.mark.parametrize("at", [18, 16])
def test_one_look_under_the_limit_in_a_steps_watch_does_not_make_its_band_free(at):
    """Target 60 out of reach: 48 fps from 14 W, 30 under it; the package draws the limit. One look of the watch of
    the step to `at` W has the package 2.5 W under the limit: the step's band was marked free for good, and the full
    limit was asked once more on the way down. The same steps as without that look."""
    def run(dip):
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=lambda w: 48.0 if w >= 14 else 30.0)
        used = []

        def draw(limit, t):
            if dip and not used and g.st.mode == "probe" and limit == at and t - g.st.wrote_at > 1.0:
                used.append(t)
                return limit - 2.5
            return float(limit)
        g.draw = draw
        g.run(60)
        return [w for _, w in g.writes]
    assert run(True) == run(False)


@pytest.mark.parametrize("target, reach", [(30, False), (22, True)])
def test_after_a_menu_the_full_limit_is_asked_once_when_the_target_is_out_of_reach(target, reach):
    """A place where the full limit gives 23.5 fps, held some watts under it. A menu for 5 s (72 fps, the package at
    9 W: no measurement of the full limit), then another place: the same frame rate at the held limit, and more at
    the full one. No frame says so (the band the limit had before the menu is back). With target 30, out of reach
    in the first place, the full limit is asked once after the menu and the target is held (it was not asked: 400 s
    at 24 fps, fuzz seed 298). With target 22, which the held limit gives, nothing is asked."""
    first = lambda w: 23.5 * min(1.0, w / 18)
    place = {"fps": first, "draw": lambda w: float(w)}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30), fps=lambda w: place["fps"](w))
    g.draw = lambda w, t: place["draw"](w)
    g.run(120)
    held, pace, measured = g.limit, first(g.limit), g.st.full_at
    assert held <= 19
    place.update(fps=lambda w: 72.0, draw=lambda w: 9.0)
    g.run(5)
    assert g.st.full_at == measured
    place.update(fps=lambda w: min(30.0, pace * w / held), draw=lambda w: float(w))
    t0 = g.t
    g.run(60)
    asked = [t for t, n in g.notes if t >= t0 and "before a menu" in n]
    if reach:
        assert not asked and 30 not in g.since(t0), [n for n in g.notes if n[0] >= t0]
    else:
        assert len(asked) == 1 and asked[0] - t0 <= 10 and place["fps"](g.limit) >= 28.5, [n for n in g.notes if n[0] >= t0]


def test_after_a_pause_the_pace_is_of_the_frames_after_it():
    """30 fps held at 14 W, six seconds without frames, then a lighter place: 60 fps from 14 W up, and its first frames
    come 0.4 s into a look. The pace judged is that of the new place (with frames of both it read 43 fps: "43 fps at
    14 W, 60 at 30 W", and a dip of the game's own was booked that never was)."""
    place = {"x": lambda w: 30.0 * min(1.0, w / 14)}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=16), fps=lambda w: place["x"](w))
    g.draw = lambda w, t: float(w)
    g.run(90)
    assert g.limit == 14
    g.run(6, frames=False)
    place["x"] = lambda w: 60.0 * min(1.0, w / 14)
    g.end, t0, dips = g.t + 0.4, g.t, len(g.st.dips)
    g.run(40)
    said = [n for t, n in g.notes if t >= t0]
    assert said[0].startswith("60.0 fps at 14 W") and "the scene, not the limit" in said[1], said
    assert len(g.st.dips) == dips and 14 not in g.st.fail and g.limit <= 14


@pytest.mark.parametrize("pause", ["frames stop", "watts back"])
def test_under_the_guards_clock_cap_a_pause_and_watts_given_back_forget_nothing(pause):
    """Target 60 out of reach (40 fps from 12 W up), the guard's clock cap on. Eight seconds without frames, or the
    guard's power cap going from 20 W to 24 W to none: what is known stays known, and the next write is a step down
    (it was the full limit, once for each: a pace under the target with nothing known is a fall)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 40.0 if w >= 12 else 30.0)
    g.draw = lambda w, t: float(w)
    g.cap = 20
    g.run(120)
    assert g.limit == 12
    g.thermal, t0 = True, g.t
    if pause == "frames stop":
        g.run(8, frames=False)
        g.run(15)
    else:
        g.cap = 24
        g.run(10)
        g.cap = None
        g.run(10)
    assert g.since(t0)[:1] in ([], [11]), (g.since(t0), [n for n in g.notes if n[0] >= t0])


def test_a_pause_at_a_limit_the_package_does_not_fill_writes_nothing():
    """Target 60, a game that gives 30 fps at every limit and draws 6 W, held at the floor. After eight seconds without
    frames nothing is known at the limit, and the pace is under the target: no fall, the limit holds nothing back
    (the full limit for a second after every pause and loading screen)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 30.0)
    g.draw = lambda w, t: 6.0
    g.run(120)
    assert g.limit == 8
    g.run(8, frames=False)
    t0 = g.t
    g.run(15)
    assert g.since(t0) == [], [n for n in g.notes if n[0] >= t0]


@pytest.mark.parametrize("knee, first", [(19.0, 150), (19.3, 160), (20.0, 160), (20.3, 150), (20.6, 150)])
@pytest.mark.parametrize("period, rises", [(20, 1), (25, 1), (40, 4)])
def test_a_game_that_goes_between_two_frame_rates_is_asked_once_what_the_faster_one_costs(knee, first, period, rises):
    """56 fps, then 60 for a while, and so on for five minutes (no target set, the package at the limit). The first
    time at 60 fps the full limit says that it gives no more; after that 60 fps at this limit is no news for half a
    minute after the pace was last there (four looks in half a minute of Enshrouded: the band of the faster place was
    dropped at each return to the slower one). The last pace the full limit did not beat counts; away from it for
    longer, the pace may be another place's and is asked again. (With the knee over 19 W the
    second try of 19 W is at 150 s: the first rise is 10 s after it.) The slower place is asked once too."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30),
             fps=lambda w, t: (60.0 if t >= first and ((t - first) // period) % 2 == 0 else 56.0) * min(1.0, w / knee))
    g.draw = lambda w, t: float(w)
    g.run(first + 300)
    asked = [n for t, n in g.notes if t >= first and "checking what the full" in n]
    assert 1 <= len(asked) <= rises, [n for n in g.notes if n[0] >= first]
    falls = [n for t, n in g.notes if t >= first and "the scene, not the limit" in n]
    assert len(falls) <= rises, [n for n in g.notes if n[0] >= first]


def test_a_scene_that_gets_lighter_under_a_step_has_the_full_limit_say_what_the_step_costs():
    """50 fps at the full 30 W, 2 % less for every watt under it. During the first step down the scene gets 10 %
    lighter: 28 W gives more frames than 30 W gave. A lower limit gives no more frames, so the step's frames say
    nothing of its cost: taken as "costs nothing", the next steps were judged against too few frames and the limit held
    8 % under the full one. The full limit is looked at, and stays: each watt under it costs 2 %."""
    light = {"on": False}

    def fps(w):
        light["on"] = light["on"] or w < 30
        return (55.0 if light["on"] else 50.0) * 0.98 ** (30 - w)
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=fps)
    g.draw = lambda w, t: float(w)
    g.run(120)
    assert g.limit == 30 and [w for _, w in g.writes][:2] == [28, 30], (g.limit, g.writes, g.notes[:6])


def test_steps_down_end_without_a_look_at_the_full_limit():
    """Steps down from the full limit to the level that holds: each is judged against the frames before it, and the
    full limit is not written to see what they cost."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w: 56.0 if w >= 24 else 56.0 - (24 - w))
    g.draw = lambda w, t: float(w)
    g.run(60)
    steps = [t for t, n in g.notes if "trying" in n]
    assert steps and g.limit == 24 and 30 not in g.since(0), (g.limit, g.notes[:8])


def test_more_frames_at_a_held_limit_than_when_the_reference_was_taken_has_the_full_limit_looked_at():
    """A lighter scene: this limit gives 20 % more than it did. The full limit gives more too, and the levels that
    failed in the heavier scene say nothing here. (6 % more: the full limit is looked at as well, and what failed
    still counts. A game whose own frame rate moves that much for seconds is one scene.)"""
    lift = {"x": 1.0}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120), fps=lambda w, t: lift["x"] * (60.0 if w >= 26 else 60.0 - (26 - w)))
    g.draw = lambda w, t: float(w)
    g.run(40)
    assert g.st.mode == "hold" and g.limit == 26 and g.st.fail
    g.run(20)
    lift["x"], t0, ref, failed = 1.07, g.t, g.st.ref, dict(g.st.fail)
    g.run(15)
    assert any("checking what the full" in n for t, n in g.notes if t >= t0)
    old = [w for w in failed if w in g.st.fail and g.st.fail[w][4] < t0]         # (not failed again since)
    assert g.st.ref > ref * 1.05 and old and all(gov._counts(g.st.fail[w], g.st.ref, g.t) for w in old)
    lift["x"], t1 = 1.07 * 1.2, g.t
    g.run(15)
    old = [w for w in old if w in g.st.fail and g.st.fail[w][4] < t1]
    assert g.st.ref > ref * 1.2 and not any(gov._counts(g.st.fail[w], g.st.ref, g.t) for w in old)


def test_no_look_at_the_full_limit_while_the_package_is_well_under_the_limit():
    """A limit the package doesn't use holds nothing back: what it shows is the reference, and a look at the full
    limit would only cost watts."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120))
    g.draw = 5.0
    g.run(60)
    assert g.limit == 8 and 30 not in g.since(0)
    t0 = g.t
    g.run(660)
    assert g.since(t0) == []


def test_looks_that_are_free_do_not_add_up_across_looks_that_are_not():
    """The reference is the median of FREE_LOOKS looks in a row. Four of them in a lighter moment, then looks in which
    the limit holds the game back, then one more: not five."""
    cfg = gov.Cfg(floor=8, ceiling=30, target=120)
    g = Game(cfg, fps=lambda w: 60.0 if w >= 26 else 60.0 - (26 - w))
    g.draw = lambda w, t: float(w)
    g.run(40)
    st, ref, t = g.st, g.st.ref, g.t
    st.quiet_until = t + 120                                         # no step of its own meanwhile

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
    assert g.st.noise > 0.03 and not any(w >= 25 for w in late)      # no "all the watts at once" once the wobble is known


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


def test_a_step_of_two_watts_is_watched_from_its_write():
    """The package draws the 2 W of a step for a second more: no more than BIND_W over the new limit, so the watch
    does not wait for it and the next step follows after DWELL_S."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=lambda w: 60.0 if w >= 20 else 50.0)
    g.draw = lambda w, t: float(w + 2) if g.writes and t - g.writes[-1][0] < 1.0 else float(w)
    g.run(12)
    assert [w for _, w in g.writes][:2] == [28, 26]
    assert g.writes[1][0] - g.writes[0][0] <= gov.DWELL_S + 0.2, g.writes[:3]


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


@pytest.mark.parametrize("target", [120, 60])
@pytest.mark.parametrize("begins", [1.7, 2.0, 2.3])
def test_in_a_game_that_dips_a_step_does_not_hold_while_a_loss_is_waited_out(begins, target):
    """A game that dips, and a loss of 9 % that begins in the last second of a step's watch and stays. The watch does
    not end as "holds" on the typical frame while the newest frames are under the goal: the step fails when the loss
    has stayed a second, and the note names the frame rate that is under. Taken as held, the level was left by the
    rules of holding: three writes and a second try, the loss a second longer."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30), fps=lambda w: 60.0)
    g.st.dips.extend([0.0, 0.0])
    while not g.writes:
        g.run(0.1)
    t1, tried = g.writes[0]
    g.fps = lambda w, t: 60.0 * (0.91 if w < 30 and t >= t1 + begins else 1.0)
    g.run(8)
    assert g.writes[1:] == [(pytest.approx(t1 + begins + gov.PATIENT_S, abs=0.4), 30)] and tried in g.st.fail, g.writes
    under = {60: "57.0", 120: "59.4"}[target]
    assert next(n for _, n in g.notes if "back to 30 W" in n).startswith(f"{tried} W: 54.6 fps, under {under}"), g.notes


def test_a_long_loss_at_the_full_limit_is_a_heavier_scene_not_a_dip():
    """A dip is over within DIP_MAX_S. Ten seconds of fewer frames at the full limit are a place in the game: counted
    as dips, two of them made the governor wait a second before every answer from then on."""
    slow = [(20.0, 30.0), (50.0, 60.0)]
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30),
             fps=lambda w, t: (60.0 if w >= 30 else 50.0) * (0.8 if any(a <= t < b for a, b in slow) else 1.0))
    g.run(70)
    assert len(g.st.dips) == 0
    slow += [(80.0, 80.6), (95.0, 95.6)]                             # two short ones are
    g.run(40)
    assert len(g.st.dips) == 2


def test_the_thresholds_follow_the_games_wobble_up_to_four_times_and_no_further():
    """A game whose rate of ten frames wobbles by 20 %: 2.5 times that would be "no loss under 50 %". A loss of 16 %
    of the goal is still a clear one (the full limit at once), one of 5 % that stays is still answered (the full
    limit when the pace is of this limit's frames)."""
    def after(fps):
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=HOLDS_FROM_14).run(40)
        t0, held = g.t, g.limit
        g.st.noises.extend([0.2] * g.st.noises.maxlen)               # as learned from a wild game
        g.fps = lambda w: 60.0 if w >= 20 else fps
        g.run(6)
        return held, [(round(t - t0, 2), w) for t, w in g.writes if t >= t0]
    held, writes = after(48.0)                                       # 16 % under the 57 fps goal
    assert writes[0][1] == 30 and writes[0][0] <= 0.6, writes
    held, writes = after(54.0)                                       # 5 % under it
    assert writes and writes[0][1] == 30 and writes[0][0] <= gov.DWELL_S + 1.5, writes
    held, writes = after(55.6)                                       # 2.5 % under it: inside this game's wobble
    assert not [w for t, w in writes if t <= 3.0], writes


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


def test_steps_down_to_the_floor_get_no_look_at_the_full_limit():
    """Steps down that end at the floor without a failure (three of them, and eleven): no write but the steps, and
    after every CHAIN of them the rules of holding run once."""
    for ceiling, runs in ((14, 1), (30, 4)):
        g = Game(gov.Cfg(floor=8, ceiling=ceiling, target=120, start_w=ceiling), fps=lambda w: 56.0)
        g.draw = lambda w, t: float(w)
        g.run(90)
        ends = [t for t, n in g.notes if n.startswith("holding at")]
        down = [w for _, w in g.writes]
        assert g.limit == 8 and len(ends) == runs and down == sorted(down, reverse=True), (ceiling, g.notes)


@pytest.mark.parametrize("pause, looks", [(3, 0), (8, 1), (700, 1)])
def test_after_a_pause_or_a_loading_screen_the_full_limit_is_looked_at_once_and_what_held_is_kept(pause, looks):
    """5 s or more without frames may be a loading screen, and the place after it another one that has the same frame
    rate at this limit: one look at the full limit. In the same place (a pause menu) the limit that held is held again
    and what failed is still known. A stall of 3 s is no loading screen."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=16), fps=HOLDS_FROM_14).run(90)
    assert g.limit == 14
    fail = set(g.st.fail)
    g.run(pause, frames=False)
    back = g.t
    g.run(12)
    assert g.since(back).count(30) == looks and g.limit == 14 and fail <= set(g.st.fail), (g.since(back), g.notes[-3:])


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


def test_back_from_a_menu_the_game_is_judged_on_frames_of_the_game_alone():
    """A 20 fps game (target 60, out of reach) held at 14 W; 4 s of a 60 fps menu. The first look after the menu has
    frames of both. For every moment in a look at which the menu can end: no write (the band the limit had before the
    menu is known again)."""
    for frame in range(18, 30):
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 20.0 if w >= 14 else 20.0 * w / 14)
        menu = 60.0 + frame / 60.0
        g.shape = lambda i, w, at: 1 / 3 if menu <= at < menu + 4.0 else 1.0
        g.run(60)
        held = g.limit
        g.run(30)
        assert g.since(60) == [] and g.limit == held, (frame, g.since(60), [n for n in g.notes if n[0] >= 60])


def test_steam_menu_over_a_30fps_game_moves_nothing():
    """R7: the Steam menu over a game for 30 s, then back: no step taken from the menu's frames. Here the menu shows
    60 fps over a game locked at 30 (twice the game's rate: it is judged as the game)."""
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
    print("held", held, "notes:", g.notes[n:n + 8], "dips", len(g.st.dips), "home", g.st.home, "limit", g.limit)
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


def test_a_locked_game_under_a_target_it_cannot_reach_is_near_its_limit_within_a_minute():
    """Midnight Club: Los Angeles parked on RPCS3's 30 fps lock, target 60: at or under 17 W within 60 s (the lock
    is no cutscene of a game that holds 60, and the start level is no floor)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=lambda w: 30.0 if w >= 14 else 2.08 * w)
    g.draw = lambda w, t: float(w)                       # RPCS3 draws the whole limit
    g.run(60)
    assert g.limit <= 17


def test_a_locked_game_stays_near_its_limit_without_a_package_power_reading():
    """The same with no RAPL energy reading, 15 minutes on."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=lambda w: 30.0 if w >= 14 else 2.08 * w)
    g.run(900)
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
    reset by a scene change), so the edge rule reads the new place as the limit's edge: +1 W every 10 s. Two watts,
    then the full limit says whose they are, and the limit is back where it held within five minutes."""
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
    looks = [w for _, w in ups if w == 30]                   # two watts, then the full limit says whose they are
    assert len(looks) <= 1 and max([w for _, w in ups if w != 30] + [held]) <= held + gov.STEP_W, ups
    g.run(300)
    assert g.limit == held and [w for _, w in g.writes[n:]].count(30) == 1, g.writes[n:]


@pytest.mark.parametrize("every", [17, 8])
@pytest.mark.parametrize("target", [30, 120])
@pytest.mark.parametrize("draws_the_limit", [True, False])
def test_under_the_guards_clock_cap_the_games_own_slow_frames_get_no_look_until_the_cap_goes(every, target, draws_the_limit):
    """Slow frames get no watt and no look while the guard caps the CPU's clock: nothing walks the limit up in the
    minutes in which the chip is hot. When the cap goes: two watts at most, then one look."""
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
    assert ups.count(30) == 1 and max([w for w in ups if w != 30] + [held]) <= held + gov.STEP_W, ups


@pytest.mark.parametrize("cut", ["the guard's cap", "no frames", "Autotune ends the watch"])
def test_a_look_for_slow_frames_that_is_cut_short_says_nothing_in_the_next_look(cut):
    """Slow frames under 23 W, the limit's: after two watts, the look for whose they are. The look is cut short and
    the slow frames end. The next look at the full limit (here: for more frames) was judged with the share of slow
    frames the cut one had left: "30 W has none of the slow frames", a limit that holds booked as failed, and the full
    limit kept."""
    rough = {"on": True}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=16),
             shape=lambda i, w, t: (1.3, 0.7)[i % 12] if rough["on"] and w < 23 and i % 12 < 2 and t > 20 else 1.0)
    g.st.base_slow = 0.0
    while not (g.st.mode == "check" and g.st.why == "slow"):
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
    rough["on"], t0, n = False, g.t, len(g.notes)
    g.run(12)
    g.fps = lambda w: 66.0                              # more frames: the full limit is looked at
    g.run(18)
    assert [w for w in g.since(t0) if w == 30] and not [x for _, x in g.notes[n:] if "slow frames" in x], g.notes[n:]
    assert held not in g.st.fail or g.st.fail[held][0] < t0


@pytest.mark.parametrize("ratio", [1.3, 2.0, 4.0])
@pytest.mark.parametrize("target", [60, 120])
@pytest.mark.parametrize("draws_the_limit", [True, False])
def test_a_menu_that_opens_at_any_moment_of_a_look_never_costs_the_game_its_limit(ratio, target, draws_the_limit):
    """A menu over a game on a 30 fps lock, opened at any moment of the governor's half second, for 20 s. A look that
    fires on a pace that is part menu compares the game's frames at the held limit with the menu's at the full limit:
    the held limit was booked as failed and the full limit kept (47 s at 30 W for a need of 14 W). At most one look
    in the menu, the held limit is not booked, and the game has its limit back."""
    for k in range(0, 30, 2):
        opens = 120.0 + k / 60.0
        g = Game(gov.Cfg(floor=8, ceiling=30, target=target), fps=lambda w: 30.0 if w >= 14 else 2.08 * w,
                 shape=lambda i, w, at: 1.0 / ratio if opens <= at < opens + 20.0 else 1.0)
        g.draw = (lambda w, t: float(w)) if draws_the_limit else None
        g.run(119.5)
        held, n = g.limit, len(g.writes)
        g.run(20.5)
        case = (k, g.writes[n:], [x for x in g.notes if x[0] >= 119.5])
        assert [w for _, w in g.writes[n:]] in ([], [30, held]), case
        g.run(20)
        assert g.limit == held and held not in g.st.fail and [w for _, w in g.writes[n:]].count(30) <= 2, case
        assert all(w >= held for _, w in g.writes[n:]), case


def test_a_lighter_scene_that_frees_the_limit_gets_no_look_at_the_full_limit():
    """Most frames, a game that draws the whole limit. A minute after the last look at the full limit the scene gets a
    little lighter: 4 % more frames and the package 3 W under the limit. The limit holds nothing back now (the pace is
    the reference within a few looks): no look at the full limit. Without `binds` in the rule, the "moved 3 %, a
    minute since the last look" clause fires in the looks before the free reference is taken."""
    lift = {"x": 1.0, "free": False}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120), fps=lambda w, t: lift["x"] * (60.0 if w >= 26 else 60.0 - (26 - w)))
    g.draw = lambda w, t: float(w) - (3.0 if lift["free"] else 0.0)
    g.run(100)
    assert g.st.mode == "hold" and g.limit == 26
    lift["x"], lift["free"], t0 = 1.04, True, g.t
    g.run(20)
    looks = [(t, n) for t, n in g.notes if t >= t0 and "checking what the full" in n]
    assert looks == [], looks


@pytest.mark.parametrize("title_s", [0.0, 1.5, 3.0, 6.0, 10.0, 25.0])
def test_a_title_screen_at_the_target_costs_a_locked_game_the_wait_of_a_new_scene_and_no_more(title_s):
    """A 30 fps lock from 14 W behind a title screen at the 60 fps target, the package at the limit: the lock's limit
    within 85 s of the game's first frame (30 s of them: a new scene has to stay before a step), and no look at the
    full limit after. A step that fails while the title ends says nothing of the game: that level is not closed for it."""
    game = lambda w: 30.0 if w >= 14 else 2.08 * w
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60), fps=lambda w, t: 60.0 if t < title_s else game(w))
    g.draw = lambda w, t: float(w)
    g.run(title_s + 85)
    assert g.limit == 14, (title_s, g.limit, g.writes)
    t0 = g.t
    g.run(105)
    assert g.limit == 14 and 30 not in g.since(t0), (title_s, g.since(t0))


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("age, booked", [(3.0, 0), (8.0, 0), (12.0, 1), (20.0, 1)])
def test_a_dip_soon_after_a_write_is_not_one_of_the_games_own(age, booked, held):
    """A short loss at the full limit is one of the game's own dips (two of them make a game that dips: a second of
    patience before every answer). In the 10 s after a write it is not booked: what counts as a loss follows the wobble
    of the last seconds, and a place that wobbles more than the last one looked like a game that dips. The same for
    a loss that passes at a held limit, in a game that dips already."""
    dip = {"at": None}
    low = lambda t: dip["at"] is not None and dip["at"] <= t < dip["at"] + 0.6
    if held:
        g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16),
                 fps=lambda w, t: (60.0 if w >= 14 else 4.0 * w) * (0.8 if low(t) else 1.0))
    else:
        g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w, t: (60.0 if w >= 30 else 40.0) * (0.8 if low(t) else 1.0))
    g.run(1)
    while not ((g.limit < 30) == held and g.st.mode == "hold" and age - 0.6 <= g.t - g.st.changed_at <= age
               and g.st.quiet_until > g.t + 3):
        g.run(0.1)
        assert g.t < 900
    if held:
        g.st.dips.extend([g.t, g.t])
    wrote, before, dip["at"] = g.st.changed_at, len(g.st.dips), g.t
    g.run(3)
    assert g.st.changed_at == wrote and len(g.st.dips) - before == booked, (age, g.st.dips)


def test_in_a_game_that_dips_and_stutters_a_step_that_holds_gets_no_look():
    """One frame in ten is half as long again (an emulator that stutters): the rate is 5 % under what the typical
    frame says. A step in a game that dips is judged on its typical frame against the typical frame before it; against
    the rate before it, every step that held looked like 5 % gained, and got a look at the full limit."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w: 60.0, shape=lambda i, w, t: 1.5 if i % 10 == 0 else 1.0)
    g.st.dips.extend([0.0, 0.0])
    g.run(12)
    assert [w for _, w in g.writes[:3]] == [28, 26, 24], g.writes


@pytest.mark.parametrize("long_s, factor", [(0.15, 2.0), (0.15, 2.4), (0.3, 2.0)])
def test_long_frames_in_a_sessions_first_moments_are_not_the_start_levels_rate(long_s, factor):
    """No target, a start level that is enough, and the session's first frames are long ones. What the start level
    gives is asked of its last 20 frames; on the pace, which had the long ones in it, the full limit seemed to give
    a fifth more: the start level was booked as failed and the limit stayed up."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=16), fps=lambda w: 60.0 if w >= 12 else 5.0 * w,
             shape=lambda i, w, t: factor if t < long_s else 1.0)
    g.run(20)
    assert 16 not in g.st.fail and g.limit <= 14, (g.writes, g.notes[:3])


def test_slow_frames_that_are_the_limits_get_a_watt_over_the_edge_twice_and_then_the_limit_stays_up():
    """Slow frames that start at a held limit, at every limit under 17 W and at none above: the answer is a watt more,
    with no look at the full limit. Twice in RETRY_S; a third level with them means the edge is not near, or they are
    the game's own: the full limit says which. The limit's: it stays up, and the way down from there finds the edge."""
    rough = {"on": False}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=30, start_w=16), fps=lambda w: 30.0 if w >= 14 else 2.1 * w,
             shape=lambda i, w, t: 1.3 if rough["on"] and w < 17 and i % 12 == 0 else 1.0)
    g.draw = lambda w, t: float(w)
    g.run(100)
    t0, rough["on"] = g.t, True
    assert g.limit == 14
    g.run(34)
    notes = [n for at, n in g.notes if at >= t0]
    assert g.since(t0) == [15, 16, 30], (g.since(t0), notes)
    assert "staying up" in notes[-1], notes
    g.run(60)
    t1 = g.t
    assert g.limit == 17, (g.limit, [n for at, n in g.notes if at >= t0])
    g.run(240)
    assert g.limit == 17 and 30 not in g.since(t1), g.since(t1)     # (a try of 16 W: its slow frames end it, no look)


def test_a_need_that_fell_behind_a_video_at_the_same_frame_rate_is_found_by_the_next_try_of_what_failed():
    """60 fps from 30 W before a video, from 20 W after it, and nothing in the frames says so (a video or a menu
    changes nothing for the game that comes back). The level that failed is tried again when its time is up, at most
    AFTER_LOSS_MAX_S later, and the way down goes on from there."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=30), fps=lambda w: 60.0 * min(1.0, w / 30))
    g.draw = lambda w, t: float(w)
    g.run(150)
    g.fps = lambda w: 30.0
    g.run(40)
    g.fps, t0, held = (lambda w: 60.0 * min(1.0, w / 20)), g.t, g.limit
    g.run(gov.AFTER_LOSS_MAX_S + 60)
    first = next((at - t0 for at, w in g.writes if at >= t0 and w < held), None)
    assert first is not None and first <= gov.AFTER_LOSS_MAX_S and 19 <= g.limit <= 21, (first, g.limit)


@pytest.mark.parametrize("lag", [0.3, 0.6])
@pytest.mark.parametrize("heavy", [27, 29, 30])
@pytest.mark.parametrize("loading", [0, 3])
@pytest.mark.parametrize("target, cap", [(30, 40.0), (30, 30.0), (120, 30.0)])
def test_a_step_that_starves_the_game_is_a_failed_step_and_is_not_tried_again_every_few_seconds(lag, heavy, loading,
                                                                                               target, cap):
    """A game on a frame limit gives the same frames at the full limit in a place that needs 8 W and in one that needs
    nearly all of it. The heavy place has under 35 % of the frames at 8 W: the full limit, and nothing is known of 8 W
    there. So the level that held in the light place is tried first. The chip's watts follow a write with a delay, and
    when they have arrived the frames are under 35 % again: "too few frames to judge on" took the step back unjudged,
    and it was tried again every 12 s."""
    need, chip = {"w": 8}, {"w": None, "t": 0.0}

    def watts(limit, t):
        if chip["w"] is None:
            chip["w"] = float(limit)
        chip["w"] += (limit - chip["w"]) * (1 - math.exp(-max(0.0, t - chip["t"]) / lag))
        chip["t"] = t
        return chip["w"]

    g = Game(gov.Cfg(floor=8, ceiling=30, target=target), fps=lambda w: cap,
             shape=lambda i, limit, end: max(1.0, need["w"] / watts(limit, end)))
    g.draw = lambda limit, t: min(watts(limit, t), need["w"] + 2.0)
    g.run(180)
    assert g.limit == 8, g.writes
    g.run(loading, frames=False)
    need["w"], t0 = heavy, g.t
    g.run(300)
    tries = [(at, w) for at, w in g.writes if at >= t0 + 5 and w < 12]
    lowest = 0.95 * min(target or cap, cap) * heavy / cap            # the lowest limit that gives the goal (a look at
    assert len(tries) <= 1 and -1 <= g.limit - lowest < 2, (tries, g.limit, g.notes[-6:])    # watts still arriving: 1 W)


@pytest.mark.parametrize("menu, loading", [(False, 8), (False, 20), (True, 0), (True, 20)])
@pytest.mark.parametrize("starved", [29.2, 30.2, 31.0])
def test_the_game_back_from_a_video_at_the_videos_frame_rate_gets_the_full_limit_after_a_loading_screen(menu, loading, starved):
    """A session starts in a 30 fps video, which takes the limit down, and the game comes by way of a loading screen
    or a menu. At the video's limit the game gives the video's frame rate, and what the full limit gave in the video
    was taken for what it gives the game: half the frames for minutes. After a loading screen nothing is known at the
    limit, and a pace under the target is a loss. With no loading screen there is no sign while the game's pace is
    within ABOVE over the video's and SUSTAINED under it: straight from the video
    (`test_a_need_that_fell_behind_a_video_at_the_same_frame_rate_is_found_by_the_next_try_of_what_failed`), and by
    way of a menu of under SCENE_S."""
    scene = {"now": "video"}
    rate = {"game": lambda w: 58.8 if w >= 15 else starved, "video": lambda w: 30.0, "menu": lambda w: 60.0}
    g = Game(gov.Cfg(floor=8, ceiling=17, target=60), fps=lambda w: rate[scene["now"]](w))
    g.run(120)
    assert g.limit < 15, g.writes
    g.run(loading, frames=False)
    if menu:
        scene["now"] = "menu"
        g.run(12)
        g.run(loading, frames=False)
    scene["now"], t0 = "game", g.t
    g.run(12)
    if loading or not 30.0 * (1 - gov.SUSTAINED) <= starved <= 30.0 * (1 + gov.ABOVE):
        assert 17 in g.since(t0), (g.writes[-4:], g.notes[-4:])
        g.run(60)
        assert g.limit >= 15, (g.writes[-6:], g.notes[-4:])
    else:
        assert g.since(t0) == [], g.since(t0)


def test_a_frame_of_three_times_the_usual_time_is_left_out_of_a_rate_and_one_of_twice_is_not():
    """The game's own long frames are not its frame rate: a rate is the frames over their time without them. A frame
    of twice the usual time is a slow frame, and in the rate."""
    assert gov._fps([33.3] * 9 + [100.0]) == pytest.approx(1000 / 33.3)
    assert gov._fps([33.3] * 9 + [66.6]) == pytest.approx(10000 / (33.3 * 9 + 66.6))


def _at_14_w_with_a_level_that_failed():
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=HOLDS_FROM_14).run(60)
    assert g.limit == 14 and g.st.fail, (g.limit, g.st.fail)
    return g, max(g.st.fail)


def test_a_level_that_fails_again_and_again_is_tried_again_after_ten_minutes_at_most():
    """The wait before the next try of a failed level doubles at each failure: 2, 4, 8 minutes, then 10."""
    g, low = _at_14_w_with_a_level_that_failed()
    g.st.fail[low][0], g.st.fail[low][1], g.st.quiet_until = g.t, 3, g.t    # its third failure; the next try is due
    g.run(30)
    retry_at, n, _, _, at = g.st.fail[low]
    assert n == 4 and retry_at - at == pytest.approx(600), g.st.fail


@pytest.mark.parametrize("misses", [9, 1024])
def test_after_many_failed_steps_in_a_row_the_next_step_waits_five_minutes_at_most(misses):
    """Steps wait 5 s after a failed one, twice that after each further one in a row: 10, 20, ... s, then 5 minutes;
    also after a thousand (5 s times 2 to the 1024th is past a float: the governor stopped, after 7 days)."""
    g, low = _at_14_w_with_a_level_that_failed()
    g.st.fail[low][0], g.st.misses, g.st.miss_ref, g.st.quiet_until = g.t, misses, g.st.fail[low][2], g.t
    t0 = g.t
    g.run(30)
    assert low in g.since(t0) and g.st.misses == misses + 1, (g.since(t0), g.st.misses)
    assert g.st.quiet_until - g.writes[-1][0] == pytest.approx(gov.BACK_S + gov.AFTER_LOSS_MAX_S, abs=0.5), g.writes[-2:]


@pytest.mark.parametrize("apart, waited", [(200, True), (400, False)])
def test_two_of_the_games_own_dips_within_five_minutes_make_a_loss_wait_a_second(apart, waited):
    g, _ = _at_14_w_with_a_level_that_failed()
    g.run(20)
    g.st.dips.clear()
    g.st.dips.extend([g.t - apart, g.t])
    t0 = g.t
    g.fps = lambda w, t: HOLDS_FROM_14(w) * (0.75 if t0 <= t < t0 + 0.8 else 1.0)
    g.run(4)
    assert (g.since(t0) == []) == waited, g.since(t0)


@pytest.mark.parametrize("lasts, booked", [(1.9, True), (3.0, False)])
def test_a_loss_at_the_full_limit_that_is_over_within_two_seconds_is_one_of_the_games_own_dips(lasts, booked):
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=30), fps=lambda w: 40.0 if w >= 30 else 30.0).run(40)
    while g.limit < 30 or g.t - g.st.changed_at < gov.RECENT_S + 2:
        g.run(0.5)
        assert g.t < 400
    t0, n = g.t, len(g.st.dips)
    g.fps = lambda w, t: (40.0 if w >= 30 else 30.0) * (0.7 if t0 <= t < t0 + lasts else 1.0)
    g.run(lasts + 2)
    assert (len(g.st.dips) == n + 1) == booked and g.limit == 30, (g.st.dips, g.writes[-3:])


def test_the_next_try_of_a_level_that_failed_for_slow_frames_is_watched_ten_seconds():
    """Slow frames come seldom: 2.5 s of a step do not show that they are gone."""
    g, low = _at_14_w_with_a_level_that_failed()
    g.fps = lambda w: 60.0                                           # the place got lighter: the level holds now
    g.st.fail[low][0], g.st.fail[low][3], g.st.quiet_until = g.t, True, g.t
    while g.limit != low:
        g.run(0.1)
        assert g.t < 200
    t0, n = g.t, len(g.notes)
    while len(g.notes) == n and g.limit == low:
        g.run(0.1)
        assert g.t < t0 + 60
    assert 9.5 <= g.t - t0 <= 11.0, (g.t - t0, g.notes[-2:])


def test_a_look_for_slow_frames_ends_after_ten_seconds_in_a_game_of_few_frames():
    """A share of slow frames needs 240 frames. At 20 fps those take 12 s: the look ends after 10."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=16), fps=lambda w: 20.0,
             shape=lambda i, w, t: (1.3, 0.7)[i % 12] if w < 27 and i % 12 < 2 and t > 40 else 1.0)
    g.st.base_slow = 0.0
    while not (g.st.mode == "check" and g.st.why == "slow"):
        g.run(0.1)
        assert g.t < 400
    t0 = g.t
    while g.st.mode == "check":
        g.run(0.1)
    assert 9.5 <= g.t - t0 <= 11.5, g.t - t0


@pytest.mark.parametrize("a_look, judged_after", [(0.45, (2.9, 4.0)), (0.2, (0.9, 1.7))])
def test_a_look_is_judged_when_the_package_draw_has_stopped_rising(a_look, judged_after):
    """The watts arrive after the write. While the package draws 0.3 W more at each look of the watch they are still
    arriving, and the frames are not the full limit's yet (3 s at most)."""
    g, _ = _at_14_w_with_a_level_that_failed()
    g.draw = lambda w, t: float(w) if w < 30 else min(30.0, 14.0 + a_look * (t - g.st.changed_at) / gov.WATCH_S)
    g.run(20)
    t0 = g.t
    g.fps = lambda w, t: HOLDS_FROM_14(w) * (0.75 if w < 30 and t >= t0 else 1.0)          # the need rose
    while g.limit != 30:
        g.run(0.1)
        assert g.t < t0 + 20
    t1, n = g.t, len(g.notes)
    while len(g.notes) == n:
        g.run(0.1)
        assert g.t < t1 + 20
    assert judged_after[0] <= g.t - t1 <= judged_after[1], (g.t - t1, g.notes[-2:])


# ---- what a look found is that place's ------------------------------------------------------------------------------
@pytest.mark.parametrize("target", [60, 120])
@pytest.mark.parametrize("other, seconds, lost, need", [(30.0, 12, 30.0, 28), (53.0, 12, 54.0, 20), (56.0, 20, 55.0, 20),
                                                        (45.0, 40, 48.0, 24)])
def test_a_loss_at_the_frame_rate_of_an_earlier_video_or_heavier_place_gets_the_full_limit(other, seconds, lost, need, target):
    """A video or a heavier place gives `other` fps at every limit: one look, the scene, and back. The game goes on.
    Later the need rises, and the limit that held gives about the frame rate that look saw, the full limit all 60: a
    loss, and the full limit within 3 s (taken into the band of the limit, the slower place hid it for 9 minutes)."""
    place = {"p": "game"}

    def fps(w, t):
        if place["p"] == "other":
            return other
        if place["p"] == "lost":
            return 60.0 if w >= need else lost
        return 60.0 * min(1.0, w / 14)
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=16), fps=fps)
    g.run(150)
    place["p"] = "other"
    g.run(seconds)
    place["p"] = "game"
    g.run(20)
    t0 = g.t
    place["p"] = "lost"
    g.run(60)
    first = next((at for at, w in g.writes if at >= t0), None)
    assert first is not None and first - t0 <= 3.0 and g.limit >= need, (first, t0, g.limit, g.since(t0)[:6])


@pytest.mark.parametrize("target", [120, 60])
def test_a_heavier_place_at_the_frame_rate_of_an_earlier_video_is_a_loss(target):
    """A 30 fps video over a game held at the 12 W it needs: one look, the scene. The game again. Later a place that
    needs 24 W gives the video's 30 fps at 12 W and 60 at the full limit: a fall, not the video's band again (120 s of
    120 at half the frames)."""
    place = {"p": "game"}

    def fps(w, t):
        return 30.0 if place["p"] == "video" else 60.0 * min(1.0, w / (24.0 if place["p"] == "heavy" else 12.0))
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=14), fps=fps)
    g.draw = lambda w, t: float(w)
    g.run(90)
    place["p"] = "video"
    g.run(15)
    place["p"] = "game"
    g.run(15)
    place["p"], lost = "heavy", 0
    for _ in range(120):
        g.run(1.0)
        lost += fps(g.limit, g.t) < 54
    assert lost <= 12, lost


@pytest.mark.parametrize("target", [120, 60])
def test_the_game_after_an_intro_video_that_drew_little_is_not_held_at_the_videos_limit(target):
    """A 30 fps intro video that draws 6 W, then the game: 60 fps from 22 W, 30 under it, and the package draws all
    of the limit. The band of the lowered limit was taken while the package drew far less than it: nothing is known
    there once the package draws all of it (597 s at half the frames)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target), fps=lambda w, t: 30.0 if t < 40 else 60.0 if w >= 22 else 30.0)
    g.draw = lambda w, t: 6.0 if t < 40 else float(w)
    starved = 0
    for _ in range(300):
        g.run(1.0)
        starved += g.t > 45 and g.limit < 22
    assert starved <= 15, (starved, g.writes[:12])


def test_frames_over_the_target_from_the_limits_own_steps_are_no_other_scene():
    """Target 60 in a game that makes 4 fps a watt (120 at the full limit; 60 needs 15 W). The frame rate moves only
    because the limit does: no new scene, and no 30 s of waiting before each step (15 W after 60 s and more)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=12), fps=lambda w: 4.0 * w)
    g.draw = lambda w, t: float(w)
    g.run(120)
    assert next(t for t, w in g.writes if w == 15) < 45, g.writes


def test_a_target_that_a_lowered_limit_nearly_gives_is_not_out_of_reach():
    """60 fps from 18 W, 58 at 16 and 17 W (within the margin of a target of 60): the target is reached, and what is
    shown and saved says so (58 was booked as what the full limit gives: "60 out of reach")."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 60.0 if w >= 18 else 58.0 if w >= 16 else 50.0)
    g.draw = lambda w, t: float(w)
    g.run(180)
    assert not gov.out_of_reach(g.st, g.cfg) and gov.goal(g.st, g.cfg) == pytest.approx(57.0), (g.st.ref, g.limit)


def test_a_level_that_failed_at_a_look_is_not_the_level_to_go_back_to_in_that_scene():
    """Target 60, 4 fps a watt: 15 W held for minutes. A heavier place (3 fps a watt): the look says 15 W was too low.
    15 W is not tried again seconds later as "the level that held in this scene" (its failure was booked with the
    full limit's 90 fps, the level with the target's 60)."""
    heavy = {"on": False}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: (3.0 if heavy["on"] else 4.0) * w)
    g.draw = lambda w, t: float(w)
    g.run(100)
    while not (g.notes[-1][0] > 100 and "back to 15 W" in g.notes[-1][1]):
        g.run(0.1)
        assert g.t < 900, g.notes[-3:]
    g.run(3)
    n, heavy["on"] = len(g.notes), True
    g.run(20)
    assert [x for x in g.notes[n:] if x[1].startswith("15 W was too low")], g.notes[n:]
    assert not [x for x in g.notes[n:] if "trying 15 W" in x[1]], g.notes[n:]


def test_in_a_lighter_scene_a_level_that_failed_is_tried_again_as_a_first_failure():
    """14 W fails in a scene where the full limit gives 60 fps. From 150 s on the full limit gives 80 (a third more:
    another scene): 14 W is tried again there without its wait, and when it fails there too that is a first failure
    (the next try after 2 minutes, not 8)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120), fps=lambda w, t: (60.0 if t < 150 else 80.0) * min(1.0, w / 15))
    g.draw = lambda w, t: float(w)
    g.run(150)
    assert g.st.fail[14][1] == 2 and g.st.fail[14][0] > 300, g.st.fail[14]       # failed twice: the next try at 379 s
    while g.st.fail[14][4] < 150:
        g.run(0.5)
        assert g.t < 200, (g.st.fail[14], g.writes[-6:])
    assert g.st.fail[14][1] == 1 and g.st.fail[14][2] == pytest.approx(80.0, rel=0.02), g.st.fail[14]


@pytest.mark.parametrize("target", [60, 120])
def test_the_level_that_held_in_another_place_is_not_taken_in_a_menu(target):
    """A light place held at 14 W for minutes, then a heavy place (45 fps from 26 W) held near 26 W. A menu at 60 fps
    over it for 20 s: no step from the menu's frames, and not the light place's level either."""
    place = {"p": "light"}

    def fps(w, t):
        if place["p"] == "menu":
            return 60.0
        return 45.0 * min(1.0, w / 26) if place["p"] == "heavy" else 60.0 * min(1.0, w / 14)
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=16), fps=fps)
    g.run(120)
    place["p"] = "heavy"
    g.run(240)
    t0, place["p"] = g.t, "menu"
    g.run(20)
    assert all(w >= 25 for w in g.since(t0)), g.since(t0)


@pytest.mark.parametrize("target", [60, 120])
@pytest.mark.parametrize("menu_s", [20, 28])
def test_a_menu_at_nearly_the_games_frame_rate_takes_no_step_down_when_the_package_draws_less(menu_s, target):
    """A title screen and a menu at 60 fps, a game at 52 fps from 20 W: by its frame rate the menu is no other scene
    (under 15 % away). The package draws 9 W in it and all of the limit in the game: a lighter scene, and no step
    down from its frames (the game came back at 8 W: 21 of its 52 fps)."""
    place = {"p": "title"}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target), fps=lambda w, t: 52.0 * min(1.0, w / 20) if place["p"] == "game" else 60.0)
    g.draw = lambda w, t: min(float(w), 21.0) if place["p"] == "game" else 9.0
    g.run(10)
    place["p"] = "game"
    g.run(300)
    held, t0, place["p"] = g.limit, g.t, "menu"
    g.run(menu_s)
    assert held <= 22 and all(w >= held for w in g.since(t0)), (held, g.since(t0))


def test_bursts_of_the_games_own_slow_frames_are_not_the_limits():
    """Every third frame 20 % long for 1.5 s every 30 s, at every limit; the need is 14 W. The look for slow frames
    finds none at the full limit, because it falls between two bursts: slow frames in one burst are the game's (23 to
    26 W, with each level under it booked as failed)."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=30, start_w=16), fps=lambda w: 30.0 if w >= 14 else 30.0 * w / 14,
             shape=lambda i, w, t: 1.2 if (t % 30) < 1.5 and i % 3 == 0 else 1.0)
    g.draw = lambda w, t: float(w)
    g.run(1200)
    late = [(at, w) for at, w in g.writes if at >= 120]
    assert g.limit <= 17 and len(late) <= 18 and not [n for a, n in g.notes if a >= 120 and "none of the slow frames" in n], (g.limit, late)


@pytest.mark.parametrize("target", [120, 60])
@pytest.mark.parametrize("drop", [0.035, 0.04, 0.045, 0.06, 0.12])
def test_a_fall_that_stays_at_the_full_limit_is_no_dip_of_the_games_own(drop, target):
    """At the full limit a place gets heavier for good. A dip of the game's own is over within 2 s: a frame rate that
    stays at the edge of a clear loss is not booked as a dip each time it crosses that edge (two or three were booked,
    and the game counted as one that dips)."""
    heavy = {"on": False}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30),
             fps=lambda w, t: 50.0 * (w / 30) ** 2 * ((1 - drop) if heavy["on"] else 1.0),
             shape=lambda i, w, t: 1.0 + 0.02 * math.sin(i * 0.37) + 0.02 * math.sin(i * 0.011))
    g.run(100)
    while not (g.st.mode == "hold" and g.limit == 30 and g.t - g.st.changed_at > 12 and g.st.quiet_until > g.t + 6):
        g.run(0.5)
        assert g.t < 2000, (g.limit, g.st.mode, g.writes[-6:])
    before, heavy["on"] = len(g.st.dips), True
    g.run(10)
    assert len(g.st.dips) == before, g.st.dips


def test_the_share_of_slow_frames_before_a_look_is_counted_as_every_other_share(monkeypatch):
    """A look for slow frames is judged against the share of slow frames at the limit before it. That share is counted
    as every other one (over the frames a share can be told for), not over all the frames of the limit."""
    seen, real = [], gov._edge

    def edge(ms, base):
        n = real(ms, base)
        seen.append((list(ms), n))
        return n
    monkeypatch.setattr(gov, "_edge", edge)
    rough = {"on": False}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=30, start_w=16), fps=lambda w: 30.0 if w >= 14 else 2.1 * w,
             shape=lambda i, w, t: 1.3 if rough["on"] and w < 17 and i % 12 == 0 else 1.0)
    g.draw = lambda w, t: float(w)
    g.run(100)
    rough["on"] = True
    looks, end, was = 0, g.t + 40, False
    while g.t < end:
        seen.clear()
        g.run(g.look)
        now = g.st.mode == "check" and g.st.why == "slow"
        if now and not was:
            looks += 1
            assert g.st.lost_slow == pytest.approx(gov._slow([ms for ms, n in seen if n][-1]))
        was = now
    assert looks


@pytest.mark.parametrize("target", [120, 60])
@pytest.mark.parametrize("rise, looked", [(0.025, False), (0.035, True), (0.045, True), (0.06, True)])
def test_a_few_percent_more_frames_at_a_held_limit_get_a_look_at_the_full_limit(target, rise, looked):
    """A place where 17 W of 25 holds the frames (45 of 47 fps), then one where 17 W gives a little more than it did
    and the full limit 60 fps. From 3 % more the full limit is looked at within 3 s; under that the pace is the band's."""
    first = lambda w: 47.4 * (1 - 0.006 * (25 - w))
    g = Game(gov.Cfg(floor=8, ceiling=25, target=target, start_w=25), fps=first).run(240)
    held, t0 = g.limit, g.t
    at_held = first(held) * (1 + rise)
    g.fps = lambda w: 59.6 - (25 - w) * (59.6 - at_held) / (25 - held)
    g.run(60)
    up = [t - t0 for t, w in g.writes if t >= t0 and w == 25]
    assert (up and up[0] <= 3.0) if looked else not up, (held, up, [n for n in g.notes if n[0] >= t0])


@pytest.mark.parametrize("target", [120, 60])
def test_a_failed_step_is_not_gone_back_from_when_the_full_limit_gives_far_more_than_the_limit_before(target):
    """A place that gives 45 fps from 12 W up (25 at 11 W), then one with the same 45 fps at 12 W and 60 at the full
    limit. The step to 11 W loses 20 fps and gets the full limit: 60 fps there is another scene than the 45 before the
    step, and 12 W is not gone back to."""
    place = {"second": False}
    fps = lambda w: 25.0 * w / 11 if w < 12 else (45.0 + 15.0 * (w - 12) / 18 if place["second"] else 45.0)
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=16), fps=fps)
    g.draw = lambda w, t: float(w)
    g.run(180)
    assert g.limit == 12
    place["second"] = True
    g.run(230)
    assert fps(g.limit) >= 57.0, (g.limit, [n for n in g.notes if n[0] >= 180])


@pytest.mark.parametrize("target", [120, 90])
@pytest.mark.parametrize("draw, waits", [(6.0, True), ("the limit", False), (None, False)])
def test_a_far_slower_pace_that_leaves_the_chip_free_is_stepped_in_after_the_wait_of_a_new_scene(target, draw, waits):
    """A 100 fps game, then 30 fps at every limit. Where the package draws 6 W of the limit (a video) the full limit
    stays for the 30 s of a new scene, and the game back after 20 s finds it; where it draws all of the limit, or the
    draw is not known, the slower pace is stepped in at once."""
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=30), fps=lambda w: 100.0 if w >= 20 else 40.0 + 5.0 * (w - 8))
    g.draw = lambda w, t: float(w)
    g.run(180)
    g.fps = lambda w: 30.0
    g.draw = (lambda w, t: float(w)) if draw == "the limit" else draw
    g.run(20)
    at_20 = g.limit
    g.run(14)
    at_34 = g.limit
    g.run(11)
    assert ((at_20, at_34) == (30, 30) if waits else at_20 < 30) and g.limit < 30, (at_20, at_34, g.limit)


@pytest.mark.parametrize("visit, back", [(10, []), (20, []), (40, [30, 14])])
def test_a_band_that_comes_back_after_a_visit_elsewhere_is_known_again_for_half_a_minute(visit, back):
    """60 fps from 14 W; after 400 s a place with 50 fps at every limit (the full limit once: the scene), then the game
    again. Back within SCENE_S the game's band is known again and nothing is written. After that long the same pace
    may be another place's: the full limit is asked once."""
    place = {"other": False}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=16), fps=lambda w: 50.0 if place["other"] else 60.0 * min(1.0, w / 14))
    g.draw = lambda w, t: float(w)
    g.run(400)
    place["other"] = True
    g.run(visit)
    assert g.since(400) == [30, 14], g.since(400)
    place["other"] = False
    g.run(60)
    assert g.since(400 + visit) == back, (g.since(400 + visit), [n for n in g.notes if n[0] >= 400])


def test_a_second_try_that_failed_does_not_make_the_next_step_a_second_try():
    """60 fps from 14 W, then from 18 W: the full limit, 14 W once more (it held for minutes), it fails, and the way
    down from the full limit goes on. Its steps are ordinary ones: one that holds is not booked as a dip of the game's."""
    need = {"w": 14.0}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=60, start_w=16), fps=lambda w: 60.0 if w >= need["w"] else 60.0 * w / need["w"])
    g.draw = lambda w, t: float(w)
    g.run(120)
    need["w"] = 18.0
    g.run(60)
    assert g.limit >= 18 and list(g.st.dips) == [], (g.limit, list(g.st.dips), [n for n in g.notes if n[0] >= 120])


@pytest.mark.parametrize("target", [120, 60])
def test_in_a_game_that_dips_a_second_try_that_loses_is_taken_back_at_once(target):
    """A game that dips by itself, held at 14 W for minutes; the need rises to 18 W. The fall is waited out a second
    (it may be a dip), the full limit shows it was not, and 14 W is tried once more: that loss was waited out once
    already and is the limit's."""
    need = {"w": 14.0}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=target, start_w=16), fps=lambda w: 60.0 if w >= need["w"] else 60.0 * w / need["w"])
    g.draw = lambda w, t: float(w)
    g.run(120)
    g.st.dips.extend([g.t - 20, g.t - 10])
    need["w"] = 18.0
    g.run(30)
    again = [t for t, n in g.notes if t >= 120 and n.endswith("14 W once more")]
    back = [t for t, w in g.writes if again and t > again[0] and w == 30]
    assert len(again) == 1 and back and back[0] - again[0] <= 0.6, (again, back, [n for n in g.notes if n[0] >= 120])


@pytest.mark.parametrize("visit", [10, 20])
def test_a_band_that_comes_back_is_not_taken_for_one_of_a_place_that_left_the_chip_free(visit):
    """60 fps from 14 W with the package drawing all of the limit; a place with 50 fps at every limit that draws 6 W
    (one look: the scene), then the game again. The game's band is its own: the package drawing all of the limit again
    says nothing against it, and it is no fall. One look follows, the one after a menu with a target out of reach."""
    place = {"other": False}
    g = Game(gov.Cfg(floor=8, ceiling=30, target=120, start_w=16), fps=lambda w: 50.0 if place["other"] else 60.0 * min(1.0, w / 14))
    g.draw = lambda w, t: 6.0 if place["other"] else float(w)
    g.run(100)
    place["other"] = True
    g.run(visit)
    assert g.limit == 14 and g.st.free_w == 14
    place["other"], t0 = False, g.t
    g.run(30)
    said = [n for t, n in g.notes if t >= t0]
    assert g.since(t0) == [30, 14] and "before a menu" in said[0] and "back to 14 W" in said[1], (g.since(t0), said)
