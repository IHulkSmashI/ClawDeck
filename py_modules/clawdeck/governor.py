"""The Autotune governor: from the frames of the last moments, the next power limit.

One question, asked all the time: would more watts, up to your limit, give frames it has to deliver?
  no  -> try fewer: a step down, watched for DWELL_S on every frame and taken back at the first loss;
  yes -> all of them at once (the full limit), then down again between the watts that failed and the watts that hold.
The full limit is written when the frames ask for it: a loss; more frames than the held limit was known to give (another
place); slow frames that a watt more did not end. By the clock every CHECK_MAX_S and once after a menu, both only
while the goal is what the full limit gives (a target out of reach). Never because a step held or failed: the governors of other projects
have no such looks, and on the Claw they cost changes and gave nothing.

Why this shape (measured on a Claw 8 EX, 2026-11-14/15):
  - frames answer a new limit within 0.15-0.35 s and are back 0.25 s after a raise, so a step down can be tried and
    undone before it is seen: 46 such steps during play cost about 0.6 s of at most 10 % slower frames each;
  - only that answer says why frames are slow. An emulator that spins (RPCS3) draws the whole limit at every limit and
    keeps the chip's "limited by power" flag set while its frames hold; a heavy scene and a limit that is too low look
    the same in the frames;
  - the watts a game needs move fast (under 14 W to over 30 W within 20 s), so what was found is where to start, never
    a result.

step(state, obs, cfg) has no clock and no I/O: the same frames give the same limits, on a Claw and in a test.
It changes `state` in place and returns what to do.

Words:
  pace       the frame time the game holds: the median of the last PACE_FRAMES frames;
  reference  what the full limit gives: measured there, or the pace with the package 2 W or more under the limit;
  goal       what has to be delivered: your target (or the reference when that is lower and Auto FPS is on) less
             TOL;
  band       the pace at the held limit when the limit was last known to cost no more than that: by the full limit
             after a loss, by the step that came here and held, or all the time while the limit can't hold the game
             back (the full limit, the package 2 W under the limit, the pace at your target). Lowest and highest;
  ratio      what the held limit gave over what the full limit gave, when both were last measured. After that the
             reference is the pace at the held limit over it: it moves with the game's own frame rate;
  fail       watts that lost frames, each with the time it may be tried again.
"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field, replace

N = 10                  # a clear loss: the last N frames are CLEAR under the goal ...
CLEAR = 0.03
SUSTAINED = 0.01        # ... a sustained one: the pace (PACE_FRAMES) is this far under it. A step down has to hold the
                        # goal itself over its whole watch, so a level that was accepted isn't lost to noise afterwards
NOISE = 2.5             # ... and both at least this many times what the game's own rate does by itself over such a
                        # window (frame times with 9 % spread move a 10-frame rate by about 3 % all the time)
HITCH = 2.5             # a frame this many times the median is one of the game's own long frames: left out of a rate
PATIENT_S = 1.0         # a game that dips by itself (DIPS short losses in DIPS_S that the limit can't have caused, each
DIPS, DIPS_S = 2, 300.0 # over within DIP_MAX_S): a loss counts only when it is still there after PATIENT_S
DIP_MAX_S = 2.0
PACE_FRAMES = 60
TOL = 0.05              # target mode: this far under the target is still the target
REACHED = 0.99          # the full limit gives this much of the target: the target is in reach
SLOW = 1.10             # a frame this much longer than the pace is a slow one ...
EDGE = 0.03             # ... and this much more of them than before a step is the edge of the limit (15 W in
                        # Midnight Club: Los Angeles: the median on pace, 6 % slow frames)
SLOW_FRAMES, SLOW_MAX_S = 240, 10.0     # the full limit for slow frames lasts until this many frames are in (a share
                        # of 6 % against the game's own shows in them, not in the 36 frames of a second), at most this
                        # long
SETTLE_S = 0.15         # frames that end this soon after a write still belong to the limit before
DWELL_S = 2.5           # a step down is watched this long
BACK_S = 1.0            # a raise is judged on the frames of this long
HOLD_S = 10.0           # no step down sooner after a change ...
AFTER_LOSS_S = 5.0      # ... or after a loss was answered; doubled for each step down in a row that failed,
AFTER_LOSS_MAX_S = 300.0  # up to this (a success, or another scene, starts it over)
HOME_S = 20.0           # a limit held this long is where to go back to when the scene that drove it out is over
STEP_W = 2
CHAIN = 3               # steps down in a row; then the rules of holding run
RETRY_S, RETRY_MAX_S = 120.0, 600.0    # a failed level may be tried again after this; doubled at each further failure
RETRY_DWELL_S = 10.0    # ... and the next try of a level that failed for its slow frames is watched this long (2.5 s
                        # do not show a share of slow frames of 6 %)
ABOVE = 0.03            # the pace this much over the band: the full limit may give more too. And what held belongs
                        # to a scene: a level that held counts while the full limit gives within this of what it gave
                        # then (what failed: _counts)
STEADY_S = 3.0          # a step down starts only while the frame rate of each half second of the last STEADY_S is
                        # within the game's own wobble of their median
FREE_LOOKS = 5          # the reference is the median pace of the last looks at a limit that can't hold the game back,
                        # at least this many: one of the game's own dips doesn't move it
BIND_S, BIND_W = 3.0, 2.5   # a step down is watched from when the package draws less than BIND_W over the new limit
                        # (PL2 allows 2 W over it): a cut made while the chip ran over its limit took 2.3 s to bind. A
RISE_W = 0.3            # raise is judged when the draw has stopped rising by this much a look. Both at most BIND_S late
SHIFT, SHIFT_S = 0.15, 5.0             # the pace moved this much for this long at one limit: another scene
CHECK_MAX_S = 600.0     # what the full limit said of a held limit is asked again when it is this old
SCENE_S = 30.0          # ... and no step down is tried in it before it has stayed this long (a menu, a map, a video)
OFF_LO = 0.35           # frames this far under the usual rate are a loading screen or a pause: not judged ...
ADOPT_S = 5.0           # ... unless they stay this long: then that is the game now
NO_FRAMES_S = 1.5
RECENT_S = 10.0
LOOK_S, WATCH_S = 0.5, 0.1             # how soon to look again: holding, and while a write is being watched


@dataclass
class Cfg:
    floor: int
    ceiling: int                    # your limit
    target: float                   # fps
    auto_fps: bool = True           # a target out of reach: hold what the full limit gives instead
    start_w: int | None = None      # where this game held last time; None: mid-range


@dataclass
class Obs:
    t: float                                    # now (s, any steady clock)
    frames: list = field(default_factory=list)  # (end time s, frame time ms) since the last step, oldest first
    draw_w: float | None = None                 # package power since the last step
    thermal: bool = False                       # the temperature guard is at work, or the chip says "thermal"
    cap_w: int | None = None                    # the guard's cap on the limit
    multiplier: float = 1.0                     # shown frames per rendered frame (frame generation)


@dataclass
class Action:
    limit_w: int | None = None      # write this limit now (None: leave it)
    look_s: float = LOOK_S          # look again after this long
    note: str | None = None         # what was decided, for the log and the panel


@dataclass
class State:
    limit: int
    mode: str = "hold"              # hold | probe (a step down is watched) | back (a failed step was undone) |
                                    # recover (the full limit after a loss) | check (the full limit for the reference)
    t: float | None = None
    since: float = 0.0              # when the write being watched was made
    prev: int = 0                   # the limit before it
    changed_at: float = -1e9
    quiet_until: float = -1e9       # no step down before this
    fail: dict = field(default_factory=dict)        # watts -> [may be tried again at, failures, what the full limit gave,
                                                    # it was the slow frames, when]
    misses: int = 0                 # steps down in a row that failed, and what the full limit gave at the last one
    miss_ref: float | None = None
    ref: float | None = None        # the reference as last measured (at the full limit, or the pace at a free limit)
    lo: float | None = None         # the band: the pace at the held limit at which it was last known to cost no more
    hi: float | None = None         # than the margin (None: not known at this limit)
    ratio: float | None = None      # what the held limit gave over what the full limit gave then
    kept: tuple = (None, None, None, None, None, None)  # what was known at the limit before the write being watched:
                                    # band, ratio, `was`, `seen_hi`, `free_w`
    band_at: float = -1e9           # since when the band is this one
    was: tuple | None = None        # band, ratio and reference this limit had before its pace moved to another band
                                    # (a menu, a map, a video), when that was, and since when it had been the band:
                                    # back in it once within SCENE_S, they are known again
    seen_hi: float | None = None    # the pace at this limit that the full limit last did not beat: up to it, more
                                    # frames than the band has are no news (a game that goes between two paces) ...
    seen_at: float = -1e9           # ... for SCENE_S after the pace was last there (then it may be another place's)
    full_at: float | None = None    # when the full limit last said what the held limit costs (None: never)
    free_w: int | None = None       # the lowered limit whose band was taken while the package drew 2 W under it
    bound: bool = False             # the package drew all of the held limit at the last look that could tell
    why: str | None = None          # why the full limit was written: fall (a loss) | slow (slow frames that a watt
                                    # more did not end) | rise (more frames than the band has)
    lost_slow: float = 0.0          # the share of slow frames at the limit before it
    scene_fps: float | None = None  # the pace of the scene, since when the pace is SHIFT away from it, and when the
    scene_since: float | None = None  # scene last became another one
    scene_at: float = -1e9
    usual: float | None = None      # the pace of the scene that last stayed SCENE_S: what "a loading screen" is far
                                    # from, and the scene that what failed (and what held) belongs to
    free_fps: deque = field(default_factory=lambda: deque(maxlen=2 * FREE_LOOKS))   # the pace at the last free looks
    free_run: int = 0               # frames in a row that came in looks where the limit couldn't hold the game back
    draw_was: float | None = None   # the package draw at the last look, and when a watched write was made
    wrote_at: float = -1e9
    off_since: float | None = None  # since when the frames are far from it
    gap_s: float = 0.0              # ... and how long the stretch has lasted
    noise: float = 0.0              # how much the game's rate of N frames wobbles by itself (relative), from the pace
    noises: deque = field(default_factory=lambda: deque(maxlen=21))
    dips: deque = field(default_factory=lambda: deque(maxlen=8))   # when it lost frames at a limit that can't be the cause
    loss_since: float | None = None # a loss seen in a game that dips by itself: since when
    small_since: float | None = None
    free_loss: float | None = None  # a loss at a limit that can't be its cause: since when (a dip if it is over soon)
    held_for: float | None = None   # how long the limit had held when a loss sent it to the full limit
    said: tuple = (-1e9, 0.0)       # when the full limit last said of a loss "the scene, not the limit", and what
                                    # it gave then
    retested: dict = field(default_factory=dict)    # watts -> when a level that lost frames was last tried once more
    retest: bool = False            # the step being watched is such a second try
    long: bool = False              # the step being watched is the next try of a level that failed: RETRY_DWELL_S
    chain: int = 0                  # steps down in a row since the last hold
    top: int | None = None          # the most that was allowed at the last step (your limit, or the guard's cap)
    lost: float | None = None       # the frame rate before a raise, fps
    before: float | None = None     # the frame rate before the step down being watched, fps
    typ: float | None = None        # ... and the typical frame then, ms (the median)
    edged: deque = field(default_factory=lambda: deque(maxlen=STEP_W))   # when the limit last got a watt more for its
                                    # slow frames
    tried: int | None = None        # a step down that lost far more than its size explains (the loss is its, not prev's)
    home: int | None = None         # the last limit that held for HOME_S, and what the full limit gave then: tried
                                    # first when its scene is back
    home_ref: float | None = None
    base_slow: float | None = None  # share of slow frames before a step down (the game's own); None: not taken yet
    frame_at: float | None = None   # when the last frame ended
    last: deque = field(default_factory=lambda: deque(maxlen=N))
    pace: deque = field(default_factory=lambda: deque(maxlen=PACE_FRAMES))
    recent: deque = field(default_factory=deque)    # (end time, ms) of the last RECENT_S
    watch: list = field(default_factory=list)       # frame times since SETTLE_S after the write being watched
    held_s: dict = field(default_factory=dict)      # watts -> seconds held there (for the next start)


def start(cfg: Cfg, t: float) -> tuple[State, Action]:
    """A game starts: where it held last time, else mid-range."""
    w = cfg.start_w if cfg.start_w is not None else -(-(cfg.floor + cfg.ceiling) // 2)
    w = int(max(cfg.floor, min(cfg.ceiling, w)))
    return State(limit=w, t=t, changed_at=t, frame_at=t), Action(limit_w=w, note=f"start at {w} W")


def drop(st: State) -> None:
    """The write being watched is not judged (the guard's cap, a raised floor, a refused write): holding, and the next
    step down is a first try."""
    st.mode = "hold"
    st.retest, st.before, st.tried, st.loss_since = False, None, None, None
    _unknown(st)                                     # (what is on the chip now is not a limit that was judged)


def forget(st: State) -> None:
    """Nothing known of the game applies any more: frames far under the usual rate that stayed, another
    frame-generation multiplier."""
    st.ref = None
    _unknown(st)
    if st.mode != "hold":
        drop(st)                                     # (a watch in flight: what it is judged against is gone)
    st.fail.clear()
    st.misses, st.home = 0, None
    st.scene_fps = st.scene_since = None


def _unknown(st: State) -> None:
    """Nothing is known at the held limit."""
    st.lo = st.hi = st.ratio = st.was = st.seen_hi = st.free_w = None


def start_level(held_s: dict, floor: int, ceiling: int) -> int | None:
    """Where to start next time: the level under which 80 % of the holding time lies."""
    total = sum(held_s.values())
    if total < 60:
        return None
    acc = 0.0
    for w in sorted(held_s):
        acc += held_s[w]
        if acc >= 0.8 * total:
            return int(max(floor, min(ceiling, w)))
    return None


def _fps(ms) -> float | None:
    """Frames a second over a run of frame times: the frames over their time, without the game's own long frames (a
    median misreads frames that alternate short and long, as generated frames do)."""
    ms = list(ms)
    if not ms:
        return None
    line = statistics.median(ms) * HITCH
    kept = [x for x in ms if x <= line]
    return 1000.0 * len(kept) / sum(kept)


def _spread(ms) -> float:
    """How much the frame rate of N frames wobbles in a run, relative: the rates of its consecutive blocks of N frames,
    their range over 2.5 (about one standard deviation for a handful of blocks) and over their median. Frames that
    alternate short and long (generated frames) have no wobble by this; frame times with 9 % spread have about 3 %."""
    ms = list(ms)
    rates = [_fps(ms[i:i + N]) for i in range(0, len(ms) - N + 1, N)]
    if len(rates) < 3:
        return 0.0
    return (max(rates) - min(rates)) / 2.5 / statistics.median(rates)


def _slow_at(ms: list) -> list:
    """Where the slow frames of these frames are: more than SLOW over the median of the frames around them (the N
    before and the N after). Frames of their own time: a slower stretch of the game has no slow frames by this, and at
    the edge between a menu and the game neither side's frames are slow; a long frame between others is one. The
    first and the last N frames are not judged: a few long ones there can be the end of a slower stretch. Three or
    more slow frames in a row are a burst of the game's own: the edge of the limit makes them one or two at a time
    (Midnight Club: Los Angeles at 15 W: 14 slow frames, none three in a row)."""
    at, run = [], []
    for i in range(N, len(ms) - N):
        if ms[i] > sorted(ms[i - N:i + N + 1])[N] * SLOW:
            run.append(i)
        else:
            at, run = at + (run if len(run) < 3 else []), []
    return at + (run if len(run) < 3 else [])


def _slow_frames(ms: list) -> tuple[int, int]:
    """How many of these frames are slow, and of how many."""
    return len(_slow_at(ms)), max(0, len(ms) - 2 * N)


def _burst(ms: list) -> bool:
    """The slow frames of these frames all lie within a third of them."""
    at = _slow_at(ms)
    return bool(at) and at[-1] - at[0] < len(ms) / 3


def _slow(ms) -> float:
    slow, of = _slow_frames(list(ms))
    return slow / of if of else 0.0


def _edge(ms, base: float) -> int:
    """The number of slow frames in a run when that is the edge of the limit: their share is over the share before
    the step (`base`) by EDGE and by three times what chance does to a share in a run of this length (never under
    three frames). Else 0."""
    slow, of = _slow_frames(list(ms))
    if of < N:
        return 0
    chance = 3.0 * (max(base, 1.0 / of) * (1.0 - base) / of) ** 0.5
    return slow if slow / of > base + max(EDGE, chance) else 0


def _jumped(ms: list, line: float, clear: float) -> float | None:
    """The frame rate of the last 2 N of these frames when it is far over `line` (SHIFT: no wobble does that) and both
    halves of them say the same (a rate that is part old frames, part new is the pace of nothing); else None."""
    if len(ms) < 2 * N:
        return None
    early, late = _fps(ms[-2 * N:-N]), _fps(ms[-N:])
    return _fps(ms[-2 * N:]) if min(early, late) > line * (1 + SHIFT) and abs(late - early) <= clear * late else None


def goal(st: State, cfg: Cfg) -> float:
    """The frame rate that has to be delivered now."""
    return _goal_at(st.ref, cfg)


def _goal_at(ref: float | None, cfg: Cfg) -> float:
    """... were `ref` what the full limit gives."""
    return (ref if cfg.auto_fps and ref is not None and ref < cfg.target * REACHED else cfg.target) * (1 - TOL)


def _need(st: State, cfg: Cfg) -> float:
    """The pace the held limit has to give: your target less TOL. When the band is under that (a target out of reach
    with Auto FPS on): the goal of when the band was taken, from what the full limit gave then (the band's lower edge
    over the ratio). A pace that falls from the band and stays over that goal is no loss, whatever
    its cause: of 24 falls of under 5 % that got the full limit on the Claw (2026-11-16), 15 were the game's own, two
    changes of the limit each; the 9 others were 2 to 7 % under what the full limit gave."""
    if not cfg.auto_fps or st.lo is None or st.lo >= cfg.target * (1 - TOL):
        return cfg.target * (1 - TOL)
    if not st.ratio:
        return st.lo
    return min(st.lo, _goal_at(st.lo / st.ratio, cfg) * (1 + SUSTAINED))     # (answered at that goal, not under it)


def out_of_reach(st: State, cfg: Cfg) -> bool:
    """Auto FPS: the full limit gives less than the target, so what it gives is held instead."""
    return cfg.auto_fps and st.ref is not None and st.ref < cfg.target * REACHED


def _take(st: State, pace: float) -> None:
    """The band takes a pace in; one that would make it wider than SHIFT starts a new band (the one before is kept:
    the game may come back to it)."""
    if st.lo is not None and max(st.hi, pace) <= min(st.lo, pace) * (1 + SHIFT):
        st.lo, st.hi = min(st.lo, pace), max(st.hi, pace)
    else:
        st.was = (st.lo, st.hi, st.ratio, st.ref, st.t, st.band_at, st.free_w) if st.lo is not None else st.was
        st.lo, st.hi, st.band_at = pace, pace, st.t


def step(st: State, obs: Obs, cfg: Cfg) -> Action:
    t = obs.t
    dt = max(0.0, t - st.t) if st.t is not None else 0.0
    st.t = t
    if obs.cap_w is not None and obs.cap_w < cfg.floor:
        cfg = replace(cfg, floor=int(obs.cap_w))     # the guard's cap is under your floor: the guard wins
    top = cfg.ceiling if obs.cap_w is None else int(max(cfg.floor, min(cfg.ceiling, obs.cap_w)))
    act = Action(look_s=LOOK_S if st.mode == "hold" else WATCH_S)

    def write(watts: int, note: str) -> None:
        watts = int(max(cfg.floor, min(top, watts)))
        if watts != st.limit:
            st.free_w = watts if st.free_w == st.limit and watts < st.limit else None
            st.limit, st.changed_at, act.limit_w = watts, t, watts
            st.bound, st.seen_hi = False, None
            st.lo = st.hi = st.ratio = st.was = None             # nothing is known at this limit yet
        act.note = note

    def back(note: str) -> None:
        write(st.prev, note)                         # the limit before the write being watched, and what was known there
        st.lo, st.hi, st.ratio, st.was, st.seen_hi, st.free_w = st.kept

    def watch(mode: str, watts: int, note: str, why: str | None = None) -> None:
        if mode == "back":
            back(note)
        else:
            st.kept = (st.lo, st.hi, st.ratio, st.was, st.seen_hi, st.free_w)
        st.mode, st.prev, st.since, st.wrote_at, st.watch, st.why = mode, st.limit, t, t, [], why
        st.free_fps.clear()
        st.free_run = 0
        if mode != "back":
            write(watts, note)
        act.look_s = WATCH_S

    def hold(quiet_s: float = HOLD_S) -> None:
        st.mode, st.quiet_until = "hold", t + quiet_s
        act.look_s = LOOK_S

    def failed(watts: int | None, ref: float | None, slow: bool = False) -> None:
        if watts is None:
            return
        ref = min(ref, cfg.target) if ref is not None else ref           # (frames over your target, as _hold's)
        was = st.fail.get(watts)
        n = was[1] + 1 if was and not _lighter(was[2], ref) else 1   # (in a lighter scene it is a first failure)
        st.fail[watts] = [t + min(RETRY_MAX_S, RETRY_S * 2 ** (n - 1)), n, ref, slow, t]
        st.miss_ref = ref
        if st.home is not None and st.home <= watts and _same(st.home_ref, ref):
            st.home = None

    for end, ms in obs.frames:
        if not ms > 0:                               # not a frame time (zero, negative, not a number)
            continue
        ms *= obs.multiplier                         # a rendered frame is `multiplier` shown ones
        st.last.append(ms)
        st.pace.append(ms)
        st.recent.append((end, ms))
        st.frame_at = end
        if st.mode != "hold" and end >= st.since + SETTLE_S:
            st.watch.append(ms)
    while st.recent and st.recent[0][0] < t - RECENT_S:
        st.recent.popleft()

    if st.top is not None and top > st.top and not obs.thermal:      # the guard gave watts back: what they give isn't
        st.ref = None                                # known, and nothing that was measured under the cap says it
        _unknown(st)                                 # (under its clock cap: when that goes, autotune.thermal_clock)
        if st.limit >= st.top:
            st.retested[st.limit] = t                # (the cap held it there: a loss at it is no dip of the game's own)
        if st.mode != "hold":
            drop(st)
    st.top = top
    if st.limit < cfg.floor:                         # the floor was raised
        write(cfg.floor, f"the floor is {cfg.floor} W")
        st.prev = max(st.prev, cfg.floor)
        if st.mode != "hold":
            drop(st)
            hold()
        return act
    if st.limit > top:                               # the guard's cap: under it at once, whatever was being watched
        write(top, f"the temperature guard allows {top} W")
        st.prev = min(st.prev, top)
        if st.mode != "hold":
            drop(st)
            hold()
        return act

    cur = _fps(st.last) if len(st.last) == N else None
    pace_ms = statistics.median(st.pace) if len(st.pace) >= N else None
    here = _fps(st.pace) if pace_ms is not None else None
    usual = st.usual or st.ref
    silent = st.frame_at is not None and t - st.frame_at > NO_FRAMES_S
    off = bool(usual) and cur is not None and cur < usual * OFF_LO
    if off and not silent and len(st.pace) == PACE_FRAMES:
        st.off_since = st.off_since if st.off_since is not None else t
        if t - st.off_since >= ADOPT_S:
            # it stayed: this is the game now (a much heavier part, a frame limit switched on, a target far from what
            # the game does). Its pace is the usual one, and nothing known before applies. Where the limit does not
            # hold the chip (a video), steps down wait as in a new scene: the game comes back at this limit
            free = obs.draw_w is not None and obs.draw_w <= st.limit - 2
            st.usual, st.off_since = here, None
            forget(st)
            if free:
                st.scene_at = t
            off = False
    elif not off:
        st.off_since = None
    if silent or cur is None or pace_ms is None or (off and st.mode != "probe"):
        # no frames, or far too few: a loading screen, a pause, or a scene the limit starves. Nothing is learned from it
        # and the timers wait. A write being watched is dropped (back to the limit before it); a lowered limit goes up
        # while too few frames still come (in a loading screen that costs watts for a moment; in a starved scene it is
        # the cure). Far too few frames in the watch of a step down are that step's loss, judged below: taken back
        # unjudged, a level that starves the game was tried again every 12 s
        if st.mode in ("probe", "recover", "check"):
            back(f"too few frames to judge on: back to {st.prev} W")
            st.retest, st.tried = False, None
            hold()
        elif st.mode == "back":
            hold()
        elif st.limit < top and not silent and cur is not None and usual and cur < usual * OFF_LO:
            write(top, f"{cur:.0f} fps at {st.limit} W: the full {top} W")
            hold()
        st.quiet_until += dt
        st.scene_at += dt
        st.free_fps.clear()                          # what comes after it is another stretch of frames
        st.free_run = 0
        st.gap_s += dt
        if silent and st.gap_s >= SHIFT_S:           # ... and of another place, maybe: its pace is of its own frames
            st.pace.clear(), st.last.clear(), st.recent.clear()
        return act
    if st.gap_s >= SHIFT_S and not obs.thermal:      # after a loading screen the place may be another one with the same
        _unknown(st)                                 # pace at this limit: nothing is known at this limit
    st.gap_s = 0.0
    # the limit can't be what holds the game back: at the full limit, with the package 2 W or more under the limit, or
    # with the pace at your target. The band takes the pace in there (the median of the last looks: one of the game's
    # own dips doesn't move it), and at the first two that pace is what the full limit gives. The pace is the last
    # PACE_FRAMES frames, the draw is this look's: when the draw falls (a loading screen, a pause) the pace is still
    # that of frames the limit did hold back. So only once every frame of the pace came in a look that was free
    free = st.limit >= top or (obs.draw_w is not None and obs.draw_w <= st.limit - 2)
    st.free_run = st.free_run + len(obs.frames) if free else 0
    free = free and st.free_run >= PACE_FRAMES
    if obs.draw_w is not None and st.limit < top:
        # the package drew all of this limit and now draws 2 W less: a lighter scene (a menu, a map over the game).
        # No step down from its frames before it has stayed (rule 3), whatever its pace
        if free and st.bound:
            st.scene_at = t
        st.bound = not free if free or obs.draw_w > st.limit - 2 else st.bound
        if st.mode == "probe" and obs.draw_w <= st.limit - 2:
            st.free_w = st.limit                     # (a step's band is of such frames too)
    met = here >= cfg.target * (1 - TOL)
    if st.mode == "hold" and (free or met) and len(st.pace) == PACE_FRAMES:
        if obs.frames:
            st.free_fps.append(here)
        if len(st.free_fps) >= FREE_LOOKS:
            level = statistics.median(st.free_fps)
            _take(st, level)
            if free:
                st.ref, st.ratio = level, 1.0
                st.full_at = t if st.limit >= top else st.full_at
                st.free_w = st.limit if st.limit < top else None
            elif st.ref is None or cfg.target > st.ref:
                st.ref = cfg.target                  # the full limit gives at least what this limit gives (of your
                                                     # target: a menu's frames over it are not the game's)
    else:
        st.free_fps.clear()
    rising = obs.draw_w is not None and st.draw_was is not None and obs.draw_w > st.draw_was + RISE_W
    st.draw_was = obs.draw_w if obs.draw_w is not None else st.draw_was
    if st.mode == "hold":
        # the median of the figures of the last 10 s: a change of the frame rate (a loss!) is in a few of them, not in
        # most
        st.noises.append(_spread(st.pace))
        st.noise = statistics.median(st.noises)
    # NOISE times what the game's own wobble does to a rate of N frames, and of PACE_FRAMES (at most four times the
    # plain thresholds)
    clear = min(4 * CLEAR, max(CLEAR, NOISE * st.noise))
    small = min(4 * SUSTAINED, max(SUSTAINED, NOISE * st.noise / (PACE_FRAMES / N) ** 0.5))
    dippy = sum(1 for d in st.dips if t - d <= DIPS_S) >= DIPS

    if st.mode == "hold":
        st.held_s[st.limit] = st.held_s.get(st.limit, 0.0) + dt
        return _hold(st, obs, cfg, act, top, cur, pace_ms, here, clear, small, dippy, free, met, watch, hold, write,
                     failed)

    # a step down (and its undo) is judged against what the full limit gave just before it: the pace at the limit
    # before, over that limit's ratio
    ratio = st.kept[2]
    full = st.before / ratio if st.before and ratio else None        # what the full limit gives, as far as is known
    scene = full or max(st.ref or 0.0, st.before or 0.0) or None     # ... or at least: the scene what fails belongs to
    scene = min(scene, cfg.target) if scene else scene               # (frames over your target are no other scene)
    want = _goal_at(full, cfg)
    if st.mode == "probe" and obs.draw_w is not None and obs.draw_w > st.limit + BIND_W and t - st.wrote_at < BIND_S:
        st.since, st.watch = t, []                   # the package still draws more than the new limit: not yet in force
        if cur < want * (1 - SHIFT) and st.prev < top and not st.retest and not dippy:
            tried, st.lost = st.limit, cur           # ... but the frames collapse meanwhile: all the watts at once
            back("")
            watch("recover", top, f"{cur:.1f} fps at {tried} W, under {want:.1f}: the full {top} W", "fall")
            st.tried = tried
        return act
    seen = t - st.since
    up = _jumped(st.watch, st.before, clear) if st.mode == "probe" and st.before else None
    if up is not None:
        # far more frames than before the step: not the scene the step was for (the game back from a video in the
        # middle of the way down took every further step, down to the floor). The step is taken back, not judged
        back(f"{up:.0f} fps at {st.limit} W, {st.before:.0f} before the step: another scene; back to {st.prev} W")
        st.retest, st.tried, st.loss_since, st.chain = False, None, None, 0
        hold()
        return act
    if st.mode == "probe":
        dwell = RETRY_DWELL_S if st.long else DWELL_S
        slow = _edge(st.watch, st.base_slow or 0.0) if seen >= DWELL_S / 2 else 0
        edge = slow > 0 and not _burst(st.watch)     # (one burst is the game's own, as at a held limit)
        # a clear loss on the last N frames; a small one on all the frames of the watch, once there are enough of them
        # to tell; at the end of the watch, the goal itself
        got = _fps(st.watch) if st.watch and (seen >= DWELL_S or (len(st.watch) >= 2 * N and not dippy)) else None
        if got is not None and dippy:
            # this game dips by itself: judged at the end of the watch only, and on what the step did to its typical
            # frame, not on a rate with a dip in it (the typical frame against the typical frame before the step: a
            # median against a rate is off by the game's own long frames, 4 % in an emulator that stutters)
            typ = statistics.median(st.watch)
            got = st.before * st.typ / typ if st.before and st.typ else 1000.0 / typ
        quick = len(st.watch) >= N // 2 and cur < want * (1 - clear)
        if quick and dippy and not st.retest:        # this game dips by itself: only a loss that stays counts (on a
            # second try the loss was waited out once already: one that comes back is the limit's)
            st.loss_since = st.loss_since if st.loss_since is not None else t
            quick = t - st.loss_since >= PATIENT_S
        elif not quick:
            st.loss_since = None
        margin = small if seen < DWELL_S else 0.0
        # a step down has to leave CLEAR over the goal: a level with less has, in the game's own wobble, losses of
        # the size that counts (a walk at 45: seconds under the goal at the lowest level that held it; in the fuzz
        # three times the frames lost). Unless it gives what the full limit gives (the limit before, where that is
        # not known): nothing is lost to the limit then, whatever the rate (a game on its own frame limit with slow
        # frames of its own is under that line at every limit)
        most = full or st.before
        line = want * (1 + CLEAR) if got is not None and most and got < most * (1 - small) else want
        lost = quick or (got is not None and got < line * (1 - margin))
        if lost and cur < want * (1 - SHIFT) and st.prev < top and not st.retest:
            # far more lost than a step of this size usually costs: the scene may have changed under the step. All
            # the watts at once; what the full limit gives then says which it was
            tried, st.lost, st.loss_since = st.limit, cur, None
            back("")                                 # the limit to go back to afterwards is the one before the step
            watch("recover", top, f"{cur:.1f} fps at {tried} W, under {want:.1f}: the full {top} W", "fall")
            st.tried = tried
        elif lost or edge:
            why = f"{got if got is not None and got < line else cur:.1f} fps, under {line:.1f}" if lost else f"{slow} slow frames"
            tried, before = st.limit, st.prev
            st.retest = False                        # (a loss that was waited out is waited for no more at the limit before)
            failed(tried, scene, edge and not lost)
            st.misses += 1
            watch("back", before, f"{tried} W: {why}; back to {before} W")
        elif st.loss_since is not None:
            pass                                     # a loss that is being waited out: the watch ends when it has
        elif seen >= dwell and st.retest:
            # the level that lost frames holds on its second try: the loss was the game's own
            st.retest, st.loss_since = False, None
            st.dips.append(t)
            _take(st, _fps(st.watch))
            st.ratio = min(1.0, ratio * _fps(st.watch) / st.before) if ratio and st.before else None
            act.note = f"{st.limit} W holds again: the game's own dip"
            hold(HOLD_S)
        elif seen >= dwell:
            st.misses, st.loss_since, st.chain = 0, None, st.chain + 1
            held = _fps(st.watch)
            # what this limit gives against the full one is from the step. More frames than before the step: the full
            # limit gives at least as many, so the ratio is at most 1
            st.lo, st.hi, st.ratio = held, held, (min(1.0, ratio * held / st.before) if ratio and st.before else None)
            st.band_at = t
            if st.free_w == st.limit and obs.draw_w is not None and obs.draw_w > st.limit - 0.5:
                st.free_w = None                     # (one look of the watch under it: the package draws the limit now)
            nxt = _next_down(st, obs, cfg, t, scene)
            rate = got if dippy and got is not None else held        # (a game that dips: on its typical frame)
            if st.before and rate > st.before * (1 + max(ABOVE, small)) and not obs.thermal:
                # more frames than before the step: a lower limit gives no more frames, so the scene changed under
                # the step, and what the step costs is not in its frames (three steps held on such a ratio, 9 % under
                # the full limit for 17 s). The full limit says it
                st.chain, st.lost, st.lost_slow, was = 0, held, 0.0, st.limit
                watch("check", top, f"{held:.0f} fps at {was} W, {st.before:.0f} before the step: "
                                    f"checking what the full {top} W gives", "rise")
            elif st.chain >= CHAIN:
                st.chain = 0
                act.note = f"holding at {st.limit} W"
                hold(AFTER_LOSS_S)
            elif nxt is not None and _steady(st, t, clear):
                was, st.before, st.long = st.limit, held, _slow_before(st, nxt, scene, t)
                st.typ = statistics.median(st.watch)
                watch("probe", nxt, f"{was} W holds; trying {nxt} W")
            else:
                act.note = f"holding at {st.limit} W"
                hold(0.0 if nxt is None else HOLD_S)
        return act

    if st.mode == "back":                            # a failed step was undone: are the frames back?
        tail = st.watch[-N:]
        if len(st.watch) < N or seen < BACK_S / 2:
            return act
        low = st.limit < top and (
            _fps(tail) < want * (1 - clear) or (seen >= BACK_S and not dippy and _fps(st.watch) < want * (1 - small)))
        if low and dippy:                            # a game that dips by itself: only a loss that stays counts (the
            st.loss_since = st.loss_since if st.loss_since is not None else t    # time the step waited counts too)
            low = t - st.loss_since >= PATIENT_S
        elif not low:
            st.loss_since = None
        if low:
            st.lost, was, st.held_for, st.loss_since = _fps(tail), st.limit, None, None
            watch("recover", top, f"{st.lost:.1f} fps at {was} W, under {want:.1f}: the full {top} W", "fall")
        elif seen >= BACK_S and st.loss_since is None:
            hold(min(AFTER_LOSS_MAX_S, AFTER_LOSS_S * 2 ** max(0, st.misses - 1)))
        return act

    # recover / check: the full limit, after a loss, for slow frames, or to measure what it gives (no target, nothing
    # known)
    if seen < BACK_S or (len(st.watch) < 3 * N and seen < 3 * BACK_S):
        return act                                   # a second, and 3 N frames of it (a 20 fps game takes longer)
    if len(st.watch) < N // 2:                       # no judgement possible: stay where it is
        hold()
        return act
    if rising and t - st.wrote_at < BIND_S:          # the watts are still arriving: judged on the frames after that
        st.since, st.watch = t - BACK_S / 2, st.watch[-N:]
        return act
    third = len(st.watch) // 3
    if third >= N and t - st.wrote_at < 2 * BIND_S and _fps(st.watch[-third:]) > _fps(st.watch[:third]) * (1 + CLEAR):
        return act                                   # the frame rate is still climbing: not judged yet
    if (st.mode == "recover" and st.tried is None and third >= N
            and _fps(st.watch[-third:]) < _fps(st.watch[:third]) * (1 - clear)):
        # the frame rate falls at the full limit: the place gets heavier under the look, and what the limit before gave
        # when it lost is of a lighter moment. Taken for "the scene, not the limit", the limit went back, lost again at
        # once, and so on (the way into a heavier place: 8 writes in 7 s). The full limit stays; what it gives is
        # measured when the place has stayed
        act.note = f"{_fps(st.watch[-third:]):.0f} fps at {top} W and falling: a heavier place; staying at {top} W"
        st.ref = None
        hold(AFTER_LOSS_S)
        return act
    if st.why == "slow" and len(st.watch) < SLOW_FRAMES and seen < SLOW_MAX_S:
        return act                                   # a share of slow frames needs more frames than a frame rate
    now = _fps(st.watch[len(st.watch) // 2:]) if len(st.watch) >= 4 * N else _fps(st.watch)

    # what the full limit gives is the reference, and the band here; the game's own slow frames are those it has here
    was, share = st.lost or now, _slow(st.watch)
    lo, hi = st.kept[:2]
    before = st.ref
    st.ref, st.lo, st.hi, st.ratio, st.base_slow, st.full_at = now, now, now, 1.0, share, t
    want = _goal_at(now, cfg)
    if st.mode == "recover" and st.tried is not None:
        tried, st.tried = st.tried, None
        if st.before and now >= st.before * (1 - CLEAR) and st.before >= want:
            # the full limit gives what the limit before the step gave: the same scene, with a steep edge under it
            failed(tried, now)
            back(f"{tried} W lost {st.before - was:.0f} fps; back to {st.prev} W")
        else:
            # ... fewer frames than the limit before the step gave, or so many more that the limit before is
            # under their goal: the scene changed under the step, and what the step lost says nothing of this scene
            # (the level tried in the last second of an intro at the target was closed for two minutes of R1's game)
            act.note = f"another scene: {now:.0f} fps at {top} W"
        hold(AFTER_LOSS_S)
        return act
    if was >= want and not (st.why == "slow" and share < st.lost_slow - EDGE):
        # the limit before gives what the full one gives, within the margin (and has no more slow frames): the scene
        # or the game's own, not the limit. Back to it; its band takes that pace in, and its ratio is from now
        if st.mode == "recover" and st.wrote_at - st.said[0] < AFTER_LOSS_S and now < st.said[1] * (1 - clear):
            # ... unless this loss came within AFTER_LOSS_S of the last such answer and the full limit gives less than
            # it gave then: the place is still getting heavier (too slowly to show in one look), and each way back
            # loses again. (The same frame rate as then is a place that has stayed: a video that began between two
            # looks loses twice, the second time against the band of its first frames)
            act.note = f"{now:.0f} fps at {top} W, {st.said[1]:.0f} at the last look: a heavier place; staying at {top} W"
            hold(AFTER_LOSS_S)
            return act
        st.said = (t, now) if st.mode == "recover" else st.said
        back(f"the full {top} W gives {now:.0f} fps: the scene, not the limit; back to {st.prev} W" if st.mode == "recover"
             else f"{top} W gives {now:.0f} fps, {st.prev} W {was:.0f}: back to {st.prev} W")
        st.lo, st.hi, st.ref = lo, hi, before
        if st.lo is not None and before is not None and abs(now - before) > 2 * small * before:
            # the full limit gives another frame rate than the last time: another place. Its band is from now alone
            # (taken into the band before, a slower place widened it, and a loss of 10 % in the place after was in
            # it). The one before is kept, with its reference, when it is the faster one or had stood for SCENE_S (the
            # game under a menu): a pace that falls into the band of a short visit to a slower place is a loss until
            # the full limit says otherwise (another place, where the last watts count for more, has that pace too)
            keep = now < before or t - st.band_at >= SCENE_S
            st.was = (st.lo, st.hi, st.ratio, st.ref, t, st.band_at, st.free_w) if keep else None
            st.lo, st.hi, st.band_at, st.free_w = was, was, t, None
        else:
            _take(st, was)
        st.ratio, st.ref, st.seen_hi, st.seen_at = min(1.0, was / now), now, was, t
        if st.why == "slow":
            st.base_slow = max(st.lost_slow, share)  # as many at the full limit: the game's own in this place
        hold(AFTER_LOSS_S)
    elif (st.mode == "recover" and ((st.held_for or 0.0) >= HOME_S or dippy)
          and (st.why != "free" or before is not None and abs(now - before) <= 2 * small * before)
          and t - st.retested.get(st.prev, -1e9) >= RETRY_S):
        # the frames are back at the full limit. But the level had held for a while, or this game dips by itself: a
        # dip of the game's own looks the same. The level once more, watched; a loss that comes back is the limit's
        level = st.prev
        st.retested[level] = t
        st.before, st.retest, st.loss_since, st.long = now, True, None, False
        st.typ = statistics.median(st.watch[len(st.watch) // 2:] if len(st.watch) >= 4 * N else st.watch)
        watch("probe", level, f"{was:.0f} fps at {level} W, {now:.0f} at {top} W: {level} W once more")
    else:
        if st.why != "free":
            failed(st.prev, now, was >= want)
        act.note = (f"{top} W has none of the slow frames of {st.prev} W: staying up" if was >= want
                    else f"{st.prev} W was too low ({was:.0f} fps, {now:.0f} at {top} W)" if st.mode == "recover"
                    else f"{top} W gives {now:.0f} fps, {st.prev} W gave {was:.0f}: staying up")
        hold(AFTER_LOSS_S)
    return act


def _steady(st: State, t: float, clear: float) -> bool:
    """The frame rate of each half second of the last STEADY_S is within the game's own wobble (`clear`) of their
    median: the pace a step down is judged against is the pace of now (frame times may be uneven; the rate may not
    have moved. The median of PACE_FRAMES frames is two seconds late at 30 fps)."""
    slots: dict = {}
    for end, ms in st.recent:
        if end >= t - STEADY_S:
            n, total = slots.get(int((t - end) * 2), (0, 0.0))
            slots[int((t - end) * 2)] = (n + 1, total + ms)
    rates = [1000.0 * n / total for n, total in slots.values() if n >= 3]
    if len(rates) < STEADY_S * 2 - 1:
        return False
    mid = statistics.median(rates)
    return max(abs(r - mid) for r in rates) <= clear * mid


def _same(then: float | None, now: float | None) -> bool:
    """What the full limit gives now is within ABOVE of what it gave then: the scene a level held in."""
    return then is None or now is None or abs(now - then) <= ABOVE * then


def _lighter(then: float | None, now: float | None) -> bool:
    """The full limit gives so much more than it did then that this is another scene (SHIFT, as in the scene rule): a
    level that failed then may hold now. A game whose own frame rate moves by less, for seconds and at every limit,
    is one scene: what failed in its slow stretch failed. (When the full limit gives less, a video or a heavier place,
    what failed still counts: the game is back at the limit it needs.)"""
    return then is not None and now is not None and now > then * (1 + SHIFT)


def _counts(entry: list, ref: float | None, t: float) -> bool:
    """A level that failed still counts: in the scene it failed in; where the full limit gives a little more (under
    SHIFT: the same game in a lighter stretch), until its time for another try; in a heavier or slower one (a video,
    a cutscene: the game comes back to the limit it needs) for RETRY_MAX_S. Not in another, lighter scene: it may
    hold there."""
    if _lighter(entry[2], ref):
        return False
    if _same(entry[2], ref):
        return True
    return t < entry[0] if ref > entry[2] else t - entry[4] < RETRY_MAX_S


def _slow_before(st: State, watts: int, ref: float | None, t: float) -> bool:
    """This level failed for its slow frames, and that still counts."""
    return watts in st.fail and st.fail[watts][3] and _counts(st.fail[watts], ref, t)


def _next_down(st: State, obs: Obs, cfg: Cfg, t: float, ref: float | None) -> int | None:
    """The next limit to try under this one, or None (`ref`: what the full limit gives now, as far as is known): the
    level that held in this scene for HOME_S, when it is over every level that failed; else halfway to the highest
    level under this one that failed, and that level itself only when it is the next one down, its time is up, and
    this is the scene it failed in (a try in a video says nothing of the game); with none, STEP_W down or straight to
    just over what the package draws."""
    under = [w for w in st.fail if w < st.limit and _counts(st.fail[w], ref, t)]
    if (st.home is not None and st.home < st.limit - STEP_W and st.home_ref is not None and ref is not None
            and _same(st.home_ref, ref) and all(w < st.home for w in under if _same(st.fail[w][2], ref))):
        nxt = st.home                                # (held in this scene: what failed over it failed in another)
    elif under:
        nxt = (st.limit + max(under) + 1) // 2
        if nxt >= st.limit:
            if st.fail[max(under)][0] > t or not _same(st.fail[max(under)][2], ref):
                return None
            nxt = max(under)
    else:
        nxt = st.limit - STEP_W
        if obs.draw_w is not None and obs.draw_w + 2 < nxt:
            nxt = int(-(-obs.draw_w // 1)) + 2       # a limit the chip doesn't reach changes nothing
    nxt = max(cfg.floor, nxt)
    return nxt if nxt < st.limit else None


def _hold(st, obs, cfg, act, top, cur, pace_ms, here, clear, small, dippy, free, met, watch, hold, write,
          failed) -> Action:
    t = st.t
    full = len(st.pace) == PACE_FRAMES
    # the scene: the pace SHIFT away for SHIFT_S is another one, and no step down is tried in it before it has stayed
    # SCENE_S (a menu or a map over the game, a video); then its pace is the usual one
    if full and st.scene_fps is None:
        st.scene_fps = st.usual = here
    elif full and abs(min(here, cfg.target) - min(st.scene_fps, cfg.target)) >= SHIFT * min(
            st.scene_fps, cfg.target):                   # (frames over your target are no other scene)
        st.scene_since = st.scene_since if st.scene_since is not None else t
        if t - st.scene_since >= SHIFT_S:
            st.scene_fps, st.scene_since, st.scene_at = here, None, t
    elif full:
        st.scene_since = None
    if st.scene_fps is not None and t - st.scene_at >= SCENE_S and abs(st.scene_fps - st.usual) >= SHIFT * st.usual:
        st.usual, st.misses = st.scene_fps, 0

    need = _need(st, cfg)

    # back in the band this limit had before a menu, a map or a video: what was known there is known again. Seen on
    # the last 2 N frames when both halves say the same; while they don't and the last frames are on the way to that
    # band, nothing is judged (the first look after a menu has frames of both)
    if st.was is not None and t - st.was[4] >= SCENE_S:
        st.was = None                                # (after that long the same pace may be another place's: in one
                                                     # where the last watts count for more it is a loss)
    if st.was is not None and not st.lo * (1 - clear) <= cur <= st.hi * (1 + clear):
        early = _fps(list(st.pace)[-2 * N:-N]) if len(st.pace) >= 2 * N else None
        if early is None or abs(cur - early) > clear * cur:
            if min(st.was[0], st.lo) * (1 - clear) <= cur <= max(st.was[1], st.hi) * (1 + clear):
                return act
        elif st.was[0] * (1 - clear) <= cur <= st.was[1] * (1 + clear):
            # (once: the band left is not kept, or a video's band was there for a heavier place of the game that has
            # the video's frame rate. And a band that was a video's, taken with the package under the limit, is one
            # still: for a place that has its frame rate and draws the limit, nothing is known, as below)
            menu = st.free_w == st.limit
            (st.lo, st.hi, st.ratio, st.ref, _, st.band_at, st.free_w), st.was = st.was, None
            if menu:
                st.full_at = -1e9                    # (the place after a menu may be another one with this pace: where
                                                     # the goal is what the full limit gives, that is asked once)
            need = _need(st, cfg)

    # the band was taken while the package drew 2 W under this limit (a video, a light place), and now the package
    # draws all of it: another scene, which the limit may hold back at the same frame rate. Nothing is known here, and
    # a clear fall gets the full limit at once (in a game that dips by itself, once it has lasted PATIENT_S, below). The
    # limit held no game here: it is not booked as failed, and is tried once more only where the full limit gives the
    # pace it gave before (a dip of the game's own)
    if st.free_w == st.limit and obs.draw_w is not None and obs.draw_w > st.limit - 0.5:
        _unknown(st)
        need = _need(st, cfg)
        if cur < need * (1 - clear) and st.limit < top and not dippy:
            st.lost, st.held_for, st.loss_since = cur, t - st.changed_at, None
            watch("recover", top, f"{cur:.1f} fps at {st.limit} W, under {need:.1f}: the full {top} W", "free")
        return act

    # a fall: the last frames are clearly under what this limit has to give, or the pace is a little under it and
    # stays. The full limit at once; what it gives says whether it was the limit
    # (the pace says something of this limit when all its frames came after the last write: at 20 fps they take 3 s)
    # ... and when both halves of them say the same: a pace that is part one scene, part the next is the pace of neither
    settled = full and t - st.changed_at >= max(DWELL_S, PACE_FRAMES * pace_ms / 1000.0 + SETTLE_S)
    if settled:
        frames = list(st.pace)
        first, second = _fps(frames[:PACE_FRAMES // 2]), _fps(frames[PACE_FRAMES // 2:])
        settled = abs(second - first) <= 2 * small * here
    sustained = settled and here < need * (1 - small)
    if st.lo is None and st.limit < top and obs.draw_w is not None and obs.draw_w <= st.limit - 2:
        return act                                   # nothing is known here and the package does not use the limit:
                                                     # it holds nothing back, and the band comes from these frames
    if cur < need * (1 - clear):
        if free or st.limit >= top:                  # the limit can't be the cause, and there is nothing more to give
            st.free_loss = st.free_loss if st.free_loss is not None else t
            return act
        if dippy:                                    # this game dips by itself: only a loss that stays counts
            st.loss_since = st.loss_since if st.loss_since is not None else t
            if t - st.loss_since < PATIENT_S:
                return act
        st.lost, was_at, st.held_for, st.loss_since = cur, st.limit, t - st.changed_at, None
        watch("recover", top, f"{st.lost:.1f} fps at {was_at} W, under {need:.1f}: the full {top} W", "fall")
        return act
    # (a dip is booked from RECENT_S after the last write on: the wobble that says what a clear loss is, is the
    # median of that long, and in a place that wobbles more than the last one every wobble looked like a dip)
    if st.loss_since is not None and _fps(list(st.pace)[-2 * N:]) >= need * (1 - small):
        if st.loss_since - st.changed_at >= RECENT_S:
            st.dips.append(t)                        # it passed by itself (the last 2 N frames are back): one more of
        st.loss_since = None                         # the game's own dips. A loss at the edge of "clear" comes and goes
    if st.free_loss is not None and (_fps(list(st.pace)[-2 * N:]) >= need * (1 - small) or t - st.free_loss > DIP_MAX_S):
        # over (the last 2 N frames are back), or it stays: a dip of the game's own if it was short, else another scene
        if t - st.free_loss <= DIP_MAX_S and st.free_loss - st.changed_at >= RECENT_S:
            st.dips.append(t)
        st.free_loss = None
    if sustained and dippy and st.limit < top:       # as above: a second of it, not one dip's worth of frames
        st.small_since = st.small_since if st.small_since is not None else t
        sustained = t - st.small_since >= PATIENT_S + PACE_FRAMES * pace_ms / 1000.0
    elif not sustained:
        st.small_since = None
    if sustained:
        st.small_since = None
        if st.limit < top and not free and not obs.thermal:      # (under the guard's clock cap a little less is the cap's)
            st.lost, was_at, st.held_for, st.loss_since = here, st.limit, t - st.changed_at, None
            watch("recover", top, f"{here:.1f} fps at {was_at} W, a little under {need:.1f}: the full {top} W", "fall")
        return act

    # what the full limit gives now, as far as is known: the pace here over this limit's ratio. The pace is at least
    # the band's lower edge (a little less is the game's wobble, and more than a little is a loss, above): a step down
    # is not judged against a moment's low
    known = max(here, st.lo) if st.lo is not None else here
    ref = known / st.ratio if st.ratio else max(st.ref or 0.0, known)
    ref = min(ref, cfg.target)                                       # (frames over your target are no other scene)
    # the steps that failed in a row were another scene's: this one is lighter by SHIFT, or it is the one a level held
    # in for HOME_S (the place that drove the limit out is over)
    if st.misses and (_lighter(st.miss_ref, ref) or (
            st.home is not None and _same(st.home_ref, ref) and not _same(st.miss_ref, ref))):
        st.misses, st.quiet_until = 0, min(st.quiet_until, st.changed_at + HOLD_S)

    # more frames than the band has: the full limit may give more of them too
    over = settled and st.hi is not None and here > st.hi * (1 + max(ABOVE, 2 * small))
    # (the first frames of another scene are not the scene the limit held in)
    if st.limit < top and settled and not over and t - st.changed_at >= HOME_S and st.lo is not None and (
            st.home is None or not _same(st.home_ref, ref) or st.limit <= st.home):
        st.home, st.home_ref = st.limit, ref

    binds = obs.draw_w is None or obs.draw_w > st.limit - 2
    mine = [ms for end, ms in st.recent if end >= st.changed_at + SETTLE_S]
    if st.limit < top and not obs.thermal and full:  # (under the guard's clock cap, what is slow is the cap's)
        # slow frames at a held limit (the median holds, a few frames don't: slow against the pace of their own time,
        # and only while that pace stands still), EDGE more of them than before the steps and not in one burst (those
        # are the game's): the edge of what the game needs. A watt more, STEP_W times in RETRY_S. Slow frames at that
        # level too: the edge is not near, or they are the game's own. The full limit says which
        # (the watt more is judged when it has had the frames a share needs)
        waited = (not st.edged or st.changed_at > st.edged[-1] or len(mine) >= SLOW_FRAMES
                  or t - st.changed_at >= SLOW_MAX_S)
        if (settled and waited and len(mine) >= 3 * N and st.base_slow is not None and _edge(mine, st.base_slow)
                and not _burst(mine)):
            was = st.limit
            if was + 1 < top and (len(st.edged) < STEP_W or t - st.edged[0] >= RETRY_S):
                failed(was, ref, True)
                st.edged.append(t)
                band = st.lo, st.hi, st.ratio        # (a watt more gives these frames too)
                write(was + 1, f"slow frames at {was} W: {was + 1} W")
                st.lo, st.hi, st.ratio = band
                hold(AFTER_LOSS_S)
            else:
                st.lost, st.lost_slow = here, _slow(mine)
                watch("check", top, f"slow frames at {was} W: checking what the full {top} W gives", "slow")
            return act
        # more frames than the band has, under your target, at a limit that holds the chip: another place, or a lighter
        # one. What the band says of the full limit (no more frames than here, within the margin) says nothing there.
        # Seen as a loss is: far more on the last 2 N frames of this limit (the game back from a video at the limit
        # the video needed); a little more on the pace
        tail = _jumped(mine, st.hi, clear) if st.hi is not None else None
        jump = tail is not None and tail < cfg.target * (1 - TOL)
        if st.seen_hi is not None and here >= st.seen_hi * (1 - ABOVE):
            st.seen_at = t
        if binds and (jump or (over and not met)):
            pace = tail if jump else here
            if st.seen_hi is not None and t - st.seen_at < SCENE_S and pace <= st.seen_hi * (1 + ABOVE):
                _take(st, pace)                      # (so many frames were seen at this limit, and the full limit gave
            else:                                    # no more)
                st.lost, st.lost_slow = pace, 0.0
                watch("check", top, f"checking what the full {top} W gives", "rise")
                return act
        # the goal hangs on what the full limit gave (your target is out of reach), and that is CHECK_MAX_S old or
        # from before a menu: a scene that got lighter under a step, or another place that has this pace at this
        # limit, shows in no frame here
        if (settled and binds and st.free_w != st.limit and st.limit < top - 1 and need < cfg.target * (1 - TOL)
                and (st.full_at is None or t - st.full_at >= CHECK_MAX_S)):
            st.lost, st.lost_slow = here, 0.0
            watch("check", top, f"the full {top} W was not measured yet: checking what it gives" if st.full_at is None
                  else f"the full {top} W was measured before a menu: checking what it gives" if st.full_at < 0
                  else f"{CHECK_MAX_S / 60:.0f} min since the full {top} W was measured: checking what it gives", "rise")
            return act

    # a step down: from a pace that is in the band, that the last frames agree with, in a scene that has stayed
    # (the scene a level held in for HOME_S is no new one: back in it, that level without the wait)
    if (t >= st.quiet_until and t - st.changed_at >= AFTER_LOSS_S
            and st.scene_since is None and st.loss_since is None and st.lo is not None and not over
            and abs(cur - here) <= clear * here and _steady(st, t, clear)):
        nxt = _next_down(st, obs, cfg, t, ref)
        if nxt is not None and (t - st.scene_at >= SCENE_S or (nxt == st.home and _same(st.home_ref, ref))):
            ms = [x for _, x in st.recent]
            share = _slow(ms)
            st.base_slow = share if st.limit >= top or st.base_slow is None else min(st.base_slow, share)
            st.before, st.typ, st.chain, st.long = known, pace_ms, 0, _slow_before(st, nxt, ref, t)
            was = st.limit
            watch("probe", nxt, f"{cur:.0f} fps holds at {was} W; trying {nxt} W")
    return act
