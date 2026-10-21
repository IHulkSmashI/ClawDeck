"""Autotune: learn, per game, the lowest power that holds a target frame rate, and whether the game is CPU- or
GPU-bound, then apply that on the next launch.

Loop (1 Hz while a game runs, idle otherwise; one asyncio task, no threads, no busy polling):
  1. sample: frame time (MangoHud log, see Sampler), GPU clock vs its ceiling, package power (RAPL energy delta),
     CPU utilisation (/proc/stat delta).
  2. classify: GPU-bound when the GPU is busy >= 90 % (PowerStation) or sits at its clock ceiling, while the CPU is < 60 %;
     CPU-bound when CPU > 85 % on the busiest cores and the GPU is < 70 %; else balanced.
  3. judge over time, never one second: every decision looks at the last `settle` seconds (10 by default): the
     average fps and the worst second in that window. After every change the window starts empty, so the next
     decision is based only on what the new setting did. "On target" = average >= 97 % of the target and no second
     below 85 %; "missing" = average below 95 % or a second below 75 % (stutter); in between, hold.
  4. control, one lever per decision. CPU and GPU share one power budget (the TDP), so balancing them is half the
     job: power the side that isn't holding the game back doesn't need goes to the side that is.
     - on target: alternately trim the side that isn't
       the bottleneck (GPU-bound: CPU ceiling -10 %, down to 60 %; CPU-bound: GPU floor -100 MHz, or off) and lower
       the TDP by 1 W, so the budget shrinks without starving the bound side;
     - missing: first move budget to the bound side without more watts (GPU-bound: CPU ceiling
       -10 % if the CPU is idle enough, else GPU floor +100 MHz; CPU-bound: CPU ceiling +10 %, else drop the GPU
       floor), then TDP +2 W; never above the ceiling (the profile or power limit the user picked).
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
CPU_IDLE_PCT = 50       # "the CPU has power to spare": its utilisation is under this while the GPU is the bottleneck
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
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._log_pos.get(f, 0))
                rows = fh.read()
                self._log_pos[f] = fh.tell()
        except OSError:
            return None, None
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
    trim_turn: bool = True   # on target: alternate between rebalancing CPU/GPU and lowering the TDP
    window: list[float] = field(default_factory=list)   # per-second fps since the last change
    last_change_t: float | None = None
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

    def set_ceiling(self, watts: int) -> None:
        """The user picked a profile or a power limit while autotune runs: that is its new ceiling. Learning starts
        again from there (the limit is applied by the caller) instead of pausing: autotune only ever goes lower."""
        s = self.session
        if not s:
            return
        s.ceiling_tdp = max(s.floor_tdp, int(watts))
        s.learned.tdp = s.ceiling_tdp
        s.paused, s.good_run, s.bad_run, s.unchanged = False, 0, 0, 0
        s.window.clear()
        log.info("autotune ceiling now %d W", s.ceiling_tdp)

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
        changed = False
        if s.avg_fps < t * MISS_AVG or s.low_fps < t * MISS_LOW:
            changed = self._feed_bound(l, smp)
            if not changed and l.tdp < s.ceiling_tdp:
                l.tdp = min(s.ceiling_tdp, l.tdp + 2)
                self.power.set_tdp(l.tdp, self.plugged)
                changed = True
            l.confidence = max(0, l.confidence - 1)
        elif s.avg_fps >= t * ON_AVG and s.low_fps >= t * ON_LOW:
            if l.confidence >= 3 and s.avg_fps >= t * HEADROOM:
                # far above target on a learned game: the player lowered the graphics (or a scene got lighter for
                # good), so there is power to save now, not in RECHECK_S
                log.info("autotune: %.0f fps for a %d target, learning again", s.avg_fps, t)
                l.confidence = 0
            recheck = l.confidence >= 3 and smp.t - s.last_change_t >= RECHECK_S
            if l.confidence < 3 or recheck:
                if s.trim_turn:
                    changed = self._trim_unbound(l, smp)
                if not changed and l.tdp > s.floor_tdp:
                    l.tdp -= 1
                    self.power.set_tdp(l.tdp, self.plugged)
                    changed = True
                s.trim_turn = not s.trim_turn
                if recheck:
                    log.info("autotune recheck: trying one step lower after %d s on target", RECHECK_S)
        if changed:
            s.unchanged = 0
            s.window.clear()                         # judge the new setting on its own numbers only
            s.last_change_t = smp.t
        else:
            s.unchanged += 1
            if s.unchanged >= s.stable:
                l.confidence = min(5, l.confidence + 1)
                s.unchanged = 0

    # ---- balancing CPU and GPU inside one power budget ---------------------------------------------------------------
    def _set_cpu(self, l: Learned, pct: int) -> bool:
        pct = max(CPU_MIN_PCT, min(100, pct))
        if pct == l.cpu_max_pct or not self.hw.paths.cpu_max_perf_pct:
            return False
        l.cpu_max_pct = pct
        self.power.set_cpu_max_pct(pct)
        return True

    def _set_gpu_floor(self, l: Learned, mhz: int | None) -> bool:
        if not self.hw.paths.gpu_min_freq or mhz == l.gpu_min_mhz:
            return False
        if mhz is None:
            l.gpu_min_mhz = None
            self.power.reset_gpu_window()
            return True
        hw_max = sysfs.read_int(self.hw.paths.gpu_rp0_freq) if self.hw.paths.gpu_rp0_freq else None
        if hw_max and mhz > hw_max:
            return False
        l.gpu_min_mhz = mhz
        self.power.set_gpu_window(mhz, None)
        return True

    def _trim_unbound(self, l: Learned, smp: Sample) -> bool:
        """On target: take power from the side that isn't the bottleneck, so the TDP can drop further."""
        if l.bound == "gpu" and (smp.cpu_pct is None or smp.cpu_pct < CPU_IDLE_PCT):
            return self._set_cpu(l, l.cpu_max_pct - 10)
        if l.bound == "cpu" and l.gpu_min_mhz:
            floor = l.gpu_min_mhz - GPU_STEP_MHZ
            rpe = sysfs.read_int(self.hw.paths.gpu_rpe_freq) if self.hw.paths.gpu_rpe_freq else None
            return self._set_gpu_floor(l, None if rpe is None or floor <= rpe else floor)
        return False

    def _feed_bound(self, l: Learned, smp: Sample) -> bool:
        """Below target: move budget to the bottleneck before asking for more watts."""
        if l.bound == "gpu":
            if smp.cpu_pct is not None and smp.cpu_pct < CPU_IDLE_PCT and self._set_cpu(l, l.cpu_max_pct - 10):
                return True
            rpe = sysfs.read_int(self.hw.paths.gpu_rpe_freq) if self.hw.paths.gpu_rpe_freq else None
            return self._set_gpu_floor(l, (l.gpu_min_mhz or rpe or 0) + GPU_STEP_MHZ)
        if l.bound == "cpu":
            return self._set_cpu(l, l.cpu_max_pct + 10) or (l.gpu_min_mhz is not None and self._set_gpu_floor(l, None))
        return False

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
                "last": None if last is None else last.__dict__}
