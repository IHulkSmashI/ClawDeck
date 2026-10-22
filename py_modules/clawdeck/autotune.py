"""Autotune: learn, per game, the lowest power that holds a target frame rate, and what holds the game back (CPU or
GPU), then apply that on the next launch.

Loop (1 Hz while a game runs, idle otherwise; one asyncio task, no threads, no busy polling):
  1. sample: frame time (MangoHud log, see Sampler), GPU clock vs its ceiling, package power (RAPL energy delta),
     CPU utilisation (/proc/stat delta).
  2. classify: GPU-bound when the GPU is busy >= 90 % (PowerStation) or sits at its clock ceiling, while the CPU is < 60 %;
     CPU-bound when CPU > 85 % on the busiest cores and the GPU is < 70 %; else balanced.
  3. judge over time, never one second: every decision looks at the last `settle` seconds (10 by default): the
     average fps and the worst second in that window. After every change the window starts empty, so the next
     decision is based only on what the new setting did. "On target" = average >= 97 % of the target and no second
     below 85 %; "missing" = average below 95 % or a second below 75 % (stutter); in between, hold.
  4. control, one lever per decision window (HARDWARE-FINDINGS §18): act on what is short, then on watts.
     - missing and GPU-bound: raise the GPU floor (+100 MHz from its efficient clock) until it reaches the GPU
       ceiling, then raise the TDP (+2 W);
     - missing and CPU-bound: CPU boost on, then the CPU ceiling back to 100 %, then drop a GPU floor autotune set
       (it eats the shared budget), then raise the TDP;
     - missing, neither: raise the TDP;
     - on target (battery first): lower the TDP by 1 W; only at the floor lower the GPU floor (-100 MHz, off below
       its efficient clock) and then the CPU ceiling (-10 %, not below 60 %).
     The TDP stays between the floor (the model minimum, or the user's) and the ceiling (the profile or TDP slider
     the user picked); at the ceiling and still short, the status line says so instead of changing anything.
  5. after `stable` decisions without a change, confidence goes up; at 3 the game is "learned" and autotune stops
     lowering, but keeps checking for as long as the game runs, because the player can change graphics settings or
     reach a heavier area at any time: a miss raises power again at once (settings raised), fps far above the target
     (HEADROOM) starts learning again (settings lowered), and every RECHECK_S of steady play on target it tries one
     step lower again, in case the game or a driver update now needs less.

Cost, per second while a game runs: one read of the new lines of the MangoHud log, /proc/stat, two GPU clock files
and the RAPL energy counter, plus a few comparisons. The two dearer reads are cached: the log directory listing
(every LOG_RESCAN_S) and PowerStation's GPU load over D-Bus (every BUSY_EVERY_S). No game running = no loop at all.

Rules: dry-run prints every would-be write; a manual change by the user (TDP differs from what we set) pauses
learning for the session; everything applied is reverted on game exit. The maths is a few comparisons per second:
far below the 0.5 % CPU budget even in Python.
"""
from __future__ import annotations

import asyncio
import csv
import glob
import logging
import os
import time
from dataclasses import dataclass, field

from . import hw, power, shadowblip, sysfs

log = logging.getLogger("clawdeck.autotune")

CPU_MIN_PCT = 60        # autotune never trims the CPU below this: below it, frame pacing suffers in most games
GPU_STEP_MHZ = 100
LOG_RESCAN_S = 30       # look for a new MangoHud log file this often (a new file appears only when a game starts)
BUSY_EVERY_S = 10       # PowerStation's GPU load is read at most this often (one D-Bus call per decision window)
HEADROOM = 1.15         # a learned game running this far above its target (graphics lowered?) is learned again
RECHECK_S = 300         # a learned game is re-probed one step lower after this long on target without a change
ON_AVG, ON_LOW = 0.97, 0.85     # on target: window average and worst second, as a share of the target
MISS_AVG, MISS_LOW = 0.95, 0.75  # missing: average or worst second below these

@dataclass
class Sample:
    t: float
    frametime_ms: float | None
    fps: float | None
    gpu_mhz: int | None
    gpu_max_mhz: int | None
    cpu_pct: float | None
    pkg_w: float | None
    gpu_busy: int | None = None     # PowerStation's GpuBusyPercent; None when PowerStation doesn't run


class Sampler:
    """Reads the numbers. Frame time comes from MangoHud's CSV log: the game is launched with
    MANGOHUD_CONFIG=output_folder=<dir>,log_interval=1000,autostart_log=1 (ClawDeck sets this through its launch
    wrapper, tools/clawdeck-launch.sh). Without a log, frame time is None and autotune only reports."""

    def __init__(self, hardware: hw.Hardware, log_dir: str):
        self.p = hardware.paths
        self.log_dir = log_dir
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
        ft, fps = self.frametime()
        return Sample(t=time.monotonic(), frametime_ms=ft, fps=fps,
                      gpu_mhz=sysfs.read_int(self.p.gpu_cur_freq) if self.p.gpu_cur_freq else None,
                      gpu_max_mhz=sysfs.read_int(self.p.gpu_max_freq) if self.p.gpu_max_freq else None,
                      cpu_pct=self.cpu_pct(), pkg_w=self.package_power(), gpu_busy=self.gpu_busy())


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
    tdp: int
    cpu_max_pct: int
    gpu_min_mhz: int | None
    boost: bool = True              # CPU boost (intel_pstate no_turbo = 0)
    bound: str = "balanced"
    confidence: int = 0
    samples: int = 0


@dataclass
class Session:
    appid: str
    target_fps: int
    floor_tdp: int
    ceiling_tdp: int
    learned: Learned
    settle: int = 10         # seconds of fps averaged before each decision (the window)
    stable: int = 60         # decisions without a change before confidence goes up
    good_run: int = 0
    bad_run: int = 0
    unchanged: int = 0
    paused: bool = False
    window: list[float] = field(default_factory=list)   # per-second fps since the last change
    last_change_t: float | None = None
    last_change_at: float | None = None  # monotonic time of the last change, for "12 s ago"
    last_action: str | None = None       # the last adjustment, in words: "raised GPU floor to 1300 MHz"
    last_delta: str | None = None        # the same, short: "+100 MHz GPU floor", "−1 W"
    limited: str | None = None           # short of the target with nothing left to raise: why
    avg_fps: float | None = None
    low_fps: float | None = None
    history: list[Sample] = field(default_factory=list)


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
    def start(self, appid: str, target_fps: int, learned: dict | None, floor_tdp: int, ceiling_tdp: int,
              plugged: bool | None = None) -> None:
        self.plugged = plugged
        known = {k: v for k, v in (learned or {}).items() if k in Learned.__dataclass_fields__}
        l = Learned(**known) if known.get("tdp") is not None else Learned(tdp=ceiling_tdp, cpu_max_pct=100, gpu_min_mhz=None)
        l.tdp = max(floor_tdp, min(int(l.tdp), ceiling_tdp))
        self.session = Session(appid=appid, target_fps=target_fps, floor_tdp=floor_tdp, ceiling_tdp=ceiling_tdp, learned=l)
        self._apply(l)
        if self.task is None or self.task.done():
            self.task = asyncio.get_event_loop().create_task(self._loop())

    def set_ceiling(self, watts: int) -> int | None:
        """The user picked a profile or moved the TDP slider while autotune runs: that is its new ceiling
        (HARDWARE-FINDINGS §17). The TDP autotune uses is re-clamped under it and written at once (the caller has
        just written the ceiling itself); a raised ceiling leaves the current TDP, and autotune raises it when the
        game needs it. Picking a power level hands control back after a manual change. Returns the watts applied."""
        s = self.session
        if not s:
            return None
        s.ceiling_tdp = max(s.floor_tdp, int(watts))
        l = s.learned
        want = max(s.floor_tdp, min(l.tdp, s.ceiling_tdp))
        if not self.power.set_tdp(want, self.plugged):
            log.warning("autotune: re-clamp to %d W was not applied", want)
        l.tdp = want
        s.paused, s.good_run, s.bad_run, s.unchanged, s.limited = False, 0, 0, 0, None
        s.window.clear()
        log.info("autotune range now %d-%d W, using %d W", s.floor_tdp, s.ceiling_tdp, want)
        return want

    def set_floor(self, watts: int) -> None:
        """The lowest TDP autotune may use (the model minimum unless the user sets one)."""
        s = self.session
        if not s:
            return
        lo = self.hw.model["tdp"]["min"]
        s.floor_tdp = max(lo, min(int(watts), s.ceiling_tdp))
        if s.learned.tdp < s.floor_tdp:
            self._tdp(s.learned, s.floor_tdp)

    def pause(self, reason: str = "manual change") -> None:
        """Never fight the user: a manual power change stops learning for the rest of this session (values the user
        set stay; what was learned so far is still saved at game exit)."""
        if self.session and not self.session.paused:
            self.session.paused = True
            log.info("autotune paused: %s", reason)

    def running(self) -> bool:
        return self.session is not None

    def stop(self) -> dict | None:
        s = self.session
        self.session = None
        if s:
            self.power.revert_all()
            return {"learned": s.learned.__dict__, "samples": len(s.history)}
        return None

    def _apply(self, l: Learned) -> None:
        self.power.set_tdp(l.tdp, self.plugged)
        self.power.set_cpu_max_pct(l.cpu_max_pct)
        if self.hw.paths.cpu_no_turbo and self.power.turbo() != l.boost:
            self.power.set_cpu_turbo(l.boost)
        if l.gpu_min_mhz:
            self.power.set_gpu_window(l.gpu_min_mhz, None)

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
        if smp.frametime_ms is None or s.paused:
            return
        l = s.learned
        l.samples += 1
        fps = smp.fps if smp.fps else 1000.0 / smp.frametime_ms if smp.frametime_ms else None
        if not fps:
            return
        s.window.append(fps)
        del s.window[:-s.settle]
        if s.last_change_t is None:
            s.last_change_t = smp.t
        if len(s.window) < s.settle:
            return                                   # not enough seconds since the last change to judge it
        s.avg_fps = sum(s.window) / len(s.window)
        s.low_fps = min(s.window)
        l.bound = classify(smp)
        t = s.target_fps
        changed = None                               # what was done, in words, or None
        if s.avg_fps < t * MISS_AVG or s.low_fps < t * MISS_LOW:
            changed = self._raise(s, l)
            s.limited = None if changed else f"at the {s.ceiling_tdp} W limit and still short: pick a higher profile or lower the target"
            l.confidence = max(0, l.confidence - 1)
        elif s.avg_fps >= t * ON_AVG and s.low_fps >= t * ON_LOW:
            s.limited = None
            if l.confidence >= 3 and s.avg_fps >= t * HEADROOM:
                # far above target on a learned game: the player lowered the graphics (or a scene got lighter for
                # good), so there is power to save now, not in RECHECK_S
                log.info("autotune: %.0f fps for a %d target, learning again", s.avg_fps, t)
                l.confidence = 0
            recheck = l.confidence >= 3 and smp.t - s.last_change_t >= RECHECK_S
            if l.confidence < 3 or recheck:
                changed = self._lower(s, l)
                if recheck:
                    log.info("autotune recheck: trying one step lower after %d s on target", RECHECK_S)
        if changed:
            s.unchanged = 0
            s.window.clear()                         # judge the new setting on its own numbers only
            s.last_change_t = smp.t
            s.last_change_at = time.monotonic()
            s.last_action, s.last_delta = changed
            log.info("autotune: %s-bound, %.0f/%d fps: %s", l.bound, s.avg_fps, t, s.last_action)
        else:
            s.unchanged += 1
            if s.unchanged >= s.stable:
                l.confidence = min(5, l.confidence + 1)
                s.unchanged = 0

    def _tdp(self, l: Learned, watts: int) -> bool:
        """Set the TDP and record it only if it was really applied (a refused write is not a change to learn from)."""
        if not self.power.set_tdp(watts, self.plugged):
            log.warning("autotune: TDP %d W was not applied", watts)
            return False
        l.tdp = watts
        return True

    # ---- the levers (HARDWARE-FINDINGS §18): one per decision, each returns (what was done, short form) or None ----
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

    def _cpu_ceiling(self, l: Learned, pct: int) -> bool:
        pct = max(CPU_MIN_PCT, min(100, pct))
        if pct == l.cpu_max_pct or not self.hw.paths.cpu_max_perf_pct:
            return False
        if self.power.set_cpu_max_pct(pct):
            l.cpu_max_pct = pct
            return True
        return False

    def _raise(self, s: Session, l: Learned) -> tuple[str, str] | None:
        """Short of the target: give the bound side what it lacks before asking for more watts."""
        if l.bound == "gpu":
            rpe, top = self._gpu_limits()
            nxt = (l.gpu_min_mhz or rpe or 0) + GPU_STEP_MHZ
            if top:
                nxt = min(nxt, top)
            if self.hw.paths.gpu_min_freq and (l.gpu_min_mhz or 0) < (top or nxt) and self._gpu_floor(l, nxt):
                return f"raised GPU floor to {nxt} MHz", f"+{GPU_STEP_MHZ} MHz GPU floor"
        elif l.bound == "cpu":
            if not l.boost and self.hw.paths.cpu_no_turbo and self.power.set_cpu_turbo(True):
                l.boost = True
                return "turned CPU boost on", "CPU boost on"
            if l.cpu_max_pct < 100 and self._cpu_ceiling(l, 100):
                return "raised CPU ceiling to 100 %", "CPU ceiling 100 %"
            if l.gpu_min_mhz is not None and self._gpu_floor(l, None):
                return "dropped the GPU floor (budget back to the CPU)", "GPU floor off"
        if l.tdp < s.ceiling_tdp:
            was, w = l.tdp, min(s.ceiling_tdp, l.tdp + 2)
            if self._tdp(l, w):
                return f"raised TDP to {w} W", f"+{w - was} W"
        return None

    def _lower(self, s: Session, l: Learned) -> tuple[str, str] | None:
        """On target: save battery. Watts first; only at the floor give back GPU floor, then CPU ceiling."""
        if l.tdp > s.floor_tdp:
            w = l.tdp - 1
            return (f"lowered TDP to {w} W", "−1 W") if self._tdp(l, w) else None
        if l.gpu_min_mhz:
            rpe, _ = self._gpu_limits()
            nxt = l.gpu_min_mhz - GPU_STEP_MHZ
            nxt = None if rpe is None or nxt <= rpe else nxt
            if self._gpu_floor(l, nxt):
                return (f"lowered GPU floor to {nxt} MHz", f"−{GPU_STEP_MHZ} MHz GPU floor") if nxt else ("dropped the GPU floor", "GPU floor off")
        if l.cpu_max_pct > CPU_MIN_PCT and self._cpu_ceiling(l, l.cpu_max_pct - 10):
            return f"lowered CPU ceiling to {l.cpu_max_pct} %", "−10 % CPU"
        return None

    def _log(self, s: Session, smp: Sample) -> None:
        try:
            new = not os.path.exists(self.csv_log)
            with open(self.csv_log, "a", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["time", "appid", "fps", "frametime_ms", "gpu_mhz", "gpu_max_mhz", "cpu_pct", "pkg_w",
                                "tdp", "cpu_max_pct", "gpu_min_mhz", "bound", "confidence"])
                w.writerow([f"{smp.t:.1f}", s.appid, smp.fps, smp.frametime_ms, smp.gpu_mhz, smp.gpu_max_mhz,
                            None if smp.cpu_pct is None else round(smp.cpu_pct, 1),
                            None if smp.pkg_w is None else round(smp.pkg_w, 2),
                            s.learned.tdp, s.learned.cpu_max_pct, s.learned.gpu_min_mhz, s.learned.bound, s.learned.confidence])
        except OSError:
            pass

    def status(self) -> dict:
        s = self.session
        if not s:
            return {"running": False, "enabled": self.enabled}
        last = s.history[-1] if s.history else None
        return {"running": True, "enabled": self.enabled, "appid": s.appid, "target_fps": s.target_fps,
                "learned": s.learned.__dict__, "paused": s.paused,
                "avg_fps": None if s.avg_fps is None else round(s.avg_fps, 1),
                "low_fps": None if s.low_fps is None else round(s.low_fps, 1), "window_s": s.settle,
                "last_action": s.last_action, "last_delta": s.last_delta, "limited": s.limited,
                "last_change_s": None if s.last_change_at is None else round(time.monotonic() - s.last_change_at),
                "ceiling_tdp": s.ceiling_tdp, "floor_tdp": s.floor_tdp, "bound": s.learned.bound,
                "last": None if last is None else last.__dict__}
