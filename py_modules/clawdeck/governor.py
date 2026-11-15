"""The Autotune governor: from the frames of the last moments, the next power limit.

One question, asked all the time: would more watts, up to your limit, give frames it has to deliver?
  no  -> try fewer: a step down, watched for DWELL_S on every frame and taken back at the first loss;
  yes -> all of them at once (the full limit), then down again between the watts that failed and the watts that hold.

Why this shape (Claw 8 EX, 2026-11-14/15, docs/HARDWARE-FINDINGS):
  - frames answer a new limit within 0.15-0.35 s and are back 0.25 s after a raise, so a step down can be tried and
    undone before it is seen: 46 such steps during play cost about 0.6 s of at most 10 % slower frames each;
  - only that answer says why frames are slow. An emulator that spins (RPCS3) draws the whole limit at every limit and
    keeps the chip's "limited by power" flag set while its frames hold; a heavy scene and a limit that is too low look
    the same in the frames;
  - the watts a game needs move fast (under 14 W to over 30 W within 20 s), so what was found is where to start, never
    a result.

step(state, obs, cfg) has no clock and no I/O: the same frames give the same limits, on a Claw, in a test and in
tools/autotune_sim.py. It changes `state` in place and returns what to do.

Words:
  pace       the frame time the game holds: the median of the last PACE_FRAMES frames;
  reference  the frame rate at a moment the limit was known not to cost anything: at the full limit, or with the
             package 2 W or more under the limit, or with the chip saying it is not limited by power;
  goal       what has to be delivered: your target (or the reference when that is lower and Auto FPS is on) less
             TOL; with no target, the reference less the keep margin;
  fail       watts that lost frames, each with the time it may be tried again.
"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field

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
KEEP_FPS = 2.0          # most-frames mode: within keep % or this many fps of the reference, whichever is more
SLOW = 1.10             # a frame this much longer than the pace is a slow one ...
EDGE = 0.03             # ... and this much more of them than before a step is the edge of the limit (15 W in
                        # Midnight Club: Los Angeles: the median on pace, 6 % slow frames)
EDGE_MIN = 3            # ... when at least this many
SETTLE_S = 0.15         # frames that end this soon after a write still belong to the limit before
DWELL_S = 2.5           # a step down is watched this long
BACK_S = 1.0            # a raise is judged on the frames of this long
HOLD_S = 10.0           # no step down sooner after a change ...
AFTER_LOSS_S = 5.0      # ... or after a loss was answered; doubled for each step down in a row that failed,
AFTER_LOSS_MAX_S = 300.0  # up to this (a success, or another scene, starts it over)
HOME_S = 20.0           # a limit held this long is where to go back to when the scene that drove it out is over
HOME_FOR_S = 600.0      # ... for this long (a higher limit held meanwhile doesn't replace it)
STEP_W = 2
CHAIN = 6               # steps down in a row; then a pause of AFTER_LOSS_S in which the rules of holding run
EDGE_UP_S = 10.0        # the edge: +1 W at most this often
RETRY_S, RETRY_MAX_S = 120.0, 600.0    # a failed level may be tried again after this; doubled at each further failure
CHECK_S, CHECK_MAX_S = 60.0, 600.0     # the reference is checked at the full limit: not more often than CHECK_S, when the
CHECK_MOVED = 0.03                     # frame rate at the held limit moved this much since it was taken; else every
                                       # CHECK_MAX_S
CHECK_FIRST_S = 1.0     # ... and this soon after steps down from the full limit: the scene may have changed meanwhile
ABOVE, ABOVE_S = 0.05, 5.0             # a held limit gives this much MORE than the full limit is believed to give: the
                                       # belief is wrong (a cutscene or a menu set it); looked at at once, this often
CONFIRM_S = 20.0        # the full limit gave SHIFT less than before (a cutscene, a video, a heavier place): no step down
REACH_S, REACH_FOR_S = 30.0, 120.0     # for this long, and the full limit is looked at again every REACH_S for REACH_FOR_S
STEADY, STEADY_S = 0.15, 3.0           # a step down starts only while the frame rate of each half second of the last
                                       # STEADY_S is within STEADY of their median
FREE_LOOKS = 5          # the reference is the median pace of the last looks at a limit that can't hold the game back,
                        # at least this many: one of the game's own dips doesn't move it
BIND_S, BIND_W = 3.0, 2.5   # a step down is watched from when the package draws less than BIND_W over the new limit
                        # (PL2 allows 2 W over it): a cut made while the chip ran over its limit took 2.3 s to bind. A
RISE_W = 0.3            # raise is judged when the draw has stopped rising by this much a look. Both at most BIND_S late
SHIFT, SHIFT_S = 0.15, 5.0             # the pace moved this much for this long at one limit: another scene
OFF_LO, OFF_HI = 0.35, 2.5             # frames this far under or over the usual rate are a loading screen, a menu, a map
                                       # or a pause: not judged ...
ADOPT_S = 5.0           # ... unless they stay this long: then that is the game now
NO_FRAMES_S = 1.5
RECENT_S = 10.0
LOOK_S, WATCH_S = 0.5, 0.1             # how soon to look again: holding, and while a write is being watched


@dataclass
class Cfg:
    floor: int
    ceiling: int                    # your limit
    target: float | None = None     # fps; None: the most frames your limit gives, within keep_pct
    keep_pct: float = 5.0
    auto_fps: bool = True           # a target out of reach: hold what the full limit gives instead
    start_w: int | None = None      # where this game held last time; None: mid-range


@dataclass
class Obs:
    t: float                                    # now (s, any steady clock)
    frames: list = field(default_factory=list)  # (end time s, frame time ms) since the last step, oldest first
    draw_w: float | None = None                 # package power since the last step
    clipped: bool | None = None                 # the chip says the limit holds it back; None: it doesn't say
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
    fail: dict = field(default_factory=dict)        # watts -> [may be tried again at, failures]
    misses: int = 0                 # steps down in a row that failed
    ref: float | None = None        # the reference, fps
    ref_at: float = -1e9
    ref_here: float | None = None   # the frame rate at the held limit when the reference was last known to be true
    ref_drop_at: float = -1e9       # when the full limit last gave SHIFT less than the reference before
    drop_floor: int | None = None   # ... and the limit that held before it: no step under it on that lower reference
    ref_high: float | None = None   # ... and the reference before it
    usual: float | None = None      # the game's frame rate as last judged (what "a loading screen or a menu" is far from)
    free_fps: deque = field(default_factory=lambda: deque(maxlen=2 * FREE_LOOKS))   # the pace at the last free looks
    draw_was: float | None = None   # the package draw at the last look, and when a watched write was made
    wrote_at: float = -1e9
    off_since: float | None = None  # since when the frames are far from it
    noise: float = 0.0              # how much the game's rate of N frames wobbles by itself (relative), from the pace
    noises: deque = field(default_factory=lambda: deque(maxlen=21))
    dips: deque = field(default_factory=lambda: deque(maxlen=8))   # when it lost frames at a limit that can't be the cause
    loss_since: float | None = None # a loss seen in a game that dips by itself: since when
    small_since: float | None = None
    free_loss: float | None = None  # a loss at a limit that can't be its cause: since when (a dip if it is over soon)
    held_for: float | None = None   # how long the limit had held when a loss sent it to the full limit
    retested: dict = field(default_factory=dict)    # watts -> when a level that lost frames was last tried once more
    retest: bool = False            # the step being watched is such a second try
    chain: int = 0                  # steps down in a row since the last hold
    verify: bool = False            # steps down began at the full limit: the reference is checked when they end
    safe: int | None = None         # the limit the last step down that held began from
    top: int | None = None          # the most that was allowed at the last step (your limit, or the guard's cap)
    lost: float | None = None       # the frame rate before a raise, fps
    before: float | None = None     # the frame rate before the step down being watched, fps
    tried: int | None = None        # a step down that lost far more than its size explains (the loss is its, not prev's)
    good: int | None = None         # the last limit under the full one that held
    home: int | None = None         # the last one that held for HOME_S: tried first when a heavier scene is over
    home_at: float = -1e9
    base_slow: float | None = None  # share of slow frames before a step down (the game's own); None: not taken yet
    edge_at: float = -1e9
    frame_at: float | None = None   # when the last frame ended
    shift_fps: float | None = None  # the frame rate this hold began with, and since when it is SHIFT away from it
    shift_since: float | None = None
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


def _slow(ms, pace_ms: float) -> float:
    ms = list(ms)
    return sum(1 for x in ms if x > pace_ms * SLOW) / len(ms) if ms else 0.0


def _edge(ms, base: float) -> int:
    """The number of slow frames in a run (more than SLOW over the run's own median) when that is the edge of the limit:
    at least EDGE_MIN of them, and their share over the share before the step (`base`) by EDGE and by three times what
    chance does to a share in a run of this length. Else 0."""
    ms = list(ms)
    if len(ms) < 2 * N:
        return 0
    mid = 1000.0 / _fps(ms)                          # not the median: of frames that alternate short and long it is either
    slow = sum(1 for x in ms if x > mid * SLOW)
    chance = 3.0 * (max(base, 1.0 / len(ms)) * (1.0 - base) / len(ms)) ** 0.5
    return slow if slow >= EDGE_MIN and slow / len(ms) > base + max(EDGE, chance) else 0


def goal(st: State, cfg: Cfg) -> float | None:
    """The frame rate that has to be delivered now, or None while nothing is known yet (no target, no reference)."""
    if cfg.target:
        return (st.ref if _from_ref(st, cfg) else cfg.target) * (1 - TOL)
    if st.ref is None:
        return None
    return st.ref - max(st.ref * cfg.keep_pct / 100.0, KEEP_FPS)


def out_of_reach(st: State, cfg: Cfg) -> bool:
    """Target mode with Auto FPS: the full limit gives less than the target, so what it gives is held instead."""
    return bool(cfg.target) and _from_ref(st, cfg)


def _from_ref(st: State, cfg: Cfg) -> bool:
    """The goal hangs on the reference (so the reference has to be kept true)."""
    return not cfg.target or (cfg.auto_fps and st.ref is not None and st.ref < cfg.target * REACHED)


def step(st: State, obs: Obs, cfg: Cfg) -> Action:
    t = obs.t
    dt = max(0.0, t - st.t) if st.t is not None else 0.0
    st.t = t
    top = cfg.ceiling if obs.cap_w is None else int(max(cfg.floor, min(cfg.ceiling, obs.cap_w)))
    act = Action(look_s=LOOK_S if st.mode == "hold" else WATCH_S)

    def write(watts: int, note: str) -> None:
        watts = int(max(cfg.floor, min(top, watts)))
        if watts != st.limit:
            st.limit, st.changed_at, act.limit_w = watts, t, watts
        act.note = note

    def watch(mode: str, watts: int, note: str) -> None:
        st.mode, st.prev, st.since, st.wrote_at, st.watch = mode, st.limit, t, t, []
        st.free_fps.clear()
        write(watts, note)
        act.look_s = WATCH_S

    def hold(quiet_s: float = HOLD_S) -> None:
        st.mode, st.quiet_until = "hold", t + quiet_s
        st.shift_fps = st.shift_since = None
        act.look_s = LOOK_S

    def failed(watts: int | None) -> None:
        if watts is None:
            return
        n = st.fail.get(watts, [0.0, 0])[1] + 1
        st.fail[watts] = [t + min(RETRY_MAX_S, RETRY_S * 2 ** (n - 1)), n]
        if st.good is not None and st.good <= watts:
            st.good = None

    for end, ms in obs.frames:
        ms *= obs.multiplier                         # a rendered frame is `multiplier` shown ones
        st.last.append(ms)
        st.pace.append(ms)
        st.recent.append((end, ms))
        st.frame_at = end
        if st.mode != "hold" and end >= st.since + SETTLE_S:
            st.watch.append(ms)
    while st.recent and st.recent[0][0] < t - RECENT_S:
        st.recent.popleft()

    if st.top is not None and top > st.top:          # the guard gave watts back: what they give isn't known yet
        st.ref_at = -1e9
    st.top = top
    if st.limit < cfg.floor:                         # the floor was raised
        write(cfg.floor, f"the floor is {cfg.floor} W")
        st.prev = max(st.prev, cfg.floor)
        if st.mode != "hold":
            hold()
        return act
    if st.limit > top:                               # the guard's cap: under it at once, whatever was being watched
        write(top, f"the temperature guard allows {top} W")
        st.prev = min(st.prev, top)
        if st.mode != "hold":
            hold()
        return act

    cur = _fps(st.last) if len(st.last) == N else None
    pace_ms = statistics.median(st.pace) if len(st.pace) >= N else None
    here = _fps(st.pace) if pace_ms is not None else None
    usual = st.usual or st.ref or cfg.target
    silent = st.frame_at is not None and t - st.frame_at > NO_FRAMES_S
    off = bool(usual) and cur is not None and not usual * OFF_LO <= cur <= usual * OFF_HI
    if off and not silent and len(st.pace) == PACE_FRAMES:
        st.off_since = st.off_since if st.off_since is not None else t
        if t - st.off_since >= ADOPT_S:
            # it stayed: this is the game now (a much lighter or heavier part, a frame limit switched on or off, a
            # target far from what the game does). Its pace is the usual one, and nothing known before applies
            st.usual, st.ref, st.ref_at, st.off_since = here, None, -1e9, None
            st.drop_floor = st.ref_high = None
            st.fail.clear()
            st.misses, st.good, st.home, st.safe, st.ref_here = 0, None, None, None, None
            off = False
    elif not off:
        st.off_since = None
    if silent or cur is None or pace_ms is None or off:
        # no frames, far too few or far too many: a loading screen, a menu, a pause, or a scene the limit starves.
        # Nothing is learned from it and the timers wait. A write being watched is dropped (back to the limit before
        # it); a lowered limit goes up while too few frames still come (in a loading screen that costs watts for a
        # moment; in a starved scene it is the cure)
        if st.mode in ("probe", "recover", "check"):
            write(st.prev, f"too few frames to judge on: back to {st.prev} W")
            st.retest, st.tried = False, None
            hold()
        elif st.mode == "back":
            hold()
        elif st.limit < top and not silent and cur is not None and usual and cur < usual * OFF_LO:
            write(top, f"{cur:.0f} fps at {st.limit} W: the full {top} W")
            hold()
        st.quiet_until += dt
        st.ref_at += dt
        st.ref_drop_at += dt
        return act
    st.usual = here

    # the reference follows the pace whenever the limit can't be what holds the game back
    free = st.limit >= top or obs.clipped is False or (obs.draw_w is not None and obs.draw_w <= st.limit - 2)
    if st.mode == "hold" and free and len(st.pace) == PACE_FRAMES and t - st.changed_at >= BACK_S:
        st.free_fps.append(here)
        if len(st.free_fps) >= FREE_LOOKS:
            level = statistics.median(st.free_fps)
            if st.ref is not None and level < st.ref * (1 - SHIFT):
                st.ref_drop_at = t                   # much less than before: a cutscene, a video, or a heavier place
            st.ref, st.ref_at = level, t
    elif not free:
        st.free_fps.clear()
    rising = obs.draw_w is not None and st.draw_was is not None and obs.draw_w > st.draw_was + RISE_W
    st.draw_was = obs.draw_w if obs.draw_w is not None else st.draw_was
    if st.mode == "hold":
        # the median of the last looks' figures: a change of the frame rate (a loss!) is in a few of them, not in most
        st.noises.append(_spread(st.pace))
        st.noise = statistics.median(st.noises)
    want = goal(st, cfg)
    # NOISE times what the game's own wobble does to a rate of N frames, and of PACE_FRAMES (at most four times the
    # plain thresholds)
    clear = min(4 * CLEAR, max(CLEAR, NOISE * st.noise))
    small = min(4 * SUSTAINED, max(SUSTAINED, NOISE * st.noise / (PACE_FRAMES / N) ** 0.5))
    dippy = sum(1 for d in st.dips if t - d <= DIPS_S) >= DIPS
    # two measured limits say where the need lies only when the upper one is not on a frame limit: with a target that
    # the full limit can't reach
    open_top = bool(cfg.target) and _from_ref(st, cfg)

    if st.mode == "hold":
        st.held_s[st.limit] = st.held_s.get(st.limit, 0.0) + dt
        return _hold(st, obs, cfg, act, top, cur, pace_ms, here, want, clear, small, dippy, free, write, watch, hold, failed)

    if st.mode == "probe" and obs.draw_w is not None and obs.draw_w > st.limit + BIND_W and t - st.wrote_at < BIND_S:
        st.since, st.watch = t, []                   # the package still draws more than the new limit: not yet in force
        if want is not None and cur < want * (1 - SHIFT) and st.prev < top and not st.retest and not dippy:
            tried, st.lost = st.limit, cur           # ... but the frames collapse meanwhile: all the watts at once
            write(st.prev, "")
            watch("recover", top, f"{cur:.1f} fps at {tried} W, under {want:.1f}: the full {top} W")
            st.tried = tried
        return act
    seen = t - st.since
    if st.mode == "probe":
        slow = _edge(st.watch, st.base_slow or 0.0) if seen >= DWELL_S / 2 and not dippy else 0
        edge = slow > 0
        # a clear loss on the last N frames; a small one on all the frames of the watch, once there are enough of them
        # to tell; at the end of the watch, the goal itself
        got = _fps(st.watch) if st.watch and (seen >= DWELL_S or (len(st.watch) >= 2 * N and not dippy)) else None
        if got is not None and dippy:                # this game dips by itself: judged at the end of the watch only, on
            got = 1000.0 / statistics.median(st.watch)   # its typical frame, not on a rate with a dip in it
        quick = want is not None and len(st.watch) >= N // 2 and cur < want * (1 - clear)
        if quick and dippy:                          # this game dips by itself: only a loss that stays counts
            st.loss_since = st.loss_since if st.loss_since is not None else t
            quick = t - st.loss_since >= PATIENT_S
        elif not quick:
            st.loss_since = None
        lost = quick or (want is not None and got is not None and got < want * (1 - (0.0 if seen >= DWELL_S else small)))
        if lost and cur < want * (1 - SHIFT) and st.prev < top and not st.retest:
            # far more lost than a step of this size usually costs: the scene may have changed under the step. All
            # the watts at once; what the full limit gives then says which it was
            tried, st.lost = st.limit, cur
            write(st.prev, "")                       # the limit to go back to afterwards is the one before the step
            watch("recover", top, f"{cur:.1f} fps at {tried} W, under {want:.1f}: the full {top} W")
            st.tried = tried
        elif lost or edge:
            why = f"{got or cur:.1f} fps, under {want:.1f}" if lost else f"{slow} slow frames"
            tried, before = st.limit, st.prev
            st.retest, st.loss_since = False, None
            failed(tried)
            if lost and st.before and open_top:      # how far under: the levels a straight line also puts under
                failed(_between(tried, got or cur, before, st.before, want))
            st.misses += 1
            watch("back", before, f"{tried} W: {why}; back to {before} W")
        elif seen >= DWELL_S and st.retest:
            # the level that lost frames holds on its second try: the loss was the game's own
            st.retest, st.loss_since = False, None
            st.dips.append(t)
            act.note = f"{st.limit} W holds again: the game's own dip"
            hold(HOLD_S)
        elif seen >= DWELL_S:
            st.good = st.limit if st.limit < top else st.good
            st.safe, st.misses, st.loss_since, st.chain = st.prev, 0, None, st.chain + 1
            nxt = _next_down(st, obs, cfg, t)
            if st.chain >= CHAIN:
                st.chain = 0
                act.note = f"holding at {st.limit} W"
                hold(AFTER_LOSS_S)
            elif nxt is not None and _steady(st, t) and t - st.ref_drop_at >= CONFIRM_S:
                was, st.before = st.limit, _fps(st.watch)
                watch("probe", nxt, f"{was} W holds; trying {nxt} W")
            else:
                act.note = f"holding at {st.limit} W"
                hold(0.0 if nxt is None else HOLD_S)
                st.ref_here = None
                if st.verify and _from_ref(st, cfg): # the reference is from before these steps: look again soon
                    st.ref_at = t - CHECK_MAX_S + CHECK_FIRST_S
                st.verify = False
        return act

    if st.mode == "back":                            # a failed step was undone: are the frames back?
        tail = st.watch[-N:]
        if want is not None and len(st.watch) >= N and st.limit < top and (
                (seen >= BACK_S / 2 and _fps(tail) < want * (1 - clear))
                or (seen >= BACK_S and _fps(st.watch) < want * (1 - small))):
            st.lost, was, st.held_for = _fps(tail), st.limit, None
            watch("recover", top, f"{st.lost:.1f} fps at {was} W, under {want:.1f}: the full {top} W")
        elif seen >= BACK_S:
            hold(min(AFTER_LOSS_MAX_S, AFTER_LOSS_S * 2 ** max(0, st.misses - 1)))
            st.ref_here = None
            if st.verify and _from_ref(st, cfg) and st.limit < top:
                st.ref_at = t - CHECK_MAX_S + CHECK_FIRST_S
            st.verify = False
        return act

    if seen < BACK_S or len(st.watch) < N // 2:
        if seen > 3 * BACK_S:                        # no judgement possible: stay where it is
            hold()
        return act
    if rising and t - st.wrote_at < BIND_S:          # the watts are still arriving: judged on the frames after that
        st.since, st.watch = t - BACK_S / 2, st.watch[-N:]
        return act
    third = len(st.watch) // 3
    if third >= N and t - st.wrote_at < 2 * BIND_S and _fps(st.watch[-third:]) > _fps(st.watch[:third]) * (1 + CLEAR):
        return act                                   # the frame rate is still climbing: not judged yet
    now = _fps(st.watch[len(st.watch) // 2:]) if len(st.watch) >= 4 * N else _fps(st.watch)

    # recover / check: at the full limit, on purpose. What it gives is the reference from here on
    prior = st.ref if st.ref is not None else cfg.target   # with a target that was held, the full limit gave that
    moved = st.ref is not None and abs(now - st.ref) >= SHIFT * st.ref
    if prior is not None and now < prior * (1 - SHIFT):
        # much less than before: a cutscene, a video, or a heavier place. The limit that held before it may be held on
        # this lower reference, but nothing under it: when the game is back, that limit shows more frames than the
        # reference, and the reference is looked at again
        st.ref_drop_at, st.ref_high = t, max(prior, st.ref_high or 0.0)
        if st.prev < top:
            st.drop_floor = st.prev if st.drop_floor is None else max(st.drop_floor, st.prev)
    elif st.ref_high is not None and now >= st.ref_high * (1 - SHIFT):
        st.drop_floor = st.ref_high = None           # the full limit gives what it gave before again
    if st.ref is not None and now > st.ref * (1 + 2 * CHECK_MOVED):
        # the full limit gives clearly more than it did: a lighter scene. What failed in the heavier one says nothing
        # here, and the level that held before it is the first to try
        st.fail.clear()
        st.misses = 0
        if st.home is not None and st.home < st.prev and t - st.home_at <= HOME_FOR_S:
            st.good, st.home = st.home, None
    st.ref, st.ref_at = now, t
    want = goal(st, cfg)
    was = st.lost or now
    st.ref_here = None
    if st.mode == "recover" and st.tried is not None:
        tried, st.tried = st.tried, None
        failed(tried)
        if st.before and now >= st.before * (1 - CLEAR):
            # the full limit gives what the limit before the step gave: the same scene, with a steep edge under it
            if open_top:
                failed(_between(tried, was, st.prev, st.before, want))
            write(st.prev, f"{tried} W lost {st.before - was:.0f} fps; back to {st.prev} W")
        else:                                        # another scene: where its need lies, from the two points
            if bool(cfg.target) and _from_ref(st, cfg):
                failed(_between(tried, was, top, now, want))
            act.note = f"another scene: {now:.0f} fps at {top} W"
        hold(AFTER_LOSS_S)
        return act
    if st.mode == "recover":
        if want is not None and was >= want:
            # the limit before gives what the full one gives, within the margin: the scene got heavier, not the limit
            # too low. Back to it; when the full limit gives much less or more than before, it is another scene, and
            # what failed in the last one says nothing here
            if moved:
                st.fail.clear()
                st.misses = 0
            write(st.prev, f"the full {top} W gives {now:.0f} fps: the scene, not the limit; back to {st.prev} W")
            hold(AFTER_LOSS_S)
        elif ((st.held_for or 0.0) >= HOME_S or dippy) and t - st.retested.get(st.prev, -1e9) >= RETRY_S:
            # the frames are back at the full limit. But the level had held for a while, or this game dips by
            # itself: a dip of the game's own looks the same. The level once more, watched; a loss that comes back
            # is the limit's
            level = st.prev
            st.retested[level] = t
            st.before, st.verify, st.retest, st.loss_since = now, False, True, None
            st.base_slow = _slow(st.watch, 1000.0 / _fps(st.watch))
            watch("probe", level, f"{was:.0f} fps at {level} W, {now:.0f} at {top} W: {level} W once more")
        else:
            failed(st.prev)
            if bool(cfg.target) and _from_ref(st, cfg):
                failed(_between(st.prev, was, top, now, want))
            act.note = f"{st.prev} W was too low ({was:.0f} fps, {now:.0f} at {top} W)"
            hold(AFTER_LOSS_S)
        return act
    # check
    if want is not None and was < want:
        failed(st.prev)
        if bool(cfg.target) and _from_ref(st, cfg):
            failed(_between(st.prev, was, top, now, want))
        act.note = f"{top} W gives {now:.0f} fps, {st.prev} W gave {was:.0f}: staying up"
        hold(AFTER_LOSS_S)
    else:
        st.ref_here = was
        write(st.prev, f"{top} W gives {now:.0f} fps, {st.prev} W {was:.0f}: back to {st.prev} W")
        hold(AFTER_LOSS_S)
    return act


def _between(low_w: int, low_fps: float, high_w: int, high_fps: float, want: float) -> int | None:
    """Two limits and what each gave, the lower one under the goal: the highest whole-watt level that a straight line
    between them still puts under the goal (it is taken as failed without being tried), or None when the two points
    say nothing (no gain from the watts)."""
    if high_w <= low_w or high_fps <= low_fps or want <= low_fps:
        return None
    need = low_w + (high_w - low_w) * (want - low_fps) / (high_fps - low_fps)
    level = int(-(-need // 1)) - 1                   # the last whole watt under it
    return level if low_w < level < high_w else None


def _steady(st: State, t: float) -> bool:
    """The frame rate of each half second of the last STEADY_S is within STEADY of their median (frame times may be
    uneven; the rate may not jump)."""
    slots: dict = {}
    for end, ms in st.recent:
        if end >= t - STEADY_S:
            n, total = slots.get(int((t - end) * 2), (0, 0.0))
            slots[int((t - end) * 2)] = (n + 1, total + ms)
    rates = [1000.0 * n / total for n, total in slots.values() if n >= 3]
    if len(rates) < STEADY_S * 2 - 1:
        return False
    mid = statistics.median(rates)
    return max(rates) - min(rates) <= STEADY * mid


def _next_down(st: State, obs: Obs, cfg: Cfg, t: float) -> int | None:
    """The next limit to try under this one, or None: halfway to the highest level under it that failed; that level
    itself only when it is the next one down and its time is up; with none, the last level that held, else STEP_W
    down or straight to just over what the package draws."""
    under = [w for w in st.fail if w < st.limit]
    if under:
        nxt = (st.limit + max(under) + 1) // 2
        if nxt >= st.limit:
            if st.fail[max(under)][0] > t:
                return None
            nxt = max(under)
    elif st.good is not None and st.good < st.limit - STEP_W:
        nxt = st.good
    else:
        nxt = st.limit - STEP_W
        if obs.draw_w is not None and obs.draw_w + 2 < nxt:
            nxt = int(-(-obs.draw_w // 1)) + 2       # a limit the chip doesn't reach changes nothing
    nxt = max(cfg.floor, nxt, st.drop_floor or 0)
    return nxt if nxt < st.limit else None


def _hold(st, obs, cfg, act, top, cur, pace_ms, here, want, clear, small, dippy, free, write, watch, hold, failed) -> Action:
    t = st.t
    if want is None:                                 # no target and nothing known yet: what does the full limit give?
        if st.limit < top:
            st.lost = cur
            watch("check", top, f"measuring what the full {top} W gives")
        return act

    # another scene: what failed in the last one says nothing here, and a reference from the last one is checked
    if st.shift_fps is None:
        if len(st.pace) == PACE_FRAMES and t - st.changed_at >= DWELL_S:
            st.shift_fps = here
    elif abs(here - st.shift_fps) >= SHIFT * st.shift_fps:
        st.shift_since = st.shift_since if st.shift_since is not None else t
        if t - st.shift_since >= SHIFT_S:
            st.fail.clear()
            st.misses = 0
            st.shift_fps, st.shift_since = here, None
            st.ref_at = -1e9
            if st.home is not None and st.home < st.limit:
                st.good, st.home = st.home, None     # tried once: it holds again, or this scene needs more
    else:
        st.shift_since = None

    sustained = len(st.pace) == PACE_FRAMES and t - st.changed_at >= DWELL_S and here < want * (1 - small)
    if cur < want * (1 - clear):
        if free:                                     # the limit can't be the cause, and there is nothing more to give
            st.free_loss = st.free_loss if st.free_loss is not None else t
            return act
        if dippy:                                    # this game dips by itself: only a loss that stays counts
            st.loss_since = st.loss_since if st.loss_since is not None else t
            if t - st.loss_since < PATIENT_S:
                return act
        st.lost, was_at, st.held_for, st.loss_since = cur, st.limit, t - st.changed_at, None
        watch("recover", top, f"{st.lost:.1f} fps at {was_at} W, under {want:.1f}: the full {top} W")
        return act
    if st.loss_since is not None:                    # it passed by itself: one more of the game's own dips
        st.dips.append(t)
        st.loss_since = None
    if st.free_loss is not None:                     # over: a dip of the game's own if it was short, else another scene
        if t - st.free_loss <= DIP_MAX_S:
            st.dips.append(t)
        st.free_loss = None
    if sustained and dippy and st.limit < top:       # as above: a second of it, not one dip's worth of frames
        st.small_since = st.small_since if st.small_since is not None else t
        sustained = t - st.small_since >= PATIENT_S + PACE_FRAMES * pace_ms / 1000.0
    elif not sustained:
        st.small_since = None
    if sustained:
        st.small_since = None
        if st.limit < top:                           # a little under, and staying there: the level that held before
            was_at = st.limit
            up = st.safe if st.safe is not None and st.limit < st.safe <= top else st.limit + 1
            failed(was_at)
            st.safe = None
            write(up, f"{here:.1f} fps at {was_at} W, a little under {want:.1f}: {up} W")
            hold(AFTER_LOSS_S)
        return act

    if st.limit < top and t - st.changed_at >= HOME_S and (
            st.home is None or st.limit <= st.home or t - st.home_at > HOME_FOR_S):
        st.home, st.home_at = st.limit, t

    # the edge: the median holds, a few frames don't. Slow against the pace of their own time, and only while that
    # pace stands still (a game that got faster makes the frames before it look slow)
    mine = [ms for end, ms in st.recent if end >= st.changed_at + SETTLE_S]
    if st.limit < top and len(mine) >= 3 * N and t - st.edge_at >= EDGE_UP_S and t - st.changed_at >= DWELL_S:
        mid, half = statistics.median(mine), len(mine) // 2
        still = abs(statistics.median(mine[:half]) - statistics.median(mine[half:])) <= CHECK_MOVED * mid
        if still and not dippy and st.base_slow is not None and _edge(mine, st.base_slow):
            failed(st.limit)
            st.edge_at = t
            was = st.limit
            write(st.limit + 1, f"slow frames at {was} W: {was + 1} W")
            hold(HOLD_S)
            return act

    # the reference grows old while the limit holds the chip: look at the full limit for a moment
    binds = obs.clipped is not False and (obs.draw_w is None or obs.draw_w > st.limit - 2)
    # (not from a watt under it: what one watt more gives is not worth a look)
    if _from_ref(st, cfg) and st.limit < top - 1 and binds and not obs.thermal and len(st.pace) == PACE_FRAMES:
        if st.ref_here is None and t - st.changed_at >= DWELL_S:
            st.ref_here = here                       # what this limit gives while the reference is fresh
        moved = st.ref_here is not None and abs(here - st.ref_here) >= CHECK_MOVED * st.ref_here
        # more frames here than the full limit is believed to give (the belief is from a moment that wasn't the game),
        # or more than this limit gave when the reference was taken (a lighter scene): the full limit gives more too
        soon = t - st.ref_at >= ABOVE_S and t - st.changed_at >= DWELL_S
        above = soon and here > st.ref * (1 + max(ABOVE, 2 * small))
        rose = soon and st.ref_here is not None and here > st.ref_here * (1 + max(ABOVE, 2 * small))
        every = REACH_S if t - st.ref_drop_at < REACH_FOR_S else CHECK_MAX_S
        if above or rose or t - st.ref_at >= every or (moved and t - st.ref_at >= CHECK_S):
            st.lost = cur
            watch("check", top, f"checking what the full {top} W gives")
            return act

    if (t >= st.quiet_until and t - st.changed_at >= AFTER_LOSS_S and t - st.ref_drop_at >= CONFIRM_S
            and st.loss_since is None and _steady(st, t)):
        nxt = _next_down(st, obs, cfg, t)
        if nxt is not None:
            ms = [x for _, x in st.recent]
            share = _slow(ms, 1000.0 / _fps(ms))
            st.base_slow = share if st.limit >= top or st.base_slow is None else min(st.base_slow, share)
            st.verify, st.before, st.chain = st.limit >= top, here, 0
            was = st.limit
            watch("probe", nxt, f"{cur:.0f} fps holds at {was} W; trying {nxt} W")
    return act
