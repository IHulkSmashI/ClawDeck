"""Autotune: for the running game, the fewest watts that still give the frames it has to deliver.

That is your target fps (Power -> Autotune target), or what the full limit gives when the target is out of
reach. The limit you picked (profile or slider) is the most it uses.

The deciding is in governor.py: one question asked all the time (would more watts give frames that are owed?), a step
down that is watched on every frame and taken back at the first loss, the full limit at once when frames are lost. It
has no clock and no I/O, so the tests run it on frames written by hand. This module is
the rest: the frames (Sampler), the session of a game, the loop, the writes, and what the panel and the log show.

Frames. Every frame of every game comes from mangoapp's log (below). It counts presented frames; with XeSS frame
generation the game presents 2-4 frames for every one it renders. XeLL's xellSleep runs once per rendered frame; the
xess-xmx-linux shim logs it ("xellSleep stats: 100 calls in W ms (P ms/frame)", IGDEXT_XELL_LOG=1, set by the launch
wrapper while autotune is on), so 1000 / P is the rendered rate and the presented rate divided by it the multiplier
the governor gets. Without XeLL, ClawDeck's own frame generation multiplier for the game.

The loop looks twice a second, and ten times a second for the 1-2.5 s after a write of its own (governor.LOOK_S,
WATCH_S); one asyncio task, nothing at all when no game runs. While a game runs it is on the E-cores at nice 10
(quiet.py). Per look: the new lines of two small logs, /proc/stat, two GPU clock files and the RAPL energy counter;
PowerStation's GPU load at most every BUSY_EVERY_S.

What Autotune owns while it governs: PL1; PL2 at PL1 + 2 W, also when you set a PL2 by hand (that one belongs to your
limit and comes back with it: with PL2 far over PL1 the chip runs over a lowered limit for seconds, and a step down
would be judged on frames it didn't cost yet); the PL1 time window at 8 s from the game's start to its end. It does
not touch the CPU and GPU levers: they stay as your pick set them.

Rules: a game it has no history of starts mid-range, one it knows where it held last time under the same limit and
target; a limit picked while the game runs goes on the chip as picked. A manual change (Advanced) pauses it for the
session; xemu and a game without any frame rate get your limit and are left alone; everything is reverted on game
exit, and main.py puts the user's limit back. The temperature guard's step is a cap it works under (thermal.py).
"""
from __future__ import annotations

import asyncio
import csv
import glob
import logging
import math
import os
import re
import socket
import statistics
import time
from collections import deque
from dataclasses import dataclass, field

from . import governor, hw, power, shadowblip, sysfs

log = logging.getLogger("clawdeck.autotune")

LOG_RESCAN_S = 30       # look for a new MangoHud log file this often (a new file appears only when a game starts)
BUSY_EVERY_S = 10       # PowerStation's GPU load is read at most this often (one D-Bus call)
XELL_FRESH_S = 15       # a rendered-frame figure older than this no longer counts
# Frames: every game has Steam's overlay, mangoapp, which gamescope feeds every frame of the focused app (also a game
# without ClawDeck's launch wrapper on its shortcut: a Flatpak emulator, a game started some other way). mangoapp writes one
# row per frame to ~/mangoapp_<date>_<time>.csv while logging is on, turned on and off through its control socket
# (abstract unix socket "mangohud"); its frame times match the game's own MangoHud log within 0.12 fps (Claw 8 EX,
# 2026-11-14). A log someone else keeps on is only read; one Autotune turned on, it turns off at the game's end.
# Steam rewrites mangoapp's config when the overlay level changes, which stops the log: no new row for
# MANGOAPP_RELOG_S, ask again. A second gamescope on the machine (a nested one) sends its frames to the same mangoapp.
MANGOAPP_SOCKET = "\0mangohud"
MANGOAPP_RELOG_S = 20
MANGOAPP_FRESH_S = 30   # a mangoapp log written this recently is a live one
# One row per frame also shows what an average hides. Midnight Club: Los Angeles on RPCS3 at a 15 W limit showed 29.7 of
# its 30 fps with the median frame on pace, and 6.4 % of the frames more than 10 % slow; at 20 W, 1 % (Claw 8 EX,
# 2026-11-14): a limit that is just too low is a few slow frames, not a lower frame rate. The log gets them.
SLOW = 1.10             # a frame this much longer than the pace is a slow one
PACE_FRAMES = 60        # the pace: the median frame time of this many frames before
CSV_FIELDS = ["time", "appid", "phase", "fps", "shown_fps", "source", "gpu_mhz", "gpu_max_mhz", "cpu_pct", "pkg_w", "tdp",
              "cpu_max_pct", "gpu_min_mhz", "boost", "bound", "max_fps", "frames", "ft_median", "ft_p95", "slow_pct",
              "goal", "mult", "look_s", "note"]
TRACE_NOTES = 20        # what the governor decided since the last log row: no more than this many are kept
GOVERN_TAU_US = 8_000_000  # PL1 time window while Autotune governs: a step down has to bind within its watch
MIN_TARGET = 20
REFUSED_S = 30          # a TDP write that was not applied: nothing more is tried for this long
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
    frames: int | None = None       # presented frames behind this sample, when the log has a row for each (mangoapp's)
    ft_median: float | None = None  # ... their median frame time, ms
    ft_p95: float | None = None     # ... the 95th percentile
    slow_pct: float | None = None   # ... and the share of them more than SLOW over the pace before (PACE_FRAMES)


class Sampler:
    """Reads the numbers. Frame time comes from MangoHud's CSV log: the game is launched with
    MANGOHUD_CONFIG=output_folder=<dir>,log_interval=1000,autostart_log=1 (ClawDeck sets this through its launch
    wrapper, tools/clawdeck-launch.sh). Without a log, frame time is None and autotune only reports."""

    def __init__(self, hardware: hw.Hardware, log_dir: str, home: str | None = None):
        self.p = hardware.paths
        self.log_dir = log_dir
        self.home = home                           # where mangoapp writes its log (the desktop user's home)
        self._mango: str | None = None             # the mangoapp log being read
        self._mango_pos = 0
        self._mango_own = False                    # Autotune turned mangoapp's log on (and turns it off)
        self._mango_done: list[str] = []           # its logs from earlier games: deleted, with their summary twins
        self._mango_asked = -1e9                   # when Autotune last asked mangoapp to log
        self._mango_row_at = 0.0                   # when the last new row came
        self._mango_checked = -1e9
        self.frames_ms: list[float] = []           # the frame times behind the last frametime(), when mangoapp gave them
        self._mango_frames_at = -1e9               # when mangoapp last gave frames (its log is the source while it does)
        self._own_from: str | None = None          # the game's own log, read since mangoapp stopped (from its end then)
        self._pace: deque = deque(maxlen=PACE_FRAMES)   # the frame times before those
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
        self._since = 0.0                          # wall clock when the running game started (set_prefix)
        self._busy: tuple[float, int | None] = (-1e9, None)
        self._busy_retry = 0.0

    def _newest_log(self) -> str | None:
        """The running game's MangoHud log: the newest one written since the game started. An older one is another
        game's: read whole after a plugin restart, it handed Enshrouded's fps to Order of the Sinking Star (2026-11-12).
        Looked for every second until it appears (a game's first frame can come a minute after launch), then every
        LOG_RESCAN_S."""
        now = time.monotonic()
        if self._log and now - self._log_checked < LOG_RESCAN_S and os.path.exists(self._log):
            return self._log
        self._log_checked = now

        def mtime(f: str) -> float:
            try:
                return os.path.getmtime(f)
            except OSError:
                return 0.0
        files = [f for f in glob.glob(os.path.join(self.log_dir, "*.csv")) if mtime(f) >= self._since]
        self._log = max(files, key=mtime) if files else None
        return self._log

    def release(self) -> None:
        """The game (the Steam app) ended: a mangoapp log Autotune turned on goes off, and its file goes: one row per
        frame is about 10 MB an hour, in the user's home, for every game without its own log. A log another tool turned
        on is left alone. mangoapp writes the _summary.csv twin when logging stops, so the twins of earlier ones go
        here too."""
        if self._mango_own:
            mangoapp_command("logging=0")
            if self._mango:
                self._mango_done.append(self._mango)
        keep = []
        for f in self._mango_done:
            twin = f[:-4] + "_summary.csv"
            for g in (f, twin):
                try:
                    os.remove(g)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    log.debug("autotune: mangoapp log %s stays: %s", g, e)
            if not os.path.exists(twin) and f == self._mango:   # the twin may come after this: look again next time
                keep.append(f)
        self._mango_done = keep[-5:]
        self._mango, self._mango_pos, self._mango_own, self._mango_asked = None, 0, False, -1e9

    def frametime(self) -> tuple[float | None, float | None]:
        self.frames_ms = []
        now = time.monotonic()
        ft, fps = self._mangoapp_frametime()        # every frame of the focused app, for every game
        if ft is not None:
            self._mango_frames_at, self._own_from = now, None
            return ft, fps
        if now - self._mango_frames_at < MANGOAPP_RELOG_S:
            return None, None                       # between two rows of mangoapp's log: no frames in this look
        f = self._newest_log()                      # no mangoapp for a while: the game's own log, a row a second
        if not f:
            return None, None
        try:
            if self._own_from != f and self._mango_frames_at > -1e9:
                # mangoapp gave this game's frames until now: only what this log writes from here on. (Its rows since
                # the game began came back as one average otherwise: minutes of them, as "this look's frames")
                self._own_from, self._log_pos[f] = f, os.path.getsize(f)
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

    def _mangoapp_log(self) -> str | None:
        """The mangoapp log being written now: the newest ~/mangoapp_*.csv written in the last MANGOAPP_FRESH_S."""
        now = time.monotonic()
        if self._mango and now - self._mango_checked < LOG_RESCAN_S and os.path.exists(self._mango):
            return self._mango
        self._mango_checked = now

        def mtime(f: str) -> float:
            try:
                return os.path.getmtime(f)
            except OSError:
                return 0.0
        files = [f for f in glob.glob(os.path.join(self.home or "", "mangoapp_*.csv")) if not f.endswith("_summary.csv")]
        f = max(files, key=mtime) if files else None
        if f and time.time() - mtime(f) > MANGOAPP_FRESH_S:
            f = None
        if f != self._mango:
            # a log that was already running (another tool keeps it on): only what comes from now on is this game's
            self._mango_pos = 0 if self._mango_own else (os.path.getsize(f) if f and os.path.exists(f) else 0)
            self._mango = f
        return f

    def _mangoapp_frametime(self) -> tuple[float | None, float | None]:
        """Frame time and rate from mangoapp's per-frame log, over the frames since the last read."""
        if not self.home:
            return None, None
        now = time.monotonic()
        f = self._mangoapp_log()
        if f is None or now - self._mango_row_at > MANGOAPP_RELOG_S:
            if now - self._mango_asked >= MANGOAPP_RELOG_S:
                self._mango_asked, self._mango_row_at = now, now
                if mangoapp_command("logging=1") and f is None:
                    self._mango_own = True
                    log.info("autotune: no MangoHud log from this game; mangoapp logs its frames")
            if f is None:
                return None, None
        try:
            with open(f, "rb") as fh:
                fh.seek(self._mango_pos)
                data = fh.read()
        except OSError:
            return None, None
        end = data.rfind(b"\n")
        if end < 0:
            return None, None
        self._mango_pos += end + 1
        ft = []
        for row in csv.reader(data[:end + 1].decode("utf-8", errors="replace").splitlines()):
            if len(row) >= 2 and row[0].replace(".", "", 1).isdigit():
                try:
                    ms = float(row[1])
                except ValueError:
                    continue
                if ms > 0:
                    ft.append(ms)
        if not ft:
            return None, None
        self._mango_row_at = now
        self.frames_ms = ft
        mean = sum(ft) / len(ft)                   # one row per frame: frames over their time, not a mean of rates
        return mean, 1000.0 / mean

    def set_prefix(self, xell_log: str | None) -> None:
        """The game's prefix log where the shim writes XeLL's frame timing; read from its end, new lines only. A game
        starts here: MangoHud logs from before now are not its frames. mangoapp's log stays on: a new session can come
        inside the running app (an ES-DE game, a new power source), and turning the log off there cuts its frames for
        MANGOAPP_RELOG_S and the log of every other reader. release() turns it off when the app ends."""
        try:
            self._mango_pos = os.path.getsize(self._mango) if self._mango else 0
        except OSError:
            self._mango, self._mango_pos = None, 0
        self._mango_asked, self._mango_checked, self._mango_row_at = -1e9, -1e9, time.monotonic()
        self.frames_ms = []
        self._mango_frames_at, self._own_from = -1e9, None
        self._pace.clear()                         # another game's pace
        self.xell_log, self._xell = xell_log, None
        self._since, self._log = (time.time() - 2, None) if xell_log else (0.0, None)
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
        stats = frame_stats(self.frames_ms, statistics.median(self._pace) if self._pace else None)
        self._pace.extend(self.frames_ms)
        median, p95, slow = stats or (None, None, None)
        rendered = self.rendered_fps()
        fps, src = (rendered, "rendered") if rendered else (shown, "presented")
        return Sample(t=time.monotonic(), frametime_ms=(1000.0 / fps) if fps else ft, fps=fps,
                      gpu_mhz=sysfs.read_int(self.p.gpu_cur_freq) if self.p.gpu_cur_freq else None,
                      gpu_max_mhz=sysfs.read_int(self.p.gpu_max_freq) if self.p.gpu_max_freq else None,
                      cpu_pct=self.cpu_pct(), pkg_w=self.package_power(), gpu_busy=self.gpu_busy(),
                      shown_fps=shown, source=src, frames=len(self.frames_ms) if stats else None,
                      ft_median=median, ft_p95=p95, slow_pct=slow)


def frame_stats(frames_ms: list[float], pace_ms: float | None = None) -> tuple[float, float, float] | None:
    """(median ms, 95th percentile ms, % of the frames more than SLOW over the pace) of a run of frame times; None for
    no frames. The pace is the frame time the game held before these frames; without one, their own median."""
    if not frames_ms:
        return None
    srt = sorted(frames_ms)
    median = statistics.median(srt)
    line = (pace_ms or median) * SLOW
    return median, srt[min(len(srt) - 1, int(len(srt) * 0.95))], 100.0 * sum(1 for ms in srt if ms > line) / len(srt)


def mangoapp_command(cmd: str) -> bool:
    """One command to mangoapp's control socket (":logging=1;"); False when no mangoapp listens."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            s.connect(MANGOAPP_SOCKET)
            s.sendall(f":{cmd};".encode())
        return True
    except OSError:
        return False


def summary(s: Session) -> str:
    """One line for the toast at game exit: what autotune found, or that it didn't get far enough to save it."""
    l = s.learned
    if not s.settled or not (l.fps or l.max_fps):
        return "Autotune was still learning; nothing new saved. It goes on next time."
    fps = l.fps or l.max_fps
    use = l.start if l.start is not None else l.tdp
    saved = (l.ceiling or s.ceiling_tdp) - use
    return (f"Autotune held {fps:.0f} fps with {use} W of your {l.ceiling or s.ceiling_tdp} W"
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
    """What a game ended up with, saved per game and power source."""
    tdp: int                        # the limit in use now
    cpu_max_pct: int = 100
    gpu_min_mhz: int | None = None
    boost: bool = True              # CPU boost (intel_pstate no_turbo = 0)
    gpu_profile: str | None = None  # xe power_profile; None = left as it was
    bound: str = "balanced"
    max_fps: float | None = None    # what your full limit gives (the governor's reference), rendered fps
    fps: float | None = None        # what the limit in use gives
    ceiling: int | None = None      # your limit this was found under (another limit: start over)
    samples: int = 0
    target: int | None = None       # target fps mode: your target this was found for
    target_eff: float | None = None # ... the fps held when yours was out of reach
    start: int | None = None        # where to start next time: the level under which 80 % of the holding time lay


NO_FPS_S = 30          # seconds without any frame rate before autotune gives up on a game
# Emulators whose frame rate is not the game's: xemu presents 60 frames a second whatever the emulated game does
# (Project Gotham Racing 2 at a 16 W limit: 16-18 game fps, 60 shown; 2026-11-14), and under its own draw (~20 W on a
# Claw 8 EX) the game slows down. No frame rate can show Autotune what a lower limit costs there, so it stays out of
# these and your limit stays on the chip. Looked for (process name, else the file it runs) every few seconds of a
# game: a frontend starts the emulator after the game-start event, a launcher minutes later.
OWN_PACE = {"xemu": "it shows 60 fps whatever the game's speed"}
OWN_PACE_CHECK_S = 3
PROC = "/proc"


def _own_pace_name(base: str, name: str) -> str | None:
    """The OWN_PACE emulator the process under `base` (/proc/<pid>) is, or None: by its name, else by the file it
    runs. xemu from its AppImage runs as "AppRun" (a link to usr/bin/xemu; a program started through a link has the
    link's name): by the name alone Autotune governed it on its 60 shown frames, down to 8 W (Project Gotham Racing 2,
    2026-11-15)."""
    if name in OWN_PACE:
        return name
    try:
        exe = os.path.basename(os.readlink(os.path.join(base, "exe")))
    except OSError:
        return None
    return exe if exe in OWN_PACE else None


def own_pace_emulator(proc: str | None = None) -> str | None:
    """The first OWN_PACE emulator running now, or None. One walk over /proc (see _own_pace_name)."""
    proc = proc or PROC
    try:
        pids = [d for d in os.listdir(proc) if d.isdigit()]
    except OSError:
        return None
    for d in pids:
        try:
            with open(os.path.join(proc, d, "comm"), encoding="utf-8", errors="replace") as f:
                name = f.read().strip()
        except OSError:
            continue
        emu = _own_pace_name(os.path.join(proc, d), name)
        if emu:
            return emu
    return None


def game_process(appid: str, proc: str | None = None) -> bool:
    """Steam started this game's process: its reaper runs with AppId=<appid> (not the install script's, which also has
    Install=1). One walk over /proc/*/cmdline."""
    proc = proc or PROC
    want = f"AppId={appid}".encode()
    try:
        pids = [d for d in os.listdir(proc) if d.isdigit()]
    except OSError:
        return False
    for d in pids:
        try:
            with open(os.path.join(proc, d, "cmdline"), "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        head = args[:args.index(b"--")] if b"--" in args else args
        if want in head and b"Install=1" not in head:
            return True
    return False


@dataclass
class Session:
    appid: str
    floor_tdp: int
    ceiling_tdp: int
    learned: Learned
    phase: str = "target"           # (the log's and the panel's word)
    paused: bool = False
    gov: governor.State | None = None   # the governor's memory (governor.py)
    cfg: governor.Cfg | None = None
    look_s: float = governor.LOOK_S     # when the loop looks again
    started_at: float = 0.0
    stepped_at: float | None = None
    logged_at: float = -1e9
    own_checked_at: float = 0.0     # when the OWN_PACE emulators were last looked for
    multiplier: float | None = None     # presented / rendered when frame generation is on
    last_change_at: float | None = None
    last_action: str | None = None
    notes: list = field(default_factory=list)   # decisions since the last row of autotune.csv (its "note" column)
    row_ms: list = field(default_factory=list)  # frame times since that row (it looks more often than it logs) ...
    row_before: deque = field(default_factory=lambda: deque(maxlen=PACE_FRAMES))   # ... and the frames before those
    last_delta: str | None = None
    avg_fps: float | None = None
    low_fps: float | None = None
    fps_per_w: float | None = None
    source: str = "presented"
    thermal_cap: int | None = None      # the temperature guard stepped the TDP down to this (thermal.py): stay under it
    clock_pct: int | None = None        # ... or capped the CPU's top clock at this
    no_fps_s: float = 0.0               # seconds in a row without a frame rate
    row_at: float | None = None         # when a log with a row a second last gave frames (its next row covers the time since)
    no_frames: bool = False             # gave up: this game's frame rate can't be read
    own_pace: str | None = None         # an emulator whose frame rate isn't the game's (OWN_PACE): autotune stays out
    settled: bool = False               # it held a limit for a minute: what it found is worth saving
    short_tau: bool = False             # the PL1 time window is GOVERN_TAU_US right now
    tau_refused: bool = False           # the firmware refused it once: don't retry every step
    history: list[Sample] = field(default_factory=list)
    fg: int = 0                         # ClawDeck's frame generation multiplier for the game (0: none)
    mult_hist: deque = field(default_factory=lambda: deque(maxlen=5))   # shown / rendered, one per XeLL line
    mult_seen: float | None = None      # the XeLL line it was last taken at, and the shown frames since
    mult_n: int = 0
    mult_ms: float = 0.0
    refused: bool = False               # the last TDP write was not applied ...
    refused_until: float = -1e9         # ... and until then no other is tried (the governor rests)
    user_target: int = 0

    @property
    def target(self) -> float:
        if self.gov is None or self.cfg is None:
            return float(self.user_target)
        return self.gov.ref if governor.out_of_reach(self.gov, self.cfg) else float(self.user_target)

    @property
    def unreachable(self) -> bool:
        return bool(self.gov is not None and self.gov.ref is not None
                    and self.gov.ref < self.user_target * governor.REACHED)


class Autotune:
    def __init__(self, hardware: hw.Hardware, pw: power.Power, log_dir: str, csv_log: str | None = None,
                 home: str | None = None):
        self.hw = hardware
        self.power = pw
        self.sampler = Sampler(hardware, log_dir, home)
        self.csv_log = csv_log
        self._csv_checked = False                  # an autotune.csv with other columns is set aside once (_log)
        self.session: Session | None = None
        self.plugged: bool | None = None
        self.task: asyncio.Task | None = None
        self.enabled = False

    # ---- lifecycle ---------------------------------------------------------------------------------------------------
    def target_of(self, target: int | None) -> int:
        """The target in force. No target set: the screen's highest refresh rate, so the most frames the limit gives
        are held."""
        if target:
            return int(target)
        return max(self.hw.model.get("panel_hz") or [60])

    def start(self, appid: str, learned: dict | None, floor_tdp: int, ceiling_tdp: int, plugged: bool | None = None,
              xell_log: str | None = None, target: int | None = None,
              fg: int = 0, at_limit: bool = False, held: int | None = None) -> None:
        """Start for a game: where it held last time under this limit and target, else as held before a power change,
        else mid-range. at_limit: the limit was picked just now, in the game, and goes on the chip as picked."""
        self.plugged = plugged
        known = {k: v for k, v in (learned or {}).items() if k in Learned.__dataclass_fields__}
        target = self.target_of(target)
        same = known.get("ceiling") == ceiling_tdp and self.target_of(known.get("target")) == target
        l = Learned(tdp=ceiling_tdp, ceiling=ceiling_tdp, target=target)
        s = Session(appid=appid, floor_tdp=floor_tdp, ceiling_tdp=ceiling_tdp, learned=l,
                    user_target=target, fg=int(fg or 0))
        s.started_at = s.own_checked_at = time.monotonic()
        self.session = s
        self.sampler.set_prefix(xell_log)
        self.power.governed = True
        self._levers(l)
        # a result saved by an earlier build has no start level: the limit it ended with is the next best thing
        before = (known.get("start") or known.get("tdp")) if same else None
        if isinstance(before, bool) or not isinstance(before, (int, float)) or not math.isfinite(before):
            before = None                                 # a record edited by hand
        emu = own_pace_emulator()                         # already running (a resume, a new power source)
        self._govern_from(s, ceiling_tdp if at_limit or emu else held if before is None else int(before))
        l.start = None if at_limit or before is None else int(before)
        if emu:
            self._stay_out(s, emu)
        else:
            self._apply(l)
            self._tau(s, True)
        if self.task is None or self.task.done():
            self.task = asyncio.get_event_loop().create_task(self._loop())

    def _govern_from(self, s: Session, start_w: int | None) -> None:
        """A fresh governor for this session, from `start_w` (None: mid-range); the caller writes its limit."""
        s.cfg = governor.Cfg(floor=s.floor_tdp, ceiling=s.ceiling_tdp, target=float(s.user_target),
                             start_w=start_w)
        s.gov, act = governor.start(s.cfg, time.monotonic())
        s.learned.tdp = min(act.limit_w, self._top(s))
        s.gov.limit = s.learned.tdp
        s.look_s, s.stepped_at = governor.LOOK_S, None
        s.last_action = act.note

    def set_target(self, target: int | None) -> None:
        """The target was changed while a game runs: the governor starts over from the limit in use, for the new goal.
        After a change by hand it hands control back, as a new pick does (not in a game Autotune stays out of, not
        without frames, not while it is switched off)."""
        s = self.session
        if not s:
            return
        back = s.paused and self.enabled and not s.own_pace and s.no_fps_s < NO_FPS_S
        if back:
            s.paused, self.power.governed = False, True
        s.user_target = self.target_of(target)
        s.learned.target, s.learned.target_eff, s.learned.start = s.user_target, None, None
        s.settled = False
        on_chip = s.learned.tdp
        self._govern_from(s, on_chip)
        want, s.learned.tdp = s.learned.tdp, on_chip      # (under the guard's cap it may start lower than the chip is)
        if not s.paused:                                  # paused (xemu, no frames, switched off): it applies when that ends
            if want != on_chip or back:
                self._tdp(s.learned, want, force=True)    # a refused write leaves what is on the chip as the limit
            self._tau(s, True)
        s.gov.limit = s.learned.tdp

    def set_floor(self, watts: int) -> None:
        """The lowest TDP autotune may use (the model minimum unless the user sets one)."""
        s = self.session
        if not s:
            return
        lo = self.hw.model["tdp"]["min"]
        s.floor_tdp = max(lo, min(int(watts), s.ceiling_tdp))
        if s.cfg:
            s.cfg.floor = s.floor_tdp
        if s.learned.tdp < s.floor_tdp and self._tdp(s.learned, s.floor_tdp) and s.gov:
            s.gov.limit = s.learned.tdp
            governor.drop(s.gov)

    def thermal_step(self, watts: int) -> bool:
        """The temperature guard (thermal.py) brings the TDP down: from now on autotune works under `watts`."""
        s = self.session
        if not s:
            return False
        l = s.learned
        if not self._tdp(l, min(l.tdp, int(watts)), force=True):
            return False
        s.thermal_cap = int(watts)
        if s.gov:
            s.gov.limit = l.tdp
            governor.drop(s.gov)
        self._did(s, f"thermal: stepped to {l.tdp} W", "thermal")
        return True

    def thermal_clock(self, pct: int | None) -> None:
        """The temperature guard capped the CPU's top clock (a hot spot) or took the cap off: the same watts give other
        frames now, so what failed or held before says nothing."""
        s = self.session
        if not s:
            return
        s.clock_pct = pct
        if s.gov and pct is None:
            governor.forget(s.gov)                   # (also the band and the ratio: they were measured under the cap)
        elif s.gov:
            s.gov.fail.clear()
            s.gov.misses, s.gov.home = 0, None
        self._did(s, "thermal: CPU clock " + ("uncapped" if pct is None else f"capped at {pct} %"), "thermal")

    def thermal_release(self, watts: int) -> bool:
        """The package cooled down: the guard lets autotune use up to `watts` again. Nothing is raised here; the
        governor forgets what it measured under the cap, and climbs if the game needs the watts. A paused autotune
        (xemu, no frame rate, a manual change) never climbs: there the TDP itself goes back up with the guard, as for a game
        without autotune."""
        s = self.session
        if not s or s.thermal_cap is None:
            return False
        cap, s.thermal_cap = s.thermal_cap, None if watts >= s.ceiling_tdp else int(watts)
        if s.paused and not self._tdp(s.learned, self._top(s), force=True):
            s.thermal_cap = cap                           # not applied: the guard tries this step again
            return False
        self._did(s, "thermal: cooled, the full TDP allowed again" if s.thermal_cap is None
                  else f"thermal: cooled, up to {s.thermal_cap} W allowed", "thermal")
        return True

    def held_w(self) -> int | None:
        """The limit Autotune holds, for the temperature guard: while it looks at the full limit for a second (after a
        loss, or to see what the full limit gives), the limit before that. The guard compares it with what the package
        drew over its last 10 s, and the second at the full limit made a real overload look like a hot spot."""
        s = self.session
        if not s:
            return None
        if s.gov is not None and not s.paused and s.gov.mode in ("recover", "check"):
            return min(s.learned.tdp, s.gov.prev)
        return s.learned.tdp

    def carry_w(self) -> int | None:
        """Where the game held, for a restart on another power source: during a step down the level before it, during
        a look at the full limit after a loss that limit; nothing when paused or under the temperature guard's cap (the
        restart resets the guard)."""
        s = self.session
        if s is None or s.paused or s.thermal_cap is not None:
            return None
        return s.gov.prev if s.gov is not None and s.gov.mode == "probe" else s.learned.tdp

    def _top(self, s: Session) -> int:
        """The most autotune may use: the set TDP, or less while the temperature guard holds it down."""
        return s.ceiling_tdp if s.thermal_cap is None else min(s.ceiling_tdp, s.thermal_cap)

    def pause(self, reason: str = "manual change", limit: int | None = None) -> None:
        """Never fight the user: a manual power change stops autotune for the rest of this session (values the user
        set stay; what was found so far is still saved at game exit). `limit`: the TDP given back from now on, in place
        of Autotune's maximum (Autotune switched off: the TDP setting), under the temperature guard's step."""
        s = self.session
        if s:
            if limit is not None:
                s.ceiling_tdp = int(limit)
                if s.paused:
                    self._give_back(s)
            if not s.paused:
                log.info("autotune paused: %s", reason)
                self._note(s, f"paused: {reason}")
                self._give_back(s)                        # not a limit of Autotune's for the rest of the session
                self._tau(s, False)
            s.paused, s.no_frames = True, False           # yours, also after it gave up: frames later don't undo it

    def running(self) -> bool:
        return self.session is not None

    def release_frames(self) -> None:
        """The game (the Steam app) ended: the mangoapp log Autotune turned on goes off. A session that ends inside the
        running app (an ES-DE game, a new power source) leaves it on for the next one."""
        self.sampler.release()

    def stop(self, revert: bool = True) -> dict | None:
        """End the session; what it learned is returned for the game's profile. revert=False leaves the power as it is
        for the caller, which reverts and sets the user's limit again itself (main.py)."""
        s = self.session
        self.session = None
        self.sampler.set_prefix(None)
        self.power.governed = False
        if s:
            self._remember(s)
            if revert:
                self.power.revert_all()
            return {"learned": s.learned.__dict__, "samples": len(s.history), "settled": s.settled,
                    "summary": summary(s)}
        return None

    def _remember(self, s: Session) -> None:
        """What the next start of this game needs: where it held (governor.start_level)."""
        if s.gov is None:
            return
        level = governor.start_level(s.gov.held_s, s.floor_tdp, s.learned.ceiling or s.ceiling_tdp)
        if level is not None:
            s.learned.start, s.settled = level, True

    def _levers(self, l: Learned) -> None:
        """The other levers as the chip has them, for the log: Autotune sets the limit only."""
        p = self.hw.paths
        cpu = sysfs.read_int(p.cpu_max_perf_pct) if p.cpu_max_perf_pct else None
        turbo = self.power.turbo()
        l.cpu_max_pct = l.cpu_max_pct if cpu is None else cpu
        l.gpu_min_mhz = sysfs.read_int(p.gpu_min_freq) if p.gpu_min_freq else None
        l.boost = l.boost if turbo is None else turbo

    def _give_back(self, s: Session) -> None:
        """Autotune stays out of this game: your limit goes back on the chip (under the temperature guard's step, if
        any), with the PL2 you set by hand, if you did."""
        self.power.governed = False
        self._tdp(s.learned, s.ceiling_tdp, force=True)

    def _stay_out(self, s: Session, emu: str) -> None:
        """An OWN_PACE emulator runs: your limit on the chip and nothing more changed, whatever frames come later."""
        s.own_pace, s.paused, s.no_frames = emu, True, False
        self._give_back(s)
        self._tau(s, False)
        log.info("autotune: %s runs, %s: it stays out of this game (%d W, your limit)", emu, OWN_PACE[emu], s.learned.tdp)
        self._note(s, f"out: {emu} runs, {s.learned.tdp} W (your limit)")

    def _apply(self, l: Learned) -> None:
        self.power.set_tdp(l.tdp, self.plugged)

    # ---- the loop ----------------------------------------------------------------------------------------------------
    async def _loop(self) -> None:
        while self.session is not None:
            try:
                self._step()
            except Exception as e:   # never let a sampling error kill the loop
                log.exception("autotune step failed: %s", e)
            await asyncio.sleep(self.session.look_s if self.session is not None else 1.0)

    def _frames(self, smp: Sample, dt: float) -> list[tuple[float, float]]:
        """The frames since the last step as (end time, frame time ms): mangoapp's, one row per frame; from a log with
        one row a second, the frames of the time since its row before, at their average (the looks in between have
        none: with only this look's half second of them the governor saw a frame rate with holes and never called it
        steady, so it never stepped down on such a log; review, 2026-11-15)."""
        ms = list(self.sampler.frames_ms)
        s = self.session
        if ms:
            if s is not None:
                s.row_at = None
        elif smp.shown_fps and dt > 0:
            span = dt if s is None or s.row_at is None else max(dt, min(2.0, smp.t - s.row_at))
            if s is not None:
                s.row_at = smp.t
            ms = [1000.0 / smp.shown_fps] * max(1, round(smp.shown_fps * span))
        out, end = [], smp.t
        for x in reversed(ms):
            out.append((end, x))
            end -= x / 1000.0
        out.reverse()
        return out

    def _step(self) -> None:
        s = self.session
        if s is None:
            return
        smp = self.sampler.sample()
        dt = smp.t - s.stepped_at if s.stepped_at is not None else 0.0
        s.stepped_at = smp.t
        s.history.append(smp)
        if len(s.history) > 600:
            del s.history[:-600]
        if self.csv_log:
            s.row_ms.extend(self.sampler.frames_ms)
            if smp.t - s.logged_at >= 0.95:              # a row a second, also while it looks ten times a second
                s.logged_at = smp.t
                self._log(s, smp)
        if s.own_pace is None and (smp.t - s.own_checked_at >= OWN_PACE_CHECK_S or (smp.fps and s.no_frames)):
            s.own_checked_at = smp.t
            emu = own_pace_emulator()
            if emu:
                self._stay_out(s, emu)
        if smp.fps and s.no_frames:
            # frames after all: a game slower than NO_FPS_S to its first frame (Order of the Sinking Star takes 54 s).
            # Giving up put your pick back; autotune takes over from there
            s.no_frames, s.paused, s.no_fps_s = False, False, 0.0
            self.power.governed = True
            held = s.gov.held_s if s.gov else {}
            self._govern_from(s, s.learned.tdp)
            s.gov.held_s = held                               # what this session held before the frames stopped
            self._tdp(s.learned, s.learned.tdp, force=True)   # with PL2 as Autotune keeps it, from this look on
            log.info("autotune: frame rate from %s after all: it takes over from %d W", s.appid, s.learned.tdp)
            self._note(s, f"frames after all: it takes over from {s.learned.tdp} W")
        if smp.fps:
            s.no_fps_s = 0.0
        elif not s.paused:
            # no frame rate (a game whose frames neither mangoapp nor a MangoHud log shows; or its frames stopped
            # coming, and a lowered limit would stay on the chip unwatched). Give up: the limit you picked goes back
            # on the chip, nothing more is changed until frames come, and the panel says why
            s.no_fps_s += dt
            if s.no_fps_s >= NO_FPS_S:
                s.paused, s.no_frames = True, True
                self._give_back(s)
                self._tau(s, False)
                log.info("autotune: no frame rate from %s after %d s: it stays out of this game (%d W, your limit)",
                         s.appid, NO_FPS_S, s.learned.tdp)
                self._note(s, f"out: no frame rate, {s.learned.tdp} W (your limit)")
        if s.paused or s.gov is None or smp.t < s.refused_until:
            s.look_s = 1.0
            return
        l = s.learned
        # frame generation: the governor judges rendered frames. XeLL's figure when the game has it, else ClawDeck's
        # multiplier for the game; a change of it is another game as far as the frames go
        mult = self._multiplier(s, smp)
        if s.multiplier is not None and abs(mult - s.multiplier) >= 0.5:
            governor.forget(s.gov)
            s.gov.last.clear()
            s.gov.pace.clear()
        s.multiplier = mult
        s.source = smp.source if smp.source == "rendered" or s.fg < 2 else "frame-gen pick"
        if smp.fps:
            l.samples += 1
        self._tau(s, True)
        obs = governor.Obs(t=smp.t, frames=self._frames(smp, dt), draw_w=smp.pkg_w, cap_w=s.thermal_cap,
                           thermal=s.clock_pct is not None, multiplier=mult)
        act = governor.step(s.gov, obs, s.cfg)
        s.look_s = act.look_s
        if act.limit_w is not None:
            was = l.tdp
            if self._tdp(l, act.limit_w):
                self._did(s, act.note or f"{l.tdp} W", f"{l.tdp - was:+d} W")
                s.refused = False
            elif l.tdp != act.limit_w:                   # refused: it goes on from what is on the chip, and waits
                # (the governor is not asked meanwhile: a raise it wants was tried again at every look, review 2026-11-15)
                governor.drop(s.gov)
                s.gov.quiet_until, s.look_s = smp.t + REFUSED_S, governor.LOOK_S
                s.refused_until = smp.t + REFUSED_S
                if not s.refused:
                    log.warning("autotune: TDP %d W was not applied; trying again in %d s", act.limit_w, REFUSED_S)
                    self._note(s, f"refused: {act.limit_w} W was not applied")
                s.refused = True
            s.gov.limit = l.tdp                          # what is on the chip (the guard's cap may have cut the write)
        elif act.note and act.note != s.last_action:
            s.last_action = act.note
            self._note(s, act.note)
        if len(s.gov.pace) >= governor.N:
            s.avg_fps = 1000.0 / statistics.median(s.gov.pace)
            s.low_fps = 1000.0 / max(s.gov.pace)
            s.fps_per_w = s.avg_fps / smp.pkg_w if smp.pkg_w else None
            l.fps = s.avg_fps
        l.max_fps, l.bound = s.gov.ref, classify(smp)
        l.target_eff = s.target
        if not s.settled and sum(s.gov.held_s.values()) >= 60:
            s.settled = True                             # a minute of holding: worth saving for the game

    def _multiplier(self, s: Session, smp: Sample) -> float:
        """Shown frames per rendered frame, for the governor: a whole number that changes rarely. XeLL writes its
        rendered-frame figure once per 100 rendered frames (every 1.7 s at 60 fps), so a quotient of this look's shown
        frames and that line stands still between two lines and says nothing about the frames in between; the
        governor then saw no loss at all. The quotient is taken once per XeLL line, over the
        shown frames since the line before, and the median of the last five, rounded, is the multiplier."""
        line = self.sampler._xell
        if line is None or smp.source != "rendered":
            s.mult_seen, s.mult_n, s.mult_ms = None, 0, 0.0
            s.mult_hist.clear()
            return float(s.fg) if s.fg >= 2 else 1.0
        s.mult_n += len(self.sampler.frames_ms)
        s.mult_ms += sum(self.sampler.frames_ms)
        if line[0] != s.mult_seen:
            if s.mult_seen is not None and s.mult_n >= 10 and s.mult_ms > 0:
                s.mult_hist.append((1000.0 * s.mult_n / s.mult_ms) / line[1])
            s.mult_seen, s.mult_n, s.mult_ms = line[0], 0, 0.0
        if not s.mult_hist:
            return float(s.fg) if s.fg >= 2 else (s.multiplier or 1.0)
        return float(max(1, round(statistics.median(s.mult_hist))))

    def _tau(self, s: Session, governing: bool) -> None:
        """RAPL averages PL1 over a 56 s window: for most of a minute after a change the package can still run up to
        PL2, and a step down judged on a few seconds of frames would be judged on the burst. While Autotune governs,
        an 8 s window, set once; the firmware's again when it stops or stays out (findings §22.2). Both through
        sysfs.write, so a revert restores it in any case."""
        if governing and not s.short_tau and not s.tau_refused and self.hw.paths.rapl_pl1_window:
            if self.power.set_pl1_window(GOVERN_TAU_US):
                s.short_tau = True
                log.info("autotune: PL1 time window %d s while it governs", GOVERN_TAU_US // 1_000_000)
            else:
                s.tau_refused = True
                self.power.restore_pl1_window()
                log.warning("autotune: the firmware did not take a %d s PL1 window; keeping its own", GOVERN_TAU_US // 1_000_000)
        elif not governing and s.short_tau:
            self.power.restore_pl1_window()
            s.short_tau = False
            log.info("autotune: PL1 time window back to the firmware's (%s us)", self.power.pl1_window_us())

    def _note(self, s: Session, text: str) -> None:
        s.notes.append(text)
        del s.notes[:-TRACE_NOTES]

    def _did(self, s: Session, action: str, delta: str) -> None:
        s.last_action, s.last_delta, s.last_change_at = action, delta, time.monotonic()
        self._note(s, f"{delta}: {action}")
        log.info("autotune (%s fps): %s", f"{s.avg_fps:.0f}" if s.avg_fps else "?", action)

    def _tdp(self, l: Learned, watts: int, force: bool = False) -> bool:
        """Set the TDP and record it only if it was really applied (a refused write is not a change to learn from).
        Never above the temperature guard's step."""
        s = self.session
        if s is not None and s.thermal_cap is not None and watts > s.thermal_cap:
            watts = s.thermal_cap
        if watts == l.tdp and not force:
            return False
        if not self.power.set_tdp(watts, self.plugged):
            log.debug("autotune: TDP %d W was not applied", watts)
            return False
        l.tdp = watts
        return True

    def _log(self, s: Session, smp: Sample) -> None:
        try:
            if not self._csv_checked:
                # a log written with other columns (an earlier build's): kept as <name>.1, a new one starts
                self._csv_checked = True
                if os.path.exists(self.csv_log):
                    with open(self.csv_log, encoding="utf-8", errors="replace") as f:
                        head = f.readline().strip()
                    if head and head != ",".join(CSV_FIELDS):
                        os.replace(self.csv_log, self.csv_log + ".1")
            new = not os.path.exists(self.csv_log)
            with open(self.csv_log, "a", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(CSV_FIELDS)
                l = s.learned
                r = lambda v, n: None if v is None else round(v, n)
                # the frame columns: every frame since the row before (a look is half a second or a tenth, a row a
                # second), slow against the pace of the frames before them. A log with a row a second has none
                frames, shown = (smp.frames, smp.ft_median, smp.ft_p95, smp.slow_pct), smp.shown_fps
                if s.row_ms:
                    st = frame_stats(s.row_ms, statistics.median(s.row_before) if s.row_before else None)
                    frames, shown = (len(s.row_ms), *st), 1000.0 * len(s.row_ms) / sum(s.row_ms)
                    s.row_before.extend(s.row_ms)
                    s.row_ms = []
                fps = shown if smp.source != "rendered" and shown else smp.fps      # rendered: XeLL's own figure
                w.writerow([f"{smp.t:.1f}", s.appid, s.gov.mode if s.gov and not s.paused else "out", r(fps, 1),
                            r(shown, 1), smp.source, smp.gpu_mhz, smp.gpu_max_mhz, r(smp.cpu_pct, 1),
                            r(smp.pkg_w, 2), l.tdp, l.cpu_max_pct, l.gpu_min_mhz, int(l.boost), l.bound, r(l.max_fps, 1),
                            frames[0], r(frames[1], 2), r(frames[2], 2), r(frames[3], 1),
                            r(governor.goal(s.gov, s.cfg), 1) if s.gov and s.cfg and not s.paused else None,
                            s.multiplier, s.look_s, " | ".join(s.notes)])
                s.notes.clear()
        except OSError:
            pass

    def status(self) -> dict:
        s = self.session
        if not s:
            return {"running": False, "enabled": self.enabled}
        l = s.learned
        last = s.history[-1] if s.history else None
        r = lambda v: None if v is None else round(v, 1)
        governing = not s.paused and s.gov is not None
        return {"running": True, "enabled": self.enabled, "appid": s.appid, "paused": s.paused, "phase": s.phase,
                "step": s.gov.mode if governing else None,
                "learned": l.__dict__, "bound": l.bound, "tdp": l.tdp, "ceiling_tdp": s.ceiling_tdp,
                "floor_tdp": s.floor_tdp, "plugged": self.plugged,
                "saves_w": (s.ceiling_tdp - l.tdp) if (governing and s.ceiling_tdp > l.tdp) else None,
                "max_fps": r(l.max_fps), "avg_fps": r(s.avg_fps), "low_fps": r(s.low_fps),
                "fps_per_w": None if s.fps_per_w is None else round(s.fps_per_w, 2), "source": s.source,
                "multiplier": s.multiplier, "window_s": governor.DWELL_S, "thermal_cap": s.thermal_cap,
                "no_frames": s.no_frames, "own_pace": s.own_pace, "own_pace_why": OWN_PACE.get(s.own_pace or ""),
                "learning": governing and not s.settled,
                "pl1_window_s": None if self.power.pl1_window_us() is None else round(self.power.pl1_window_us() / 1e6, 1),
                "last_action": s.last_action, "last_delta": s.last_delta,
                "target": round(s.target), "user_target": s.user_target,
                "unreachable": s.unreachable, "best_fps": r(l.max_fps) if s.unreachable else None,
                "floor_learned": None, "fg": s.fg,
                "last_change_s": None if s.last_change_at is None else round(time.monotonic() - s.last_change_at),
                "last": None if last is None else last.__dict__}
