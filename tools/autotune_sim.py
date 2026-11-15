#!/usr/bin/env python3
"""autotune_sim.py - Autotune against games as a Claw 8 EX measured them, without a Claw.

  python3 tools/autotune_sim.py                                   # every scenario, both modes, one table
  python3 tools/autotune_sim.py --scenario emulator-parked --mode target --target 30 [--seed 3] [--trace out.csv]
  python3 tools/autotune_sim.py --replay tests/data/autotune/mcla-parked.csv --target 30

Autotune was tuned on the device, one play session at a time, and no two sessions show it the same scenes: nothing
could say afterwards whether a change made it better. Here the scenes repeat, and the simulator knows what Autotune
can't see on the device: which watts each scene needs.

The game (Game). A scene is a table: the frame rate at each sustained power limit, measured level by level on a
Claw 8 EX (SCENES; tests/data/autotune/ has the recordings behind two of them, and tests compare the tables with
them). Each frame takes 1000 / rate(watts the chip gets now), with the spread the recording had, and not less than the
game's own frame limit. The chip follows a new limit with a short lag (LAG_S): recorded frames were slower from the 3rd
frame after a step down and back on pace 0.25 s after a step up. A scenario is a list of scenes with a factor for the
work each one asks (0.5: half the frames at the same watts), so the watts a game needs can move as they did in play.
Not modelled: the PL1 time window and PL2 (the chip running over PL1 for seconds), temperature, the CPU/GPU levers
(the hardware here has none, so Autotune's balance phase finds nothing to try), frame generation, loading screens.

The score (Score). For every moment the simulator knows the frame rate the full limit would give and the lowest
limit that gives it within TOLERANCE (the "need"). From that: watts spent over the need, the share of time the limit
cost frames, the longest such stretch, and the changes a minute.

Governors. Wired is clawdeck.autotune.Autotune itself, stepped at its own pace as main.py does, with a simulated
clock. New is clawdeck.governor alone. Another governor is an object with period (s between looks), start(game, floor,
ceiling), tick(frames, draw_w) and stop(); it calls game.set_limit(watts).

Replay (--replay). A recording's frames go to the governor as they came, whatever it asks for (it is not obeyed:
the limit is the recorded one). It shows when a governor notices a loss that really happened.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import math
import os
import random
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py_modules"))

from clawdeck import autotune, governor, hw, power  # noqa: E402

LAG_S = 0.15            # the chip's sustained power follows a new limit with this time constant
PRESENT_MS = 0.2        # spread of a frame held by the game's frame limit (recorded: 33.33 ms median, 33.9 ms p99)
TOLERANCE = 0.05        # a limit "costs frames" when it gives this much less than the full limit would ...
SLACK_FPS = 0.5         # ... and half a frame a second more, so a level right on the 5 % line counts as holding
FLOOR_W, CEILING_W = 8, 30


@dataclass(frozen=True)
class Scene:
    """One place in a game. curve: (watts, fps) pairs, the frame rate without the game's frame limit, rising; between
    two points a straight line, above the last one flat, under the first one in proportion to the watts."""
    curve: tuple
    cap: float | None = None        # the game's own frame limit (fps)
    fills: bool = False             # draws the whole limit whatever its frames do (an emulator that spins)
    free_w: float | None = None     # watts it draws on its frame limit when the power limit doesn't hold it
    jitter: float = 0.011           # spread of one frame's work (sigma of its logarithm)
    hitch_per_min: float = 0.0      # the game's own long frames (40-190 ms), whatever the limit
    cpu_pct: float = 40.0
    gpu_bound: bool = False         # the GPU is what limits it: busy 100 % under its frame limit, less on it
    gpu_busy: int | None = None     # else the GPU's load as recorded, % (None: not recorded)
    gpu_mhz: tuple | None = None    # (watts, MHz) pairs: the GPU's clock at each limit, when it was recorded

    def rate(self, watts: float, work: float = 1.0) -> float:
        """Frames a second at these sustained watts, before the game's own limit."""
        ws = [p[0] for p in self.curve]
        i = bisect.bisect_right(ws, watts)
        if i == 0:
            fps = self.curve[0][1] * watts / ws[0]
        elif i == len(ws):
            fps = self.curve[-1][1]
        else:
            (w0, f0), (w1, f1) = self.curve[i - 1], self.curve[i]
            fps = f0 + (f1 - f0) * (watts - w0) / (w1 - w0)
        return max(fps * work, 0.5)

    def shown(self, watts: float, work: float = 1.0) -> float:
        r = self.rate(watts, work)
        return min(r, self.cap) if self.cap else r

    def busy(self, watts: float, work: float = 1.0) -> int | None:
        """GPU load, %, as PowerStation reports it to Autotune (which lowers a target's watts only under 85 %)."""
        if not self.gpu_bound:
            return self.gpu_busy
        return round(100 * self.shown(watts, work) / self.rate(watts, work))

    def mhz(self, watts: float) -> int | None:
        if not self.gpu_mhz:
            return None
        return min(self.gpu_mhz, key=lambda p: abs(p[0] - watts))[1]


SCENES = {
    # Enshrouded (Proton), standing at the Flame altar, GPU 100 % busy at every level, package = the limit:
    # tests/data/autotune/enshrouded-altar.csv. 27 and 30 W give the same (the GPU is at its top clock at both).
    "enshrouded-altar": Scene(curve=((8, 16.4), (10, 23.2), (12, 30.6), (15, 38.7), (18, 43.5), (21, 48.7), (24, 52.9),
                                     (27, 55.7), (30, 55.7)),
                              jitter=0.011, cpu_pct=21, gpu_bound=True,
                              gpu_mhz=((8, 815), (10, 980), (12, 1235), (15, 1530), (18, 1750), (21, 2030), (24, 2205),
                                       (27, 2300), (30, 2300))),
    # The same game while playing, on its 60 fps limit: the altar's curve with less work per frame (the scenario gives
    # the factor). In 110 min of play the limit that just held 60 fps moved between 22 and 30 W from one 3-minute sample
    # to the next, and a frame slower than 30 fps came about once in two minutes at the full limit (2026-11-15).
    # Assumed, not measured: on its frame limit it draws what holds that limit, no more, and the GPU is busy for the
    # share of the time that the frames it shows are of the frames it could make.
    "enshrouded-play": Scene(curve=((8, 16.4), (10, 23.2), (12, 30.6), (15, 38.7), (18, 43.5), (21, 48.7), (24, 52.9),
                                    (27, 55.7), (30, 55.7)),
                             cap=60.0, jitter=0.011, hitch_per_min=0.5, cpu_pct=30, gpu_bound=True),
    # The same game in a place where 30 W gave 48-49 fps and every limit down to 18 W lost under 10 % of that
    # (2026-11-15 01:26 and 01:37), with the GPU 100 % busy. Under 18 W the altar's figures: not measured in that place.
    "enshrouded-heavy": Scene(curve=((8, 16.4), (10, 23.2), (12, 30.6), (15, 38.7), (18, 44.5), (30, 48.5)),
                              jitter=0.011, hitch_per_min=0.5, cpu_pct=35, gpu_bound=True),
    # Midnight Club: Los Angeles on RPCS3 (30 fps lock), parked: tests/data/autotune/mcla-parked.csv. It draws the
    # whole limit at every limit. 8, 10 and 12 W are measured (every frame slower than the lock). Above that the lock
    # hides the rate: the line goes on at the same 2.08 fps per watt. The spread is the one that gives what 15 W showed,
    # the edge a governor has to find (29.7 fps, the median on pace, 6 % of the frames more than 10 % slow), and what
    # 20 W showed (30.0 fps). It is too wide under the edge: 14 % of the frames more than 10 % over the median at
    # 8-12 W, where the recording has 1-3 %.
    # GPU load 29 % (mangoapp's figure in that game).
    "mcla": Scene(curve=((8, 17.4), (10, 20.8), (12, 24.9), (30, 62.3)), cap=30.0, fills=True, jitter=0.09, cpu_pct=50,
                  gpu_busy=29),
    # Need for Speed: Most Wanted (a Windows game on its 60 fps limit): 60.0 fps at every limit down to 8 W, drawing
    # 7-10 W under a 30 W limit (2026-11-15 01:57). What it would do under 8 W is not known.
    "nfs-mw": Scene(curve=((8, 75.0), (30, 75.0)), cap=60.0, free_w=9.0, jitter=0.02, cpu_pct=15),
}

# (seconds, scene, work factor). A factor under 1 is a heavier place: fewer frames at the same watts.
SCENARIOS = {
    "light-capped": [(300, "nfs-mw", 1.0)],
    "uncapped": [(300, "enshrouded-altar", 1.0)],
    # the need: 21, 24, 22, 24 (60 fps not reached: 55.7 x 1.04 = 58), 21, 23 W. In play it moved further (limits from
    # 22 to 30 W held 60 fps within 10 %), in places the altar's curve doesn't describe: it is flat from 27 W.
    "cap-need-moves": [(180, "enshrouded-play", 1.20), (180, "enshrouded-play", 1.08), (180, "enshrouded-play", 1.14),
                       (180, "enshrouded-play", 1.04), (180, "enshrouded-play", 1.20), (180, "enshrouded-play", 1.10)],
    "out-of-reach": [(300, "enshrouded-heavy", 1.0)],
    "emulator-parked": [(300, "mcla", 1.0)],
    # driving, after tests/data/autotune/mcla-driving.csv: within 20 s the game went from holding its lock at 14 W to
    # 23.5 fps at the full 30 W, and back
    "emulator-need-jumps": [(20, "mcla", 1.0), (20, "mcla", 0.38), (20, "mcla", 0.42), (20, "mcla", 0.47),
                            (20, "mcla", 0.80), (20, "mcla", 0.42), (20, "mcla", 0.75), (20, "mcla", 1.0),
                            (20, "mcla", 0.60), (20, "mcla", 0.50)] * 2,
}


@dataclass
class Score:
    seconds: float = 0.0
    limit_w: float = 0.0            # mean limit
    draw_w: float = 0.0             # mean package power
    need_w: float = 0.0             # mean of the lowest limit that costs no frames
    over_w: float = 0.0             # mean watts of limit over the need
    starved_pct: float = 0.0        # share of the time the limit cost more than TOLERANCE of the frames
    lost_pct: float = 0.0           # frames lost to the limit, % of what the full limit would give
    longest_starved_s: float = 0.0
    changes_per_min: float = 0.0
    fps: float = 0.0                # frames shown over the run
    slow_pct: float = 0.0           # frames more than 10 % longer than the full limit's frame time, % of all


class Game:
    """Frames of a scenario at the limit a governor sets. All times are the simulation's own (self.t, seconds)."""

    def __init__(self, scenario: list, floor: int = FLOOR_W, ceiling: int = CEILING_W, seed: int = 1,
                 goal_fps: float | None = None):
        self.segs = [(float(d), SCENES[n], float(k)) for d, n, k in scenario]
        self.ends = []
        for d, _, _ in self.segs:
            self.ends.append((self.ends[-1] if self.ends else 0.0) + d)
        self.floor, self.ceiling, self.goal = int(floor), int(ceiling), goal_fps
        self.rng = random.Random(seed)
        self.t = 0.0
        self.limit = float(ceiling)
        self.watts = float(ceiling)                # what the chip sustains now (follows the limit, LAG_S)
        self.changes = 0
        self._frames: list[tuple[float, float]] = []   # (end time s, frame time ms) since the last take()
        self._joules, self._taken_at = 0.0, 0.0
        self._need: dict = {}
        self._acc = dict(limit=0.0, draw=0.0, need=0.0, over=0.0, starved=0.0, lost=0.0, frames=0, slow=0)
        self._run, self._longest = 0.0, 0.0
        self.trace: list[tuple] | None = None      # (t, limit, watts, frame ms, need) per frame, when asked for

    @property
    def length(self) -> float:
        return self.ends[-1]

    def place(self, t: float) -> tuple[Scene, float]:
        i = min(bisect.bisect_right(self.ends, t), len(self.segs) - 1)
        return self.segs[i][1], self.segs[i][2]

    def set_limit(self, watts: float) -> None:
        w = float(max(self.floor, min(self.ceiling, watts)))
        if w != self.limit:
            self.limit, self.changes = w, self.changes + 1

    def best(self, scene: Scene, work: float) -> float:
        """The frame rate the full limit gives here (or the goal, when that is lower): what a governor owes."""
        top = scene.shown(self.ceiling, work)
        return min(top, self.goal) if self.goal else top

    def need(self, scene: Scene, work: float) -> int:
        """The lowest whole-watt limit that gives the best rate within TOLERANCE."""
        key = (id(scene), work)
        if key not in self._need:
            line = self.best(scene, work) * (1 - TOLERANCE) - SLACK_FPS
            self._need[key] = next((w for w in range(self.floor, self.ceiling + 1) if scene.shown(w, work) >= line),
                                   self.ceiling)
        return self._need[key]

    def advance(self, until: float) -> None:
        """Frames until the clock reaches `until`."""
        rng = self.rng
        while self.t < until:
            scene, work = self.place(self.t)
            ms = 1000.0 / scene.rate(self.watts, work) * math.exp(rng.gauss(0.0, scene.jitter))
            if scene.cap:
                ms = max(ms, 1000.0 / scene.cap + rng.gauss(0.0, PRESENT_MS))
            if scene.hitch_per_min and rng.random() < scene.hitch_per_min / 60.0 * ms / 1000.0:
                ms += rng.uniform(40.0, 190.0)
            dt = ms / 1000.0
            if scene.fills or not scene.cap or scene.rate(self.watts, work) < scene.cap:
                draw = self.watts                  # power-bound, or a program that takes all it gets
            else:
                draw = min(self.watts, scene.free_w if scene.free_w is not None else self._watts_for_cap(scene, work))
            # the score: what the limit costs here, against the frame rate the full limit would give
            best, need = self.best(scene, work), self.need(scene, work)
            now = scene.shown(self.watts, work)
            cost = max(0.0, 1.0 - now / best)
            a = self._acc
            a["limit"] += self.limit * dt
            a["draw"] += draw * dt
            a["need"] += need * dt
            a["over"] += max(0.0, self.limit - need) * dt
            a["lost"] += cost * dt
            a["frames"] += 1
            a["slow"] += ms > 1100.0 / best
            if now < best * (1 - TOLERANCE) - SLACK_FPS:
                a["starved"] += dt
                self._run += dt
                self._longest = max(self._longest, self._run)
            else:
                self._run = 0.0
            self._joules += draw * dt
            self.t += dt
            self._frames.append((self.t, ms))
            if self.trace is not None:
                self.trace.append((round(self.t, 3), self.limit, round(self.watts, 2), round(ms, 2), need))
            self.watts += (self.limit - self.watts) * (1.0 - math.exp(-dt / LAG_S))

    def _watts_for_cap(self, scene: Scene, work: float) -> float:
        return float(next((w for w in range(self.floor, self.ceiling + 1) if scene.rate(w, work) >= scene.cap),
                          self.ceiling))

    def take(self) -> tuple[list[tuple[float, float]], float | None]:
        """The frames since the last call, and the package power over that time (None over no time)."""
        frames, self._frames = self._frames, []
        dt = self.t - self._taken_at
        watts = self._joules / dt if dt > 0 else None
        self._joules, self._taken_at = 0.0, self.t
        return frames, watts

    def score(self) -> Score:
        a, t = self._acc, self.t or 1.0
        return Score(seconds=round(self.t, 1), limit_w=round(a["limit"] / t, 1), draw_w=round(a["draw"] / t, 1),
                     need_w=round(a["need"] / t, 1), over_w=round(a["over"] / t, 1),
                     starved_pct=round(100 * a["starved"] / t, 1), lost_pct=round(100 * a["lost"] / t, 1),
                     longest_starved_s=round(self._longest, 1), changes_per_min=round(self.changes * 60 / t, 1),
                     fps=round(a["frames"] / t, 1), slow_pct=round(100 * a["slow"] / max(1, a["frames"]), 2))

    def clipped(self) -> bool:
        """What the chip's "limited by power" flags would say: an emulator that spins is always clipped; anything
        else when the limit gives fewer frames than the full one would."""
        scene, work = self.place(self.t)
        return scene.fills or scene.shown(self.limit, work) < scene.shown(self.ceiling, work) - 0.01


@dataclass
class Recording:
    """A recorded frame log with the limits that were on the chip (tests/data/autotune/*.csv): comment lines, one of
    them "# limit_w: second:watts,...", then one frame time (ms) per line. It stands in for a Game in a replay: the
    governor's set_limit is noted, not obeyed."""
    frame_ms: list[float]
    limits: list[tuple[float, float]]               # (second, watts) as recorded
    t: float = 0.0
    asked: list[tuple[float, float]] = field(default_factory=list)   # (second, watts) the governor asked for
    floor: int = FLOOR_W
    ceiling: int = CEILING_W
    scene: Scene = Scene(curve=((FLOOR_W, 30.0),), fills=True)       # only its defaults are read (CPU and GPU load)
    _i: int = 0                                     # the next frame to hand out, and when the last one ended
    _end: float = 0.0
    _taken_at: float = 0.0

    @classmethod
    def load(cls, path: str) -> "Recording":
        frames, limits = [], []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("# limit_w:"):
                    limits = [(float(a), float(b)) for a, b in (x.split(":") for x in line.split(":", 1)[1].split(","))]
                elif line and not line.startswith("#"):
                    frames.append(float(line))
        return cls(frame_ms=frames, limits=limits)

    @property
    def length(self) -> float:
        return sum(self.frame_ms) / 1000.0

    @property
    def limit(self) -> float:
        return self.limit_at(self.t)

    def limit_at(self, t: float) -> float:
        return [w for s, w in self.limits if s <= t][-1] if self.limits and self.limits[0][0] <= t else self.limits[0][1]

    def levels(self, skip_s: float = 1.5) -> list[tuple[float, float, float, float, float]]:
        """Per recorded limit: (watts, fps, median ms, share of frames more than 10 % over that median, seconds), from
        skip_s after its write to the next write."""
        ends, t = [], 0.0
        for ms in self.frame_ms:
            t += ms / 1000.0
            ends.append(t)
        out = []
        for k, (s, w) in enumerate(self.limits):
            e = self.limits[k + 1][0] if k + 1 < len(self.limits) else ends[-1]
            ft = self.frame_ms[bisect.bisect_left(ends, s + skip_s):bisect.bisect_left(ends, e)]
            if len(ft) < 10:
                continue
            med = sorted(ft)[len(ft) // 2]
            out.append((w, 1000.0 * len(ft) / sum(ft), med, sum(1 for x in ft if x > 1.1 * med) / len(ft), sum(ft) / 1000.0))
        return out

    def set_limit(self, watts: float) -> None:
        if not self.asked or self.asked[-1][1] != watts:
            self.asked.append((round(self.t, 3), float(watts)))

    def place(self, t: float) -> tuple[Scene, float]:
        return self.scene, 1.0

    def clipped(self) -> None:
        return None

    def advance(self, until: float) -> None:
        self.t = until

    def take(self) -> tuple[list[tuple[float, float]], float | None]:
        frames, t, end = [], self._taken_at, self._end       # a frame ends at the sum of the frame times up to it
        while self._i < len(self.frame_ms) and end + self.frame_ms[self._i] / 1000.0 <= self.t:
            end += self.frame_ms[self._i] / 1000.0
            frames.append((end, self.frame_ms[self._i]))
            self._i += 1
        self._end, self._taken_at = end, self.t
        return frames, (self.limit_at((t + self.t) / 2) if self.t > t else None)   # these games drew the whole limit


class _Clock:
    """The simulation's time for clawdeck.autotune (it reads time.monotonic and time.time)."""
    def __init__(self, game):
        self.game = game

    def monotonic(self) -> float:
        return 1000.0 + self.game.t

    time = monotonic


class _Running:
    @staticmethod
    def done() -> bool:
        return False


class Wired:
    """Autotune as the plugin runs it (py_modules/clawdeck/autotune.py): its session, its loop's own pace, the frames
    as its Sampler hands them over (here: every frame, as from mangoapp's log), on the simulation's clock."""

    def __init__(self, target: int | None = None, keep_pct: float | None = None, auto_fps: bool = True):
        self.target, self.keep_pct, self.auto_fps = target, keep_pct, auto_fps
        self.name = f"autotune, target {target}" if target else "autotune, most frames"
        self.period = governor.LOOK_S
        self._saved = None

    def start(self, game, floor: int, ceiling: int) -> None:
        self.game = game
        model = {"tdp": {"min": floor, "battery_max": ceiling, "plugged_max": ceiling, "sppt_max": ceiling + 2}}
        h = hw.Hardware(product="sim", model=model, known=True, kernel="sim", cpu_model="sim", gpu_driver=None,
                        paths=hw.Paths())
        pw = power.Power(h)
        pw.set_tdp = lambda watts, plugged=None: game.set_limit(watts) or True
        self.at = autotune.Autotune(h, pw, log_dir=os.devnull)
        self.at.task = _Running()                 # no asyncio loop here: tick() is the loop
        self._saved = (autotune.time, autotune.PROC)
        autotune.time, autotune.PROC = _Clock(game), os.devnull   # its timers on the simulation's clock; no /proc walk
        self.at.sampler.sample = self._sample
        self.at.start("sim", None, floor, ceiling, plugged=True, keep_pct=self.keep_pct, target=self.target,
                      auto_fps=self.auto_fps)

    def _sample(self) -> autotune.Sample:
        frames, watts = self._seen
        scene, work = self.game.place(self.game.t)
        self.at.sampler.frames_ms = [f[1] for f in frames]
        ms = sum(f[1] for f in frames) / len(frames) if frames else None
        fps = 1000.0 / ms if ms else None
        return autotune.Sample(t=autotune.time.monotonic(), frametime_ms=ms, fps=fps, gpu_mhz=scene.mhz(self.game.limit),
                               gpu_max_mhz=2300 if scene.gpu_mhz else None, cpu_pct=scene.cpu_pct, pkg_w=watts,
                               gpu_busy=scene.busy(self.game.limit, work), shown_fps=fps)

    def tick(self, frames: list, draw_w: float | None) -> None:
        self._seen = (frames, draw_w)
        self.at._step()
        self.period = self.at.session.look_s

    def stop(self) -> None:
        if self._saved:
            autotune.time, autotune.PROC = self._saved
            self._saved, self.at.session = None, None


class New:
    """clawdeck.governor alone, with what the simulator knows of the chip's "limited by power" flags (Autotune doesn't
    read those yet): twice a second while it holds, ten times a second while it watches a write."""

    def __init__(self, target: int | None = None, keep_pct: float = 5.0, auto_fps: bool = True, start_w: int | None = None):
        self.target, self.keep_pct, self.auto_fps, self.start_w = target, keep_pct, auto_fps, start_w
        self.name = f"new, target {target}" if target else "new, most frames"
        self.period = governor.LOOK_S
        self.notes: list[tuple[float, str]] = []

    def start(self, game, floor: int, ceiling: int) -> None:
        self.game = game
        self.cfg = governor.Cfg(floor=floor, ceiling=ceiling, target=self.target, keep_pct=self.keep_pct,
                                auto_fps=self.auto_fps, start_w=self.start_w)
        self.st, act = governor.start(self.cfg, game.t)
        game.set_limit(act.limit_w)

    def tick(self, frames: list, draw_w: float | None) -> None:
        act = governor.step(self.st, governor.Obs(t=self.game.t, frames=frames, draw_w=draw_w,
                                                  clipped=self.game.clipped()), self.cfg)
        if act.limit_w is not None:
            self.game.set_limit(act.limit_w)
        if act.note and (not self.notes or self.notes[-1][1] != act.note):
            self.notes.append((round(self.game.t, 2), act.note))
        self.period = act.look_s

    def stop(self) -> None:
        pass


class Fixed:
    """A limit that never moves: the ends of the scale (the full limit wastes the most, the floor loses the most)."""
    period = 1.0

    def __init__(self, watts: int):
        self.watts, self.name = watts, f"fixed {watts} W"

    def start(self, game, floor: int, ceiling: int) -> None:
        game.set_limit(self.watts)

    def tick(self, frames: list, draw_w: float | None) -> None:
        pass

    def stop(self) -> None:
        pass


def run(governor, game, seconds: float | None = None):
    """The governor on the game (a Game or a Recording) for its whole length, or `seconds`. Returns the game."""
    end = min(game.length, seconds) if seconds else game.length
    governor.start(game, game.floor, game.ceiling)
    try:
        t = 0.0
        while t < end:
            t = min(end, t + governor.period)
            game.advance(t)
            governor.tick(*game.take())
    finally:
        governor.stop()
    return game


def main() -> int:
    ap = argparse.ArgumentParser(description="Autotune against recorded games (no Claw needed)")
    ap.add_argument("--scenario", action="append", choices=sorted(SCENARIOS), help="default: all")
    ap.add_argument("--mode", choices=("most", "target", "both"), default="both")
    ap.add_argument("--target", type=int, help="target fps (default: 60, or 30 for the emulator scenarios)")
    ap.add_argument("--seconds", type=float)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--trace", help="write every frame of the (single) run to this CSV")
    ap.add_argument("--replay", help="a recording (tests/data/autotune/*.csv) instead of a scenario")
    a = ap.parse_args()

    if a.replay:
        rec = run(Wired(target=a.target or 30), Recording.load(a.replay))
        print(f"{a.replay}: {len(rec.frame_ms)} frames, {rec.length:.0f} s")
        print("recorded limit:", ", ".join(f"{s:.0f} s {w:g} W" for s, w in rec.limits))
        print("the governor asked:", ", ".join(f"{s:.0f} s {w:g} W" for s, w in rec.asked))
        return 0

    names = a.scenario or sorted(SCENARIOS)
    print(f"{'scenario':<20} {'governor':<22} {'limit W':>7} {'need W':>6} {'over W':>6} {'draw W':>6} "
          f"{'starved %':>9} {'longest s':>9} {'lost %':>6} {'changes/min':>11} {'fps':>5} {'slow %':>6}")
    for name in names:
        target = a.target or (30 if name.startswith("emulator") else 60)
        govs = [Fixed(CEILING_W)]
        if a.mode in ("most", "both"):
            govs.append(Wired())
        if a.mode in ("target", "both"):
            govs.append(Wired(target=target))
        for g in govs:
            game = Game(SCENARIOS[name], seed=a.seed, goal_fps=getattr(g, "target", None))
            if a.trace and len(names) == 1 and g is govs[-1]:
                game.trace = []
            s = run(g, game, a.seconds).score()
            print(f"{name:<20} {g.name:<22} {s.limit_w:>7} {s.need_w:>6} {s.over_w:>6} {s.draw_w:>6} "
                  f"{s.starved_pct:>9} {s.longest_starved_s:>9} {s.lost_pct:>6} {s.changes_per_min:>11} {s.fps:>5} "
                  f"{s.slow_pct:>6}")
            if game.trace:
                with open(a.trace, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(["t_s", "limit_w", "watts", "frame_ms", "need_w"])
                    w.writerows(game.trace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
