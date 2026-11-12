"""Autotune: for the running game, find the most frames the set TDP gives, share that power between CPU and GPU so
the game gets the most out of it, then find the lowest TDP that still gives nearly as many frames (HARDWARE-FINDINGS
§22). No target fps: with a frame limit (Steam's, or the game's) the most the TDP gives is the limit, so this holds
it with the least power; without one it finds where more watts stop buying frames.

It counts rendered frames, never generated ones. With XeSS frame generation the game presents 2-4 frames for every
one it renders, and MangoHud counts all of them. XeLL's xellSleep runs once per rendered frame; the xess-xmx-linux
shim logs it ("xellSleep stats: 100 calls in W ms (P ms/frame)", IGDEXT_XELL_LOG=1, set by the launch wrapper while
autotune is on), so 1000 / P is the rendered frame rate and MangoHud's rate divided by it is the frame-generation
multiplier. A game without XeLL has no XeSS frame generation, and MangoHud's count is its rendered rate.

Loop (1 Hz while a game runs; one asyncio task, no threads, nothing at all when no game runs). Decisions are taken
on 10 s windows of rendered fps that restart after every change, one change per window:
  1. measure: at the set TDP (the ceiling), the median of three windows is the most this TDP gives (`max_fps`);
  2. balance: one lever at a time toward the side that holds the game back (GPU-bound: CPU ceiling -10 % to give the
     GPU the shared budget, then GPU floor +100 MHz; CPU-bound: CPU boost on, CPU ceiling 100 %, drop a GPU floor).
     A lever stays only if it raises fps by BALANCE_GAIN; otherwise it is undone and balancing ends;
  3. lower: TDP down while the rendered fps stays within the margin of max_fps (KEEP % or KEEP_FPS, whichever is
     more): -BIG_STEP_W while clearly above the line (in the upper half of the band) and the last step was flat, then -1 W. Plateau knee: a step that
     cost <= FLAT_FPS_PER_W per watt is flat, so a miss just after it is taken as noise and lowering goes on; near the
     line a 1 W step that costs > STEEP_FPS_PER_W per watt is past the knee, so it holds there. Otherwise the first
     miss is measured again; a second miss goes back up to the last TDP that held. At a new TDP the balance is tried once more (the bottleneck can
     move as watts drop); if a lever helps, lowering goes on from there;
  4. hold: watch. Below the line for two windows -> +1 W. A sustained shift of SHIFT either way (a new area, changed
     graphics settings), a change of the frame-generation multiplier, or RESCAN_S on the clock -> measure again.
A window far from the game's usual fps (a loading screen, a menu, a pause) or with a load hitch in it is skipped, not
decided on; three in a row are taken as real. Only a finished result (hold, settled) is saved for the game; a session
cut short keeps the last good one. While a game runs the loop runs on the E-cores at nice 10 (quiet.py).
Frames per watt (rendered fps / package power) is shown; it is not what is chased: it improves all the way down to
the minimum TDP, which is why the rule is "within KEEP of the most", not "the best ratio".

Cost, per second while a game runs: the new lines of two small logs, /proc/stat, two GPU clock files and the RAPL
energy counter. The log directory listing and PowerStation's GPU load are cached (LOG_RESCAN_S, BUSY_EVERY_S).

Target fps (optional, Power -> Autotune target): instead of "nearly the most frames", the fewest watts that keep
every second at the fps you picked or over (a minimum, not an average), judged every second on rendered (base) frames. The rules are DeckyTDPTuner's governor (GPL-3,
backend/internal/tdp/governor.go), proven on the Steam Deck, with Intel's PL1 in mind:
  - a miss is any second under G_AT of the target: +G_RAISE W at once (+G_RAISE_BIG far under, proportional up to
    G_STEP_UP), at most every G_RAISE_GAP s, then judged on fresh frames only;
  - lower only after G_LOWER_AFTER s with no change and no miss (G_LOWER_FAST s while G_FAR above), judged on the
    lowest second in that time: a step is taken only if, with frames following watts (Handheld Companion's
    assumption, which overstates the loss), that lowest second would still be G_SAFE of the target. 1 W near the target, far above it half the way to where the watts would just hold it, at most G_STEP_MAX W.
    A frame limit at the target pins fps there: at G_AT with PowerStation's GPU busy under G_BUSY, 1 W at a time;
  - it starts at the set TDP (your pick for the game) and goes from there: up to the model's maximum for this power
    source while under the target, down only as above; raises are proportional (at least +2 W, +4 W far under, at
    most G_STEP_UP). Every reading counts, a low one too. The TDP that held the target for G_SETTLE_S readings in a
    row is saved as the game's own TDP at exit (main.py), so the next start is there;
  - a miss within G_BLAME s of lowering makes the watts before it the floor (a calm scene can't talk it below what the
    heavy ones need: the RE4 report, 2026-11-08); the floor comes down 1 W per G_FLOOR_DECAY s without a miss;
  - at the full TDP and still under: it stays at the full TDP, and after REACH_S says the target is out of reach and
    what the full TDP gives. Your target is never changed (maintainer, 2026-11-12: Auto FPS lowered a 120 fps target
    to 46 in Enshrouded and then took watts away to hold that).
The PL1 time window stays short (LEARN_TAU_US) while it governs, so a lower limit takes effect within seconds. With
frame generation the target counts rendered frames: XeLL's figure, or the presented rate divided by ClawDeck's frame
generation multiplier for the game when XeLL isn't there.

Rules: a manual change (Advanced) pauses it for the session; picking a profile or
moving the TDP slider starts it over from the new limit (main.py); everything applied is reverted on game exit, and
main.py puts the user's limit back.
"""
from __future__ import annotations

import asyncio
import csv
import glob
import logging
import os
import re
import statistics
import time
from dataclasses import dataclass, field

from . import hw, power, shadowblip, sysfs

log = logging.getLogger("clawdeck.autotune")

CPU_MIN_PCT = 60        # autotune never trims the CPU below this: below it, frame pacing suffers in most games
GPU_STEP_MHZ = 100
LOG_RESCAN_S = 30       # look for a new MangoHud log file this often (a new file appears only when a game starts)
BUSY_EVERY_S = 10       # PowerStation's GPU load is read at most this often (one D-Bus call per decision window)
WINDOW_S = 10           # seconds of fps behind every decision
MAX_WINDOWS = 3         # windows whose median is the most the set TDP gives
KEEP_PCT = 5            # lowest TDP whose fps stays within this % of the most (user setting, 2-15)
KEEP_FPS = 2.0          # ... or within this many fps, whichever is more: at 30-60 fps, giving up ~2 fps is fine when
                        # it saves watts (maintainer, 2026-11-04: "lose 1-2 fps, save 5-10 W: save the watts")
BIG_STEP_W = 3          # TDP step while clearly above the line and the last step was flat: 35 -> 20 W in few windows
FLAT_FPS_PER_W = 0.2    # a step that cost at most this per watt is a plateau: a miss right after it is noise
STEEP_FPS_PER_W = 1.0   # near the line, a 1 W step that costs more than this is past the knee: stop there
BALANCE_GAIN = 0.02     # a CPU/GPU lever stays only if it adds this much fps
SHIFT = 0.15            # a sustained change this large (two windows) means a new scene: measure again
RESCAN_S = 1200         # and every this long anyway. Was 300 s: every 5 min it went back to the full TDP and down
                        # again, ~2 min of changes in every 5 (the maintainer saw it as stutter, 2026-11-04)
OFF_LO, OFF_HI = 0.35, 2.5  # a window this far from the game's usual fps is a loading screen, menu or pause: skipped
DIP = 0.4               # ... and so is one with a second below this share of its average (a load hitch in the window)
MAX_SKIPS = 3           # three such windows in a row are real (new settings, a much lighter or heavier game): used
XELL_FRESH_S = 15       # a rendered-frame figure older than this no longer counts
LEARN_TAU_US = 8_000_000  # PL1 time window while learning: shorter than WINDOW_S, so a window sees sustained power
SETTLED_WINDOWS = 3     # windows without a change in hold = learned: the firmware's PL1 window comes back
# target fps (DeckyTDPTuner's governor, see above)
G_WIN = 3               # seconds of history a miss is judged on (theirs: 4 ticks of 0.5 s)
G_RAISE, G_RAISE_BIG = 2, 4
G_STEP_UP = 12          # the most one raise moves: far under the target, 8 W -> 35 W in three raises (~6 s)
G_SETTLE_S = 60         # readings in a row at one TDP, at the target and without a miss: that TDP holds it
G_RAISE_GAP = 2         # s between raises: the last one has to kick in
G_LOWER_AFTER = 12      # s with no change and no miss before lowering near the target
G_LOWER_FAST = 5        # ... and while clearly above it (G_FAR): the PL1 window is 8 s, so a step shows within that
G_FAR = 1.15            # this far above the target: proportional steps down, G_LOWER_FAST apart
G_STEP_MAX = 6          # the most one step down moves (up: G_STEP_UP)
G_SAFE = 1.03           # a step down only if the fps after it, with frames following watts, is still this share of
                        # the target (the target met with room to spare, never lowered into a miss)
G_AT = 0.99             # the minimum, within 1 % (a frame limit at 60 reads 59.9): any second under it is a miss;
                        # at it, lowered 1 W only with GPU headroom (G_BUSY)
G_BLAME = 20            # s: a miss this soon after lowering blames the lowering (theirs 5 s; Intel's PL1 window here)
G_FLOOR_DECAY = 300     # s without a miss: the learned floor comes down 1 W (theirs 120 s: one dip every 2 min
                        # in a game that stays heavy; 5 min here)
G_BUSY = 85             # GPU busy under this % is headroom (when PowerStation reports it)
REACH_S = 60            # s at the full TDP and still under: the target is out of reach (said, never changed)
MIN_TARGET = 20
XELL_RE = re.compile(rb"xellSleep stats: 100 calls in (\d+) ms \(([\d.]+) ms/frame\)")


@dataclass
class Sample:
    t: float
    frametime_ms: float | None
    fps: float | None               # rendered frames per second (what every decision uses)
    gpu_mhz: int | None
    gpu_max_mhz: int | None
    cpu_pct: float | None
    pkg_w: float | None
    gpu_busy: int | None = None     # PowerStation's GpuBusyPercent; None when PowerStation doesn't run
    shown_fps: float | None = None  # presented frames per second (MangoHud), generated frames included
    source: str = "presented"       # "rendered" (XeLL's xellSleep) or "presented" (MangoHud, no frame generation seen)


class Sampler:
    """Reads the numbers. Frame time comes from MangoHud's CSV log: the game is launched with
    MANGOHUD_CONFIG=output_folder=<dir>,log_interval=1000,autostart_log=1 (ClawDeck sets this through its launch
    wrapper, tools/clawdeck-launch.sh). Without a log, frame time is None and autotune only reports."""

    def __init__(self, hardware: hw.Hardware, log_dir: str):
        self.p = hardware.paths
        self.log_dir = log_dir
        self.xell_log: str | None = None       # the running game's drive_c/igdext_xell.log (set_prefix)
        self._xell_pos = 0
        self._xell: tuple[float, float] | None = None   # (monotonic time, rendered fps) of the newest stats line
        self._last_energy: tuple[float, int] | None = None
        self._last_stat: tuple[int, int] | None = None
        self._log_pos: dict[str, int] = {}
        # the two reads that cost more than a file read are cached: the log directory listing (MangoHud starts a new
        # file per launch, not per second) and PowerStation's GPU load (a D-Bus call, needed once per decision)
        self._log: str | None = None
        self._log_checked = 0.0
        self._busy: tuple[float, int | None] = (-1e9, None)
        self._busy_retry = 0.0

    def _newest_log(self) -> str | None:
        now = time.monotonic()
        if self._log and now - self._log_checked < LOG_RESCAN_S and os.path.exists(self._log):
            return self._log
        self._log_checked = now
        files = glob.glob(os.path.join(self.log_dir, "*.csv"))
        self._log = max(files, key=os.path.getmtime) if files else None
        return self._log

    def frametime(self) -> tuple[float | None, float | None]:
        f = self._newest_log()
        if not f:
            return None, None
        try:
            with open(f, "rb") as fh:
                start = self._log_pos.get(f, 0)
                fh.seek(start)
                data = fh.read()
        except OSError:
            return None, None
        # MangoHud may be half-way through writing a row: read only up to the last complete line, the rest next time
        end = data.rfind(b"\n")
        if end < 0:
            return None, None
        self._log_pos[f] = start + end + 1
        rows = data[:end + 1].decode("utf-8", errors="replace")
        # every row written since the last read (MangoHud writes one per log_interval), averaged: nothing is skipped
        fps_rows, ft_rows = [], []
        for row in csv.reader(rows.splitlines()):
            if len(row) >= 2 and row[0].replace(".", "", 1).isdigit():
                try:
                    fps_rows.append(float(row[0]))
                    ft_rows.append(float(row[1]))
                except ValueError:
                    continue
        if not fps_rows:
            return None, None
        return sum(ft_rows) / len(ft_rows), sum(fps_rows) / len(fps_rows)

    def set_prefix(self, xell_log: str | None) -> None:
        """The game's prefix log where the shim writes XeLL's frame timing; read from its end, new lines only."""
        self.xell_log, self._xell = xell_log, None
        try:
            self._xell_pos = os.path.getsize(xell_log) if xell_log else 0
        except OSError:
            self._xell_pos = 0

    def rendered_fps(self) -> float | None:
        """Rendered frames per second from the newest "xellSleep stats" line (one per 100 rendered frames), or None
        when the game doesn't use XeLL or the line is stale."""
        if self.xell_log:
            try:
                # the launch wrapper rotates the log when the game starts, possibly after autotune began reading it
                if os.path.getsize(self.xell_log) < self._xell_pos:
                    self._xell_pos = 0
                with open(self.xell_log, "rb") as fh:
                    fh.seek(self._xell_pos)
                    data = fh.read()
            except OSError:
                data = b""
            end = data.rfind(b"\n")
            if end >= 0:
                self._xell_pos += end + 1
                hits = XELL_RE.findall(data[:end + 1])
                if hits:
                    ms = float(hits[-1][1])
                    if ms > 0:
                        self._xell = (time.monotonic(), 1000.0 / ms)
        if self._xell and time.monotonic() - self._xell[0] <= XELL_FRESH_S:
            return self._xell[1]
        return None

    def package_power(self) -> float | None:
        if not self.p.rapl_energy:
            return None
        e = sysfs.read_int(self.p.rapl_energy)
        now = time.monotonic()
        if e is None:
            return None
        prev = self._last_energy
        self._last_energy = (now, e)
        if not prev or now - prev[0] <= 0:
            return None
        de = e - prev[1]
        if de < 0:   # counter wrap
            return None
        return de / 1e6 / (now - prev[0])

    def cpu_pct(self) -> float | None:
        try:
            with open("/proc/stat", "r", encoding="utf-8") as f:
                parts = f.readline().split()
        except OSError:
            return None
        vals = [int(x) for x in parts[1:8]]
        idle = vals[3] + vals[4]
        total = sum(vals)
        prev = self._last_stat
        self._last_stat = (total, idle)
        if not prev or total == prev[0]:
            return None
        return 100.0 * (1.0 - (idle - prev[1]) / (total - prev[0]))

    def gpu_busy(self) -> int | None:
        """PowerStation's GPU load, at most once every BUSY_EVERY_S; after a miss, retried once a minute only."""
        now = time.monotonic()
        t, v = self._busy
        if now - t < BUSY_EVERY_S or now < self._busy_retry:
            return v
        v = shadowblip.gpu_busy_percent()
        self._busy = (now, v)
        if v is None:
            self._busy_retry = now + 60
        return v

    def sample(self) -> Sample:
        ft, shown = self.frametime()
        rendered = self.rendered_fps()
        fps, src = (rendered, "rendered") if rendered else (shown, "presented")
        return Sample(t=time.monotonic(), frametime_ms=(1000.0 / fps) if fps else ft, fps=fps,
                      gpu_mhz=sysfs.read_int(self.p.gpu_cur_freq) if self.p.gpu_cur_freq else None,
                      gpu_max_mhz=sysfs.read_int(self.p.gpu_max_freq) if self.p.gpu_max_freq else None,
                      cpu_pct=self.cpu_pct(), pkg_w=self.package_power(), gpu_busy=self.gpu_busy(),
                      shown_fps=shown, source=src)


def summary(s: Session) -> str:
    """One line for the toast at game exit: what autotune found, or that it didn't get far enough to save it."""
    l = s.learned
    if not s.settled or not l.max_fps:
        return "Autotune was still learning; nothing new saved. It goes on next time."
    fps = l.fps or l.max_fps
    saved = (l.ceiling or s.ceiling_tdp) - l.tdp
    return (f"Autotune saved {l.tdp} W of your {l.ceiling or s.ceiling_tdp} W: {fps:.0f} of {l.max_fps:.0f} fps"
            + (f", saves {saved} W" if saved > 0 else ""))


def classify(s: Sample) -> str:
    if s.gpu_busy is not None:      # a real load figure beats guessing from the clock
        if s.gpu_busy >= 90 and (s.cpu_pct is None or s.cpu_pct < 60):
            return "gpu"
        if s.cpu_pct is not None and s.cpu_pct > 85 and s.gpu_busy < 80:
            return "cpu"
        return "balanced"
    gpu_pinned = s.gpu_mhz is not None and s.gpu_max_mhz and s.gpu_mhz >= 0.95 * s.gpu_max_mhz
    if gpu_pinned and (s.cpu_pct is None or s.cpu_pct < 60):
        return "gpu"
    if s.cpu_pct is not None and s.cpu_pct > 85 and not gpu_pinned:
        return "cpu"
    return "balanced"


@dataclass
class Learned:
    """What a game ended up with, saved per game and power source: the levers together, and the reference fps."""
    tdp: int
    cpu_max_pct: int = 100
    gpu_min_mhz: int | None = None
    boost: bool = True              # CPU boost (intel_pstate no_turbo = 0)
    gpu_profile: str | None = None  # xe power_profile autotune chose (base / power_saving); None = left as it was
    bound: str = "balanced"
    max_fps: float | None = None    # the most the ceiling gave, rendered fps
    fps: float | None = None        # what the chosen TDP gives
    ceiling: int | None = None      # the ceiling max_fps was measured at (a different ceiling: measure again)
    samples: int = 0
    target: int | None = None       # target fps mode: your target this was found for
    held: int | None = None         # ... the TDP that held it for G_SETTLE_S readings (main.py: the game's next start)


NO_FPS_S = 30          # seconds without any frame rate before autotune gives up on a game


@dataclass
class Session:
    appid: str
    floor_tdp: int
    ceiling_tdp: int
    learned: Learned
    keep_pct: float = KEEP_PCT
    settle: int = WINDOW_S
    phase: str = "measure"          # measure | balance | lower | hold
    paused: bool = False
    window: list[float] = field(default_factory=list)   # rendered fps, per second, since the last change
    watts: list[float] = field(default_factory=list)    # package power over the same seconds
    shown: list[float] = field(default_factory=list)    # presented fps over the same seconds
    max_runs: list[float] = field(default_factory=list) # window averages while measuring
    misses: int = 0
    balance_undo: tuple | None = None   # (lever, previous value, fps before) of the lever on trial
    balance_tried: set = field(default_factory=set)
    multiplier: float | None = None     # presented / rendered when frame generation is on
    measured_at: float | None = None
    last_change_at: float | None = None
    last_action: str | None = None
    last_delta: str | None = None
    avg_fps: float | None = None
    low_fps: float | None = None
    fps_per_w: float | None = None
    source: str = "presented"
    thermal_cap: int | None = None      # the temperature guard stepped the TDP down to this (thermal.py): stay under it
    no_fps_s: int = 0                   # seconds in a row without a frame rate before the first one
    no_frames: bool = False             # gave up: this game's frame rate can't be read
    quiet: int = 0                      # windows in a row without a change (SETTLED_WINDOWS in hold = learned)
    skips: int = 0                      # windows in a row skipped as not gameplay (loading, menus, pause)
    settled: bool = False               # reached hold and stayed there SETTLED_WINDOWS: what it found is worth saving
    short_tau: bool = False             # the PL1 time window is LEARN_TAU_US right now (findings §22.2)
    tau_refused: bool = False           # the firmware refused it once: don't retry every window
    eff_trial: bool = False             # GPU efficiency mode on trial at the TDP floor (judged on the next window)
    eff_tried: bool = False             # tried once this measurement
    last_good: tuple | None = None      # (tdp, fps) of the last TDP that held the line: where a miss goes back to
    fine: bool = False                  # a big step overshot once: 1 W steps from here
    history: list[Sample] = field(default_factory=list)
    fg: int = 0                         # ClawDeck's frame generation multiplier for the game (0: none)
    # target fps mode
    target: float | None = None         # your target fps (never changed by autotune)
    max_tdp: int | None = None          # target mode: the most it may raise to (the model's maximum on this power source)
    g_steady: int = 0                   # readings in a row at g_steady_tdp, at the target and without a miss
    g_steady_tdp: int | None = None
    g_hist: list[float] = field(default_factory=list)
    g_last_change: float = 0.0
    g_last_miss: float = 0.0
    g_last_drop: float | None = None
    g_drop_from: int = 0
    g_floor: int = 0
    g_floor_at: float = 0.0
    g_maxed: list[float] = field(default_factory=list)
    g_maxed_since: float | None = None
    best_fps: float | None = None       # what the full TDP gave when the target was out of reach
    unreachable: bool = False


class Autotune:
    def __init__(self, hardware: hw.Hardware, pw: power.Power, log_dir: str, csv_log: str | None = None):
        self.hw = hardware
        self.power = pw
        self.sampler = Sampler(hardware, log_dir)
        self.csv_log = csv_log
        self.session: Session | None = None
        self.plugged: bool | None = None
        self.task: asyncio.Task | None = None
        self.enabled = False

    # ---- lifecycle ---------------------------------------------------------------------------------------------------
    def start(self, appid: str, learned: dict | None, floor_tdp: int, ceiling_tdp: int, plugged: bool | None = None,
              keep_pct: float | None = None, xell_log: str | None = None, target: int | None = None,
              fg: int = 0, max_tdp: int | None = None) -> None:
        """Start for a game. A saved result measured at the same ceiling starts in hold (checked within RESCAN_S);
        anything else starts by measuring the most the ceiling gives."""
        self.plugged = plugged
        known = {k: v for k, v in (learned or {}).items() if k in Learned.__dataclass_fields__}
        reuse = known.get("ceiling") == ceiling_tdp and known.get("max_fps") and known.get("tdp") is not None
        l = Learned(**known) if reuse else Learned(tdp=ceiling_tdp, cpu_max_pct=known.get("cpu_max_pct", 100),
                                                   gpu_min_mhz=known.get("gpu_min_mhz"), boost=known.get("boost", True))
        l.tdp = max(floor_tdp, min(int(l.tdp), ceiling_tdp))
        l.ceiling = ceiling_tdp
        s = Session(appid=appid, floor_tdp=floor_tdp, ceiling_tdp=ceiling_tdp, learned=l,
                    keep_pct=float(keep_pct or KEEP_PCT), phase="hold" if reuse else "measure")
        s.measured_at = time.monotonic() if reuse else None
        s.quiet = SETTLED_WINDOWS if reuse else 0           # a saved result is already learned: keep the boost window
        s.settled = bool(reuse)
        s.fg = int(fg or 0)
        s.max_tdp = None if max_tdp is None else max(ceiling_tdp, int(max_tdp))
        self.session = s
        self.sampler.set_prefix(xell_log)
        if target:
            self._start_target(s, int(target))
        self._apply(l)
        self._tau(s, bool(target) or not reuse)
        if self.task is None or self.task.done():
            self.task = asyncio.get_event_loop().create_task(self._loop())

    def _start_target(self, s: Session, target: int) -> None:
        """Target fps mode: from the set TDP (your pick for the game), up while under the target, down only while it
        is met with room to spare. It used to start mid-range (22 W of a 35 W pick), under the target in a heavy game
        from the first second, and never went above the pick."""
        l = s.learned
        s.phase, s.target = "target", float(target)
        l.tdp, l.target, l.held = min(s.ceiling_tdp, self._top(s)), target, None
        s.g_steady, s.g_steady_tdp = 0, None
        now = time.monotonic()
        s.g_hist, s.g_last_change, s.g_last_miss, s.g_last_drop = [], now, now, None
        s.g_floor, s.g_floor_at = s.floor_tdp, now
        s.g_maxed, s.g_maxed_since, s.unreachable, s.best_fps = [], None, False, None
        s.settled = False
        s.last_action = f"holding {s.target:.0f} fps from {l.tdp} W"

    def set_target(self, target: int | None) -> None:
        """The target was changed while a game runs: a new target starts over from the full TDP; none goes back to the
        most-frames mode (measure again)."""
        s = self.session
        if not s:
            return
        if target:
            self._start_target(s, int(target))
            self._tdp(s.learned, min(s.learned.tdp, self._top(s)))
            self._tau(s, True)
            s.paused = False
        elif s.phase == "target":
            s.target = s.learned.target = None
            self._remeasure(s, "target switched off")
            s.paused = False

    def set_ceiling(self, watts: int) -> int | None:
        """The user picked a profile or moved the TDP slider: the new set TDP. The most it gives has to be measured
        again, so autotune goes back to it at once and starts over (and hands control back after a manual change).
        Returns the watts applied."""
        s = self.session
        if not s:
            return None
        s.ceiling_tdp = max(s.floor_tdp, int(watts))
        s.thermal_cap = None                              # the user's new choice replaces the guard's step
        if s.phase == "target":                           # your new pick: from there again
            self._tdp(s.learned, s.ceiling_tdp)
            s.g_hist, s.g_last_change, s.g_maxed_since, s.unreachable = [], time.monotonic(), None, False
            s.g_floor = min(s.g_floor, s.ceiling_tdp)
        else:
            self._remeasure(s, "TDP set to %d W" % s.ceiling_tdp)
        s.paused = False
        return s.learned.tdp

    def set_floor(self, watts: int) -> None:
        """The lowest TDP autotune may use (the model minimum unless the user sets one)."""
        s = self.session
        if not s:
            return
        lo = self.hw.model["tdp"]["min"]
        s.floor_tdp = max(lo, min(int(watts), s.ceiling_tdp))
        if s.learned.tdp < s.floor_tdp:
            self._tdp(s.learned, s.floor_tdp)

    def set_keep(self, pct: float) -> None:
        if self.session:
            self.session.keep_pct = max(1.0, min(float(pct), 25.0))

    def thermal_step(self, watts: int) -> bool:
        """The temperature guard (thermal.py) brings the TDP down: from now on autotune works under `watts`. What was
        being measured or tried at the old TDP no longer counts, so that starts over at the new one."""
        s = self.session
        if not s:
            return False
        s.thermal_cap = int(watts)
        l = s.learned
        if s.balance_undo:                                # a lever on trial can't be judged across a TDP change
            s.balance_undo[1]()
            s.balance_undo = None
        if not self._tdp(l, s.thermal_cap):
            return False
        s.window, s.watts, s.shown, s.misses = [], [], [], 0
        if s.phase == "measure":
            s.max_runs = []
        self._did(s, f"thermal: stepped to {l.tdp} W", "thermal")
        return True

    def thermal_release(self, watts: int) -> bool:
        """The package cooled down: the guard lets autotune use up to `watts` again (the set TDP: the step is gone and
        autotune measures again what the full TDP gives). Autotune's own TDP isn't raised here; it climbs if the game
        needs it (hold: +1 W below the line), or starts over at the full TDP."""
        s = self.session
        if not s or s.thermal_cap is None:
            return False
        if watts >= self._max(s):
            s.thermal_cap = None
            if s.phase == "target":                       # it climbs again by itself if the target needs it
                self._did(s, "thermal: cooled, the full TDP allowed again", "thermal")
            else:
                self._remeasure(s, "cooled down")
        else:
            s.thermal_cap = int(watts)
            self._did(s, f"thermal: cooled, up to {s.thermal_cap} W allowed", "thermal")
        return True

    def _max(self, s: Session) -> int:
        """The most autotune may use: the set TDP, or in target mode the model's maximum (the set TDP is where it starts)."""
        return s.max_tdp if s.phase == "target" and s.max_tdp else s.ceiling_tdp

    def _top(self, s: Session) -> int:
        """... or less while the temperature guard holds it down."""
        return self._max(s) if s.thermal_cap is None else min(self._max(s), s.thermal_cap)

    def limit_w(self) -> int | None:
        """Where the temperature guard's steps back up stop (main.py)."""
        return self._max(self.session) if self.session else None

    def pause(self, reason: str = "manual change") -> None:
        """Never fight the user: a manual power change stops autotune for the rest of this session (values the user
        set stay; what was found so far is still saved at game exit)."""
        if self.session and not self.session.paused:
            self.session.paused = True
            log.info("autotune paused: %s", reason)

    def running(self) -> bool:
        return self.session is not None

    def stop(self, revert: bool = True) -> dict | None:
        """End the session; what it learned is returned for the game's profile. revert=False leaves the power as it is
        for the caller, which reverts and sets the user's limit again itself (main.py)."""
        s = self.session
        self.session = None
        self.sampler.set_prefix(None)
        if s:
            if revert:
                self.power.revert_all()
            return {"learned": s.learned.__dict__, "samples": len(s.history), "settled": s.settled,
                    "thermal": s.thermal_cap is not None, "summary": summary(s)}
        return None

    def _apply(self, l: Learned) -> None:
        self.power.set_tdp(l.tdp, self.plugged)
        self.power.set_cpu_max_pct(l.cpu_max_pct)
        if self.hw.paths.cpu_no_turbo and self.power.turbo() != l.boost:
            self.power.set_cpu_turbo(l.boost)
        if l.gpu_min_mhz:
            self.power.set_gpu_window(l.gpu_min_mhz, None)
        if l.gpu_profile:
            self._gpu_profile(l, l.gpu_profile, force=True)

    # ---- the loop ----------------------------------------------------------------------------------------------------
    async def _loop(self) -> None:
        while self.session is not None:
            try:
                self._step()
            except Exception as e:   # never let a sampling error kill the loop
                log.exception("autotune step failed: %s", e)
            await asyncio.sleep(1.0)

    def _step(self) -> None:
        s = self.session
        if s is None:
            return
        smp = self.sampler.sample()
        s.history.append(smp)
        if len(s.history) > 600:
            del s.history[:-600]
        if self.csv_log:
            self._log(s, smp)
        if s.paused:
            self._tau(s, False)                          # the user has the controls: the firmware's window again
        if not smp.fps and not s.paused and s.learned.samples == 0:
            # no frame rate at all: a game without XeSS / XeLL (the shim's log) whose MangoHud log isn't written
            # (Onimusha without OptiScaler sat in "measure" for minutes, 2026-11-05). Give up: the limit you picked
            # stays, nothing more is changed, and the panel says why
            s.no_fps_s += 1
            if s.no_fps_s >= NO_FPS_S:
                s.paused, s.no_frames = True, True
                self._tau(s, False)
                log.info("autotune: no frame rate from %s after %d s: it stays out of this game", s.appid, NO_FPS_S)
        if not smp.fps or s.paused:
            return
        base = smp.fps
        if smp.source == "presented" and s.fg >= 2:      # frame generation without XeLL's figure: rendered frames
            base = smp.fps / s.fg
            s.multiplier = float(s.fg)
        s.learned.samples += 1
        s.source = smp.source if smp.source == "rendered" or s.fg < 2 else "frame-gen pick"
        if s.phase == "target":
            self._govern(s, base, smp)
            self._tau(s, True)
            return
        if s.phase in ("lower", "hold") and self._fast_miss(s, base):
            return
        s.window.append(base)
        if smp.pkg_w:
            s.watts.append(smp.pkg_w)
        if smp.shown_fps:
            s.shown.append(smp.shown_fps)
        if len(s.window) < s.settle:
            return                                       # judge a setting on a full window of its own numbers
        fps = sum(s.window) / len(s.window)
        if self._not_gameplay(s, fps, min(s.window)):
            s.window, s.watts, s.shown = [], [], []
            return
        s.avg_fps, s.low_fps = fps, min(s.window)
        s.fps_per_w = fps / (sum(s.watts) / len(s.watts)) if s.watts else None
        self._multiplier(s)
        s.window, s.watts, s.shown = [], [], []
        l = s.learned
        l.bound = classify(smp)
        changed_at = s.last_change_at
        if s.phase == "measure":
            self._measure(s, fps)
        elif s.phase == "balance":
            self._balance(s, fps)
        elif s.phase == "lower":
            self._lower(s, fps)
        else:
            self._hold(s, fps)
        s.quiet = 0 if s.last_change_at != changed_at else s.quiet + 1
        if s.phase == "hold" and s.quiet >= SETTLED_WINDOWS:
            s.settled = True
        self._tau(s, s.phase != "hold" or s.quiet < SETTLED_WINDOWS)

    def _fast_miss(self, s: Session, fps: float) -> bool:
        """Most-frames mode, lowering or holding: a heavy scene (last G_WIN s and the last second clearly under the
        line) gets watts back at once instead of after two 10 s windows and 1 W at a time (the RE4 report:
        performance dropped for minutes after autotune had lowered the TDP in a calm scene). The TDP that held before
        the last step down is the floor from then on: lowering stops."""
        l, now = s.learned, time.monotonic()
        ref = l.fps or l.max_fps
        if not ref or fps < ref * OFF_LO:                 # loading, menu, pause: not judged here
            return False
        s.g_hist.append(fps)
        del s.g_hist[:-G_WIN]
        lim = self._line(s) - self._margin(s)            # clearly under: one more margin below the line
        win = sum(s.g_hist) / len(s.g_hist)
        if not (len(s.g_hist) >= G_WIN and win < lim and fps < lim) or l.tdp >= self._top(s):
            return False
        if now - s.g_last_change < G_RAISE_GAP:
            return True
        back = s.last_good[0] if s.last_good and s.last_good[0] > l.tdp else 0
        w, was = min(self._top(s), max(back, l.tdp + (G_RAISE_BIG if win < ref * 0.75 else G_RAISE))), l.tdp
        if self._tdp(l, w):
            s.g_last_change, s.g_hist = now, []
            s.window, s.watts, s.shown, s.misses = [], [], [], 0
            s.floor_tdp = max(s.floor_tdp, min(w, self._top(s)))   # no lowering under this for the session
            s.phase = "hold"
            self._did(s, f"{win:.0f} fps, well under {self._line(s):.0f}: back up to {w} W at once", f"+{w - was} W")
        return True

    def _govern(self, s: Session, fps: float, smp: Sample) -> None:
        """Target fps mode, every second (see the module docstring). The target is a minimum: every second at it or
        over, with the fewest watts that keep it there (maintainer, 2026-11-12: "60 fps target means 60 fps minimum")."""
        l, now, T = s.learned, time.monotonic(), s.target or 0
        if not T:
            return
        # every reading counts, a low one too: under the target is under it, loading screen or not. Raising is the
        # safe side; skipping fps under 35 % of the target as "a menu" kept a game at 8 W and 11-28 fps for 40 s
        # (Order of the Sinking Star, target 120, 2026-11-12)
        s.g_hist.append(fps)
        del s.g_hist[:-G_LOWER_AFTER]                     # since the last change, at most G_LOWER_AFTER s
        recent = s.g_hist[-G_WIN:]
        win = sum(recent) / len(recent)
        s.avg_fps, s.low_fps = win, min(recent)
        if smp.pkg_w:
            s.fps_per_w = fps / smp.pkg_w
        top = self._top(s)
        if fps < T * G_AT:
            # under the minimum this second: watts at once. It used to take a 3 s average under 93 % (56 fps for 60),
            # and 56-59 fps got +1 W only after 12 s
            s.g_last_miss, s.g_steady = now, 0
            blamed = s.g_last_drop is not None and now - s.g_last_drop < G_BLAME and s.g_drop_from > l.tdp
            if blamed and s.g_drop_from > s.g_floor:
                s.g_floor, s.g_floor_at = s.g_drop_from, now
            if l.tdp >= top:
                self._out_of_reach(s, fps, now)
                return
            if now - s.g_last_change < G_RAISE_GAP:
                return
            # watts roughly follow frames: the share missing, at least +2 W (+4 W far under), at most G_STEP_UP
            need = -(-int(l.tdp * (T / max(fps, 1.0) - 1) * 10) // 10)
            step = max(G_RAISE_BIG if fps < T * 0.75 else G_RAISE, min(G_STEP_UP, need))
            w, was = min(top, l.tdp + step), l.tdp
            if blamed:
                w, s.g_last_drop = min(top, max(s.g_drop_from, l.tdp + 1)), None   # exactly back to what held
            if self._tdp(l, w):
                s.g_last_change, s.g_hist = now, []
                self._did(s, f"{fps:.0f} fps, under the {T:.0f} fps minimum: raised TDP to {w} W", f"+{w - was} W")
            return
        s.g_maxed_since, s.unreachable = None, False
        # the floor comes down only when the game got lighter (well above the target, or the GPU clearly idle under a
        # frame limit): a game that stays as heavy keeps its watts locked instead of a probe dip every few minutes
        lighter = win >= T * G_FAR or (smp.gpu_busy is not None and smp.gpu_busy < 60)
        if (lighter and s.g_floor > s.floor_tdp and now - s.g_last_miss > G_FLOOR_DECAY
                and now - s.g_floor_at > G_FLOOR_DECAY):
            s.g_floor, s.g_floor_at = s.g_floor - 1, now
        # a TDP holds the target once G_SETTLE_S seconds in a row at it were all at the minimum or over. It used to be a
        # minute on the clock since the last change and miss, which counted skipped readings too: a session marked
        # settled at 8 W and 15 fps saved the 23 W an exit menu at 400 fps had brought it to (OotSS, 2026-11-12)
        s.g_steady = s.g_steady + 1 if s.g_steady_tdp == l.tdp else 1
        s.g_steady_tdp = l.tdp
        if s.g_steady >= G_SETTLE_S:
            s.settled, l.held = True, l.tdp
        floor = max(s.floor_tdp, s.g_floor)
        lowest = min(s.g_hist)
        far = lowest >= T * G_FAR
        wait = G_LOWER_FAST if far else G_LOWER_AFTER
        # judged on the lowest second since the last change (up to G_LOWER_AFTER s), not the average: the most a step
        # may take keeps that lowest second G_SAFE over the minimum, with frames following watts. A frame limit at the
        # target pins fps there, so it can never be exceeded: at the target with the GPU under G_BUSY, the limit holds
        # it, not the power, and 1 W is safe (a miss right after it undoes the step and makes it the floor)
        safe = int(l.tdp * (1 - T * G_SAFE / lowest))
        if safe < 1 and lowest >= T * G_AT and smp.gpu_busy is not None and smp.gpu_busy < G_BUSY:
            safe = 1
        if (l.tdp > floor and len(s.g_hist) >= wait and safe >= 1
                and now - s.g_last_change >= wait and now - s.g_last_miss >= wait):
            # far above: half the way to where the watts would just hold the target, at most G_STEP_MAX; near it: 1 W
            step = min(safe, max(1, min(G_STEP_MAX, int((l.tdp - l.tdp * T / lowest) / 2))) if far else 1)
            w, was = max(floor, l.tdp - step), l.tdp
            if self._tdp(l, w):
                s.g_drop_from, s.g_last_drop, s.g_last_change, s.g_hist = was, now, now, []
                self._did(s, f"{lowest:.0f} fps at the lowest holds the {T:.0f} fps minimum: lowered TDP to {w} W",
                          f"−{was - w} W")

    def _out_of_reach(self, s: Session, win: float, now: float) -> None:
        """At the full TDP and still under the target: it stays there. After REACH_S it says the target is out of reach
        and what the full TDP gives (refreshed every REACH_S); the target stays yours."""
        if s.g_maxed_since is None:
            s.g_maxed_since, s.g_maxed = now, []
        s.g_maxed.append(win)
        if now - s.g_maxed_since < REACH_S:
            return
        s.best_fps = statistics.median(s.g_maxed)
        s.g_maxed_since = None
        if not s.unreachable:
            s.unreachable = True
            self._did(s, f"{s.target or 0:.0f} fps is out of reach: {self._top(s)} W gives {s.best_fps:.0f} fps", "out of reach")

    def _tau(self, s: Session, learning: bool) -> None:
        """RAPL averages PL1 over a 56 s window: right after a change the package can still boost, and a 10 s window
        would judge the boost. While learning, an 8 s window; once learned, the firmware's again so play keeps its
        short bursts (findings §22.2). Both through sysfs.write, so a revert restores it in any case."""
        if learning and not s.short_tau and not s.tau_refused and self.hw.paths.rapl_pl1_window:
            if self.power.set_pl1_window(LEARN_TAU_US):
                s.short_tau = True
                log.info("autotune learning: PL1 time window %d s", LEARN_TAU_US // 1_000_000)
            else:
                s.tau_refused = True
                self.power.restore_pl1_window()
                log.warning("autotune: the firmware did not take a %d s PL1 window; keeping its own", LEARN_TAU_US // 1_000_000)
        elif not learning and s.short_tau:
            self.power.restore_pl1_window()
            s.short_tau = False
            log.info("autotune settled: PL1 time window back to the firmware's (%s us)", self.power.pl1_window_us())

    def _not_gameplay(self, s: Session, fps: float, low: float) -> bool:
        """A loading screen, a menu or a pause is not the game: deciding on it is how 11 fps in a loading screen once
        sent the TDP back up (Onimusha, 2026-11-04). Such a window is skipped, unless MAX_SKIPS in a row say it is the
        game now."""
        ref = s.learned.fps or s.learned.max_fps
        off = bool(ref) and not (ref * OFF_LO <= fps <= ref * OFF_HI)
        if (off or low < fps * DIP) and s.skips + 1 < MAX_SKIPS:
            s.skips += 1
            log.info("autotune: skipped a %.0f fps window (low %.0f%s): loading, menu or pause", fps, low,
                     f", usual {ref:.0f}" if ref else "")
            return True
        s.skips = 0
        return False

    # ---- the phases ----------------------------------------------------------------------------------------------
    def _margin(self, s: Session) -> float:
        m = s.learned.max_fps or 0
        return max(m * s.keep_pct / 100, KEEP_FPS) if m else 0.0

    def _line(self, s: Session) -> float:
        return (s.learned.max_fps or 0) - self._margin(s)

    def _did(self, s: Session, action: str, delta: str) -> None:
        s.last_action, s.last_delta, s.last_change_at = action, delta, time.monotonic()
        log.info("autotune %s (%s-bound, %s fps): %s", s.phase, s.learned.bound, f"{s.avg_fps:.0f}" if s.avg_fps else "?", action)

    def _remeasure(self, s: Session, why: str) -> None:
        l = s.learned
        s.phase, s.max_runs, s.misses, s.balance_undo = "measure", [], 0, None
        s.balance_tried, s.eff_trial, s.eff_tried, s.last_good, s.fine = set(), False, False, None, False
        s.window, s.watts, s.shown = [], [], []
        top = self._top(s)
        if l.tdp != top:
            self._tdp(l, top)
        l.ceiling = s.ceiling_tdp
        s.quiet = 0
        self._tau(s, True)                               # learning again: sustained power only, from the first window
        self._did(s, f"measuring the most {top} W gives ({why})", f"{top} W")

    def _measure(self, s: Session, fps: float) -> None:
        s.max_runs.append(fps)
        if len(s.max_runs) < MAX_WINDOWS:
            return
        l = s.learned
        l.max_fps = statistics.median(s.max_runs)
        l.fps = l.max_fps
        s.measured_at = time.monotonic()
        s.phase, s.balance_tried = "balance", set()
        self._did(s, f"{self._top(s)} W gives {l.max_fps:.0f} fps", "measured")

    def _levers(self, s: Session) -> list:
        """(name, apply, undo, words) for the next untried lever toward the side that holds the game back."""
        l, p = s.learned, self.hw.paths
        out = []
        if l.bound == "gpu":
            # first: let the GPU clock up as eagerly as it can (efficiency mode off) - before any floor or CPU trade
            if self._gpu_active() == "power_saving":
                out.append(("gpu_base", lambda: self._gpu_profile(l, "base"), lambda: self._gpu_profile(l, "power_saving"),
                            "turned GPU efficiency mode off (clocks up faster)", "GPU efficiency off"))
            # no CPU-ceiling trim (max_perf_pct): no proven tool does it and it hurts frame pacing (audit 2026-11-05);
            # PL2 = PL1 + 2 W already keeps CPU bursts from taking the GPU's budget
            rpe, top = self._gpu_limits()
            nxt = (l.gpu_min_mhz or rpe or 0) + GPU_STEP_MHZ
            if p.gpu_min_freq and (top is None or nxt <= top):
                was = l.gpu_min_mhz
                out.append(("gpu_floor_up", lambda n=nxt: self._gpu_floor(l, n), lambda w=was: self._gpu_floor(l, w),
                            f"raised GPU floor to {nxt} MHz", f"+{GPU_STEP_MHZ} MHz GPU floor"))
        elif l.bound == "cpu":
            if p.cpu_no_turbo and not l.boost:
                out.append(("boost_on", lambda: self._boost(l, True), lambda: self._boost(l, False), "turned CPU boost on", "CPU boost on"))
            if p.cpu_max_perf_pct and l.cpu_max_pct < 100:
                was = l.cpu_max_pct
                out.append(("cpu_up", lambda: self._cpu(l, 100), lambda w=was: self._cpu(l, w), "raised CPU ceiling to 100 %", "CPU ceiling 100 %"))
            if l.gpu_min_mhz is not None:
                was = l.gpu_min_mhz
                out.append(("gpu_floor_off", lambda: self._gpu_floor(l, None), lambda w=was: self._gpu_floor(l, w),
                            "dropped the GPU floor (budget back to the CPU)", "GPU floor off"))
        return [x for x in out if x[0] not in s.balance_tried]

    def _balance(self, s: Session, fps: float) -> None:
        l = s.learned
        if s.balance_undo:                                # judge the lever on trial
            name, undo, before, words, delta = s.balance_undo
            s.balance_undo = None
            if fps >= before * (1 + BALANCE_GAIN):
                s.balance_tried.discard(name)             # it helped: the same lever may go one more step
                l.fps = fps
                if l.tdp == self._top(s):
                    l.max_fps = max(l.max_fps or 0, fps)  # the set TDP gives more than measured: that is the most now
                self._did(s, f"{words}: {before:.0f} → {fps:.0f} fps, kept", delta)
            else:
                undo()
                self._did(s, f"{words} didn't help ({before:.0f} → {fps:.0f} fps), undone", "undone")
                s.phase = "lower" if l.tdp > s.floor_tdp else "hold"
                return
        levers = self._levers(s)
        if not levers:                                   # nothing (left) to try: this window judges the TDP itself
            s.phase = "lower"
            self._lower(s, fps)
            return
        name, apply, undo, words, delta = levers[0]
        s.balance_tried.add(name)
        if apply():
            s.balance_undo = (name, undo, fps, words, delta)
            self._did(s, f"trying: {words}", delta)
        # a lever that couldn't be written is just skipped; the next window tries the next one

    def _lower(self, s: Session, fps: float) -> None:
        l = s.learned
        line, margin = self._line(s), self._margin(s)
        prev = s.last_good
        # fps lost per watt saved since the last TDP that held (None at the first step)
        cost = None if not prev or prev[0] <= l.tdp else max(0.0, prev[1] - fps) / (prev[0] - l.tdp)
        flat = cost is not None and cost <= FLAT_FPS_PER_W
        if fps >= line or (flat and fps >= line - margin / 2):
            s.misses, l.fps = 0, fps
            if (cost is not None and fps < line + margin and cost > STEEP_FPS_PER_W
                    and prev and prev[0] - l.tdp <= 1):
                s.phase = "hold"                           # past the knee: the next watt costs more than it saves
                self._did(s, f"holding at {l.tdp} W: the last watt cost {cost:.1f} fps", "knee")
                s.last_good = (l.tdp, fps)
                return
            s.last_good = (l.tdp, fps)
            if l.tdp <= s.floor_tdp:
                s.phase = "hold"
                self._try_efficiency(s)                    # on the line at the floor: the GPU's efficiency mode next
                return
            # clearly above the line (the upper half of the band) and the last step was flat: a bigger step
            step = BIG_STEP_W if not s.fine and fps >= line + margin / 2 and (cost is None or cost <= FLAT_FPS_PER_W) else 1
            w, was = max(s.floor_tdp, l.tdp - step), l.tdp
            if self._tdp(l, w):
                s.balance_tried = set()                    # the bottleneck can move as watts drop: try again here
                s.phase = "balance"
                self._did(s, f"lowered TDP to {w} W ({fps:.0f} fps, {100 * fps / l.max_fps:.0f} % of {l.max_fps:.0f})", f"−{was - w} W")
            else:
                s.phase = "hold"
            return
        s.misses += 1
        if s.misses < 2:
            return                                         # one window can be a heavier patch of the map: again
        s.misses = 0
        if prev and prev[0] - l.tdp > 1 and not s.fine:
            # a big step went past the line: back to 1 W under the last TDP that held, and on in 1 W steps
            s.fine = True
            w, was = prev[0] - 1, l.tdp
            if self._tdp(l, w):
                s.phase = "balance"
                s.balance_tried = set()
                self._did(s, f"{fps:.0f} fps at {was} W is below {line:.0f}: trying {w} W", f"+{w - was} W")
            return
        back = prev[0] if prev and prev[0] > l.tdp else l.tdp + 1
        w, was = min(self._top(s), back), l.tdp
        if w != l.tdp and self._tdp(l, w):
            self._did(s, f"{fps:.0f} fps is below {line:.0f}: back up to {w} W, holding", f"+{w - was} W")
        s.phase = "hold"

    def _try_efficiency(self, s: Session) -> None:
        """At the TDP floor and still within keep %: GPU efficiency mode (power_saving) for one window, kept only if
        the game stays on the line. It saves a little more where watts can't go lower."""
        l = s.learned
        if s.eff_tried or self._gpu_active() != "base" or "power_saving" not in self.power.gpu_power_profile()[1]:
            return
        s.eff_tried = True
        if self._gpu_profile(l, "power_saving"):
            s.eff_trial = True
            s.window, s.watts, s.shown = [], [], []
            self._did(s, f"at the {s.floor_tdp} W floor: trying GPU efficiency mode", "GPU efficiency on")

    def _hold(self, s: Session, fps: float) -> None:
        l = s.learned
        if s.eff_trial:                                  # judge GPU efficiency mode at the floor
            s.eff_trial = False
            if fps < self._line(s):
                self._gpu_profile(l, "base")
                self._did(s, f"GPU efficiency mode cost frames ({fps:.0f} fps, under {self._line(s):.0f}), undone", "undone")
            else:
                self._did(s, f"GPU efficiency mode kept ({fps:.0f} fps)", "kept")
            return
        ref = l.fps or l.max_fps or fps
        shifted = abs(fps - ref) / ref >= SHIFT if ref else False
        if shifted:
            s.misses += 1
            if s.misses >= 2:
                self._remeasure(s, f"fps moved from {ref:.0f} to {fps:.0f}")
            return
        if s.measured_at and time.monotonic() - s.measured_at >= RESCAN_S:
            self._remeasure(s, "routine check")
            return
        if l.max_fps and fps < self._line(s):
            s.misses += 1
            if s.misses >= 2 and l.bound == "gpu" and self._gpu_active() == "power_saving":
                s.misses = 0                             # short and GPU-bound: the GPU's full clock ramp before watts
                if self._gpu_profile(l, "base"):
                    self._did(s, f"{fps:.0f} fps is below {self._line(s):.0f}: GPU efficiency mode off", "GPU efficiency off")
                return
            if s.misses >= 2 and l.tdp < self._top(s):
                s.misses = 0
                if self._tdp(l, l.tdp + 1):
                    self._did(s, f"{fps:.0f} fps is below {self._line(s):.0f}: raised TDP to {l.tdp} W", "+1 W")
            return
        s.misses = 0

    def _multiplier(self, s: Session) -> None:
        """Presented / rendered over the window: 1 without frame generation, 2-4 with it. A change (frame generation
        switched on or off in the game) makes the old max meaningless: measure again."""
        if s.source != "rendered" or not s.shown or not s.avg_fps:
            m = None
        else:
            m = round((sum(s.shown) / len(s.shown)) / s.avg_fps, 1)
        old = s.multiplier
        s.multiplier = m
        if s.phase != "measure" and old is not None and m is not None and abs(m - old) >= 0.5:
            self._remeasure(s, f"frame generation changed: {old:g}x → {m:g}x")

    # ---- the levers --------------------------------------------------------------------------------------------------
    def _tdp(self, l: Learned, watts: int) -> bool:
        """Set the TDP and record it only if it was really applied (a refused write is not a change to learn from).
        Never above the temperature guard's step."""
        s = self.session
        if s is not None and s.thermal_cap is not None and watts > s.thermal_cap:
            watts = s.thermal_cap
            if watts == l.tdp:
                return False
        if not self.power.set_tdp(watts, self.plugged):
            log.warning("autotune: TDP %d W was not applied", watts)
            return False
        l.tdp = watts
        return True

    def _gpu_limits(self) -> tuple[int | None, int | None]:
        """(efficient clock, current ceiling) of the GPU: where a floor starts, and where it must stop."""
        p = self.hw.paths
        rpe = sysfs.read_int(p.gpu_rpe_freq) if p.gpu_rpe_freq else None
        top = (sysfs.read_int(p.gpu_max_freq) if p.gpu_max_freq else None) or \
            (sysfs.read_int(p.gpu_rp0_freq) if p.gpu_rp0_freq else None)
        return rpe, top

    def _gpu_floor(self, l: Learned, mhz: int | None) -> bool:
        if not self.hw.paths.gpu_min_freq or mhz == l.gpu_min_mhz:
            return False
        ok = self.power.reset_gpu_floor() if mhz is None else self.power.set_gpu_window(mhz, None)
        if ok:
            l.gpu_min_mhz = mhz
        return ok

    def _gpu_active(self) -> str | None:
        return self.power.gpu_power_profile()[0] if self.hw.paths.gpu_power_profile else None

    def _gpu_profile(self, l: Learned, value: str, force: bool = False) -> bool:
        if not self.hw.paths.gpu_power_profile or (not force and self._gpu_active() == value):
            return False
        try:
            ok = self.power.set_gpu_power_profile(value)
        except ValueError:
            return False
        if ok:
            l.gpu_profile = value
        return ok

    def _cpu(self, l: Learned, pct: int) -> bool:
        pct = max(CPU_MIN_PCT, min(100, pct))
        if pct == l.cpu_max_pct or not self.hw.paths.cpu_max_perf_pct:
            return False
        if self.power.set_cpu_max_pct(pct):
            l.cpu_max_pct = pct
            return True
        return False

    def _boost(self, l: Learned, on: bool) -> bool:
        if not self.hw.paths.cpu_no_turbo or l.boost == on:
            return False
        if self.power.set_cpu_turbo(on):
            l.boost = on
            return True
        return False

    def _log(self, s: Session, smp: Sample) -> None:
        try:
            new = not os.path.exists(self.csv_log)
            with open(self.csv_log, "a", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["time", "appid", "phase", "fps", "shown_fps", "source", "gpu_mhz", "gpu_max_mhz", "cpu_pct",
                                "pkg_w", "tdp", "cpu_max_pct", "gpu_min_mhz", "boost", "bound", "max_fps"])
                l = s.learned
                w.writerow([f"{smp.t:.1f}", s.appid, s.phase, None if smp.fps is None else round(smp.fps, 1),
                            None if smp.shown_fps is None else round(smp.shown_fps, 1), smp.source, smp.gpu_mhz,
                            smp.gpu_max_mhz, None if smp.cpu_pct is None else round(smp.cpu_pct, 1),
                            None if smp.pkg_w is None else round(smp.pkg_w, 2), l.tdp, l.cpu_max_pct, l.gpu_min_mhz,
                            int(l.boost), l.bound, None if l.max_fps is None else round(l.max_fps, 1)])
        except OSError:
            pass

    def status(self) -> dict:
        s = self.session
        if not s:
            return {"running": False, "enabled": self.enabled}
        l = s.learned
        last = s.history[-1] if s.history else None
        r = lambda v: None if v is None else round(v, 1)
        return {"running": True, "enabled": self.enabled, "appid": s.appid, "paused": s.paused, "phase": s.phase,
                "learned": l.__dict__, "bound": l.bound, "tdp": l.tdp, "ceiling_tdp": s.ceiling_tdp,
                "floor_tdp": s.floor_tdp, "max_tdp": s.max_tdp, "plugged": self.plugged, "keep_pct": s.keep_pct, "keep_fps": KEEP_FPS,
                "saves_w": (s.ceiling_tdp - l.tdp) if (s.phase in ("hold", "target") and l.tdp is not None and s.ceiling_tdp > l.tdp) else None, "max_fps": r(l.max_fps), "avg_fps": r(s.avg_fps),
                "low_fps": r(s.low_fps), "pct_of_max": None if not (l.max_fps and s.avg_fps) else round(100 * s.avg_fps / l.max_fps),
                "fps_per_w": None if s.fps_per_w is None else round(s.fps_per_w, 2), "source": s.source,
                "multiplier": s.multiplier, "window_s": s.settle, "thermal_cap": s.thermal_cap, "no_frames": s.no_frames,
                "learning": s.short_tau or s.phase != "hold" or s.quiet < SETTLED_WINDOWS,
                "pl1_window_s": None if self.power.pl1_window_us() is None else round(self.power.pl1_window_us() / 1e6, 1),
                "last_action": s.last_action, "last_delta": s.last_delta,
                "target": None if s.target is None else round(s.target),
                "unreachable": s.unreachable, "best_fps": r(s.best_fps),
                "floor_learned": s.g_floor if s.phase == "target" and s.g_floor > s.floor_tdp else None, "fg": s.fg,
                "last_change_s": None if s.last_change_at is None else round(time.monotonic() - s.last_change_at),
                "last": None if last is None else last.__dict__}
