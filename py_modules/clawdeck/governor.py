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

N = 10                  # a clear loss: the median of the last N frames is CLEAR under the goal ...
CLEAR = 0.03
SUSTAINED = 0.01        # ... a sustained one: the pace (PACE_FRAMES) is this far under it. A step down has to hold the
                        # goal itself over its whole watch, so a level that was accepted isn't lost to noise afterwards
PACE_FRAMES = 60
TOL = 0.05              # target mode: this far under the target is still the target
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
STEP_W = 2
EDGE_UP_S = 10.0        # the edge: +1 W at most this often
RETRY_S, RETRY_MAX_S = 60.0, 600.0     # a failed level may be tried again after this; doubled at each further failure
CHECK_S, CHECK_MAX_S = 60.0, 600.0     # the reference is checked at the full limit this often; doubled while nothing is gained
STEADY = 1.25           # a step down starts only while the 95th percentile frame is within this of the median
SHIFT, SHIFT_S = 0.15, 5.0             # the pace moved this much for this long at one limit: another scene
OFF_LO = 0.35           # frames this far under the usual rate are a loading screen, a menu or a pause: not judged
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
    check_every: float = CHECK_S
    lost: float | None = None       # the frame rate before a raise, fps
    good: int | None = None         # the last limit under the full one that held
    home: int | None = None         # the last one that held for HOME_S: tried first when a heavier scene is over
    base_slow: float = 0.0          # share of slow frames before the step being watched (the game's own hitches)
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
    return 1000.0 / statistics.median(ms) if ms else None


def _slow(ms, pace_ms: float) -> float:
    ms = list(ms)
    return sum(1 for x in ms if x > pace_ms * SLOW) / len(ms) if ms else 0.0


def goal(st: State, cfg: Cfg) -> float | None:
    """The frame rate that has to be delivered now, or None while nothing is known yet (no target, no reference)."""
    if cfg.target:
        base = st.ref if st.ref is not None and st.ref < cfg.target and cfg.auto_fps else cfg.target
        return base * (1 - TOL)
    if st.ref is None:
        return None
    return st.ref - max(st.ref * cfg.keep_pct / 100.0, KEEP_FPS)


def _from_ref(st: State, cfg: Cfg) -> bool:
    """The goal hangs on the reference (so the reference has to be kept true)."""
    return not cfg.target or (cfg.auto_fps and st.ref is not None and st.ref < cfg.target)


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
        st.mode, st.prev, st.since, st.watch = mode, st.limit, t, []
        write(watts, note)
        act.look_s = WATCH_S

    def hold(quiet_s: float = HOLD_S) -> None:
        st.mode, st.quiet_until = "hold", t + quiet_s
        st.shift_fps = st.shift_since = None
        act.look_s = LOOK_S

    def failed(watts: int) -> None:
        n = st.fail.get(watts, [0.0, 0])[1] + 1
        st.fail[watts] = [t + min(RETRY_MAX_S, RETRY_S * 2 ** (n - 1)), n]
        if st.good is not None and st.good <= watts:
            st.good = None
        if st.home is not None and st.home <= watts:
            st.home = None

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

    if st.limit > top:                               # the guard's cap: under it at once, whatever was being watched
        write(top, f"the temperature guard allows {top} W")
        st.prev = min(st.prev, top)
        if st.mode != "hold":
            hold()
        return act

    cur = _fps(st.last) if len(st.last) == N else None
    pace_ms = statistics.median(st.pace) if len(st.pace) >= N else None
    want = goal(st, cfg)
    usual = st.ref or cfg.target
    silent = st.frame_at is not None and t - st.frame_at > NO_FRAMES_S
    if silent or cur is None or pace_ms is None or (usual and cur < usual * OFF_LO):
        # no frames, or far too few: a loading screen, a menu, a pause, or a scene the limit starves. Nothing is
        # learned from it and the timers wait. A step being watched is taken back; a lowered limit goes up while
        # frames still come (in a loading screen that costs watts for a moment; in a starved scene it is the cure)
        if st.mode == "probe":
            write(st.prev, f"too few frames to judge {st.limit} W on: back to {st.prev} W")
            hold()
        elif st.mode == "hold" and st.limit < top and not silent and cur is not None:
            write(top, f"{cur:.0f} fps at {st.limit} W: the full {top} W")
            hold()
        st.quiet_until += dt
        st.ref_at += dt
        st.since += dt if st.mode != "hold" else 0.0
        return act

    # the reference follows the pace whenever the limit can't be what holds the game back
    free = st.limit >= top or obs.clipped is False or (obs.draw_w is not None and obs.draw_w <= st.limit - 2)
    if st.mode == "hold" and free and len(st.pace) == PACE_FRAMES and t - st.changed_at >= DWELL_S:
        st.ref, st.ref_at = 1000.0 / pace_ms, t
        want = goal(st, cfg)

    if st.mode == "hold":
        st.held_s[st.limit] = st.held_s.get(st.limit, 0.0) + dt
        return _hold(st, obs, cfg, act, top, cur, pace_ms, want, write, watch, hold, failed)

    seen = t - st.since
    if st.mode == "probe":
        slow = sum(1 for x in st.watch if x > pace_ms * SLOW)
        frames_in_dwell = max(len(st.watch), DWELL_S * 1000.0 / pace_ms)
        edge = slow >= EDGE_MIN and slow > (st.base_slow + EDGE) * frames_in_dwell
        got = _fps(st.watch) if seen >= DWELL_S and st.watch else None
        lost = want is not None and ((len(st.watch) >= N // 2 and cur < want * (1 - CLEAR)) or (got is not None and got < want))
        if lost and cur < want * (1 - SHIFT) and st.prev < top:
            # far more lost than a step of this size costs: the scene changed under the step. All the watts at once
            tried, st.lost = st.limit, cur
            write(st.prev, "")                       # the limit to go back to afterwards is the one before the step
            watch("recover", top, f"{cur:.1f} fps at {tried} W, under {want:.1f}: the full {top} W")
        elif lost or edge:
            why = f"{got or cur:.1f} fps, under {want:.1f}" if lost else f"{slow} slow frames"
            tried, before = st.limit, st.prev
            failed(tried)
            st.misses += 1
            watch("back", before, f"{tried} W: {why}; back to {before} W")
        elif seen >= DWELL_S:
            st.good = st.limit if st.limit < top else st.good
            st.misses = 0
            nxt = _next_down(st, obs, cfg, t)
            if nxt is not None and _steady(st) and not obs.thermal:
                was = st.limit
                watch("probe", nxt, f"{was} W holds; trying {nxt} W")
            else:
                act.note = f"holding at {st.limit} W"
                hold(0.0 if nxt is None else HOLD_S)
        return act

    if st.mode == "back":                            # a failed step was undone: are the frames back?
        tail = st.watch[-(N // 2):]
        if want is not None and len(st.watch) >= N // 2 and st.limit < top and (
                (seen >= BACK_S / 2 and _fps(tail) < want * (1 - CLEAR))
                or (seen >= BACK_S and _fps(st.watch) < want * (1 - SUSTAINED))):
            st.lost, was = _fps(tail), st.limit
            watch("recover", top, f"{st.lost:.1f} fps at {was} W, under {want:.1f}: the full {top} W")
        elif seen >= BACK_S:
            hold(min(AFTER_LOSS_MAX_S, AFTER_LOSS_S * 2 ** max(0, st.misses - 1)))
        return act

    if seen < BACK_S or len(st.watch) < N // 2:
        if seen > 3 * BACK_S:                        # no judgement possible: stay where it is
            hold()
        return act
    now = _fps(st.watch)

    # recover / check: at the full limit, on purpose. What it gives is the reference from here on
    moved = st.ref is not None and abs(now - st.ref) >= SHIFT * st.ref
    st.ref, st.ref_at = now, t
    want = goal(st, cfg)
    was = st.lost or now
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
        else:
            failed(st.prev)
            st.check_every = CHECK_S
            act.note = f"{st.prev} W was too low ({was:.0f} fps, {now:.0f} at {top} W)"
            hold(AFTER_LOSS_S)
        return act
    # check
    if want is not None and was < want:
        failed(st.prev)
        st.check_every = CHECK_S
        act.note = f"{top} W gives {now:.0f} fps, {st.prev} W gave {was:.0f}: staying up"
        hold(AFTER_LOSS_S)
    else:
        st.check_every = min(CHECK_MAX_S, st.check_every * 2)
        write(st.prev, f"{top} W gives {now:.0f} fps, {st.prev} W {was:.0f}: back to {st.prev} W")
        hold(AFTER_LOSS_S)
    return act


def _steady(st: State) -> bool:
    ms = sorted(x for _, x in st.recent)
    return len(ms) >= 3 * N and ms[int(len(ms) * 0.95)] <= statistics.median(ms) * STEADY


def _next_down(st: State, obs: Obs, cfg: Cfg, t: float) -> int | None:
    """The next limit to try under this one, or None: halfway to the highest level under it that failed lately; when
    the time of every failed level is up, the highest of them again (not under it); with none, the last level that
    held, else STEP_W down or straight to just over what the package draws."""
    under = [w for w in st.fail if w < st.limit]
    blocked = [w for w in under if st.fail[w][0] > t]
    if blocked:
        nxt = (st.limit + max(blocked) + 1) // 2
    elif under:
        nxt = max(under)
    elif st.good is not None and st.good < st.limit - STEP_W:
        nxt = st.good
    else:
        nxt = st.limit - STEP_W
        if obs.draw_w is not None and obs.draw_w + 2 < nxt:
            nxt = int(-(-obs.draw_w // 1)) + 2       # a limit the chip doesn't reach changes nothing
    nxt = max(cfg.floor, nxt)
    return nxt if nxt < st.limit else None


def _hold(st, obs, cfg, act, top, cur, pace_ms, want, write, watch, hold, failed) -> Action:
    t = st.t
    if want is None:                                 # no target and nothing known yet: what does the full limit give?
        if st.limit < top:
            st.lost = cur
            watch("check", top, f"measuring what the full {top} W gives")
        return act

    # another scene: what failed in the last one says nothing here, and a reference from the last one is checked
    if st.shift_fps is None:
        if len(st.pace) == PACE_FRAMES and t - st.changed_at >= DWELL_S:
            st.shift_fps = 1000.0 / pace_ms
    elif abs(1000.0 / pace_ms - st.shift_fps) >= SHIFT * st.shift_fps:
        st.shift_since = st.shift_since if st.shift_since is not None else t
        if t - st.shift_since >= SHIFT_S:
            st.fail.clear()
            st.misses = 0
            st.shift_fps, st.shift_since = 1000.0 / pace_ms, None
            st.ref_at, st.check_every = -1e9, CHECK_S
            if st.home is not None and st.home < st.limit:
                st.good, st.home = st.home, None     # tried once: it holds again, or this scene needs more
    else:
        st.shift_since = None

    sustained = len(st.pace) == PACE_FRAMES and t - st.changed_at >= DWELL_S and 1000.0 / pace_ms < want * (1 - SUSTAINED)
    if cur < want * (1 - CLEAR) or sustained:
        if st.limit < top:                           # a loss under the full limit: all the watts at once
            st.lost, was_at = (min(cur, 1000.0 / pace_ms) if sustained else cur), st.limit
            watch("recover", top, f"{st.lost:.1f} fps at {was_at} W, under {want:.1f}: the full {top} W")
        return act                                   # at the full limit there is nothing more to give

    if st.limit < top and t - st.changed_at >= HOME_S:
        st.home = st.limit

    # the edge: the median holds, a few frames don't
    mine = [ms for end, ms in st.recent if end >= st.changed_at + SETTLE_S]
    if (st.limit < top and len(mine) >= 3 * N and t - st.edge_at >= EDGE_UP_S and t - st.changed_at >= DWELL_S
            and _slow(mine, pace_ms) > st.base_slow + EDGE and sum(1 for x in mine if x > pace_ms * SLOW) >= EDGE_MIN):
        failed(st.limit)
        st.edge_at = t
        was = st.limit
        write(st.limit + 1, f"slow frames at {was} W: {was + 1} W")
        hold(HOLD_S)
        return act

    # the reference grows old while the limit holds the chip: look at the full limit for a moment
    binds = obs.clipped is not False and (obs.draw_w is None or obs.draw_w > st.limit - 2)
    if _from_ref(st, cfg) and st.limit < top and binds and t - st.ref_at >= st.check_every and not obs.thermal:
        st.lost = cur
        watch("check", top, f"checking what the full {top} W gives")
        return act

    if t >= st.quiet_until and t - st.changed_at >= AFTER_LOSS_S and not obs.thermal and _steady(st):
        nxt = _next_down(st, obs, cfg, t)
        if nxt is not None:
            share = _slow((ms for _, ms in st.recent), pace_ms)
            st.base_slow = share if st.limit >= top else min(st.base_slow, share)
            was = st.limit
            watch("probe", nxt, f"{cur:.0f} fps holds at {was} W; trying {nxt} W")
    return act
