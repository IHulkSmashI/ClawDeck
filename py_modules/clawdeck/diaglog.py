"""Diagnostics log (Setup -> Diagnostics log): off until the user turns it on, then on until they turn it off, across
restarts too. For the bug reports a one-off export can't explain (the TDP dropped, the fans went loud, the battery
reading jumped, the controller went to desktop mode): what the Claw did before it, second by second.

  diag/<date>.csv             one row a second: wall-clock time, package power (RAPL energy) and limits (PL1/PL2),
                              package °C, CPU0 MHz, graphics busy % (xe gt idle / i915 RC6 residency), graphics actual
                              and requested MHz, graphics throttle reasons, battery status, current as the driver
                              reports it (mA, uncorrected: the Claw 8 EX's 16-bit error shows as it is), voltage,
                              charge, charger, fan RPMs (every FAN_EVERY s), the running game, Autotune's phase, TDP and last decision,
                              the temperature guard's step, and every TOP_EVERY s the three busiest processes.
  diag/<date>-controller.txt  every report the MSI controller's configuration interface sends (hid-msi's hidraw: the
                              mode and M-keys reports decoded, ACKs), every key on its desktop-mode keyboard, and its
                              mouse moves summed per second. Each reader gets its own copy of a report: nothing is
                              written to the controller and nothing is taken from InputPlumber or Steam.

Frame times are not here: Autotune's MangoHud log has them for the game Autotune runs on.
Earlier days are gzipped; days older than KEEP_DAYS are deleted, and the oldest days first while the folder is over
MAX_BYTES. While on, this is ClawDeck's one timer outside a game (CONTRIBUTING.md "Battery first"): one asyncio task
at 1 Hz, the reads off the event loop (main.py `_diag_loop`).
Cost on a Claw 8 EX (2026-11-14, Steam's home screen, 3 min): 0.36 % of one core; the bare 1 Hz loop is 0.05 %. Reading
the fans every second cost another 0.29 % (msi-wmi-platform answers each read through WMI), hence FAN_EVERY.
"""
from __future__ import annotations

import csv
import glob
import gzip
import logging
import os
import shutil
import struct
import time
from typing import Callable

from . import fan, hw, sysfs

log = logging.getLogger("clawdeck.diaglog")

FIELDS = ["time", "pkg_w", "pl1_w", "pl2_w", "pkg_c", "cpu_mhz", "gpu_busy_pct", "gpu_act_mhz", "gpu_cur_mhz",
          "gpu_throttle", "bat_status", "bat_ma", "bat_mv", "bat_pct", "ac", "fan1_rpm", "fan2_rpm", "game",
          "at_phase", "at_tdp_w", "at_action", "guard_w", "top_cpu"]
KEEP_DAYS = 30
MAX_BYTES = 200_000_000   # the whole folder; a day of rows is about 13 MB, 2 MB gzipped
TOP_EVERY = 10            # walking /proc is most of the cost: the busiest processes over the last 10 s
FAN_EVERY = 5             # MSI's fan readings go through WMI (an ACPI call each): half the cost when read every second
FIND_EVERY = 10           # a controller that went away (resume, re-enumeration) is looked for again this often
CPU_FREQ = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
HID_MSI = "/sys/bus/hid/drivers/hid-msi"
MODES = {1: "xinput", 2: "dinput", 4: "desktop"}          # GAMEPAD_MODE_ACK data[0]
MKEYS = {0: "macro", 1: "disabled", 2: "combination"}     # GAMEPAD_MODE_ACK data[1]
EV = struct.Struct("llHHi")                              # struct input_event on 64-bit
EV_SYN, EV_KEY = 0, 1
KEY_STATES = {0: "up", 1: "down", 2: "repeat"}


def decode_report(data: bytes) -> str:
    """An MCU report (report id 0x10) as hex, trailing zeros cut, with the mode report (cmd 0x27) and ACK (0x06)
    spelled out."""
    body = data[:max(8, len(data.rstrip(b"\0")))]
    s = body.hex(" ")
    if len(data) > 6 and data[4] == 0x27:
        s += f" = mode {MODES.get(data[5], data[5])}, M-keys {MKEYS.get(data[6], data[6])}"
    elif len(data) > 4 and data[4] == 0x06:
        s += " = ACK"
    return s


def find_controller(root: str = HID_MSI, dev: str = "/dev") -> tuple[str | None, list[str]]:
    """hid-msi's configuration hidraw (the HID device with gamepad_mode) and the event nodes of its other interfaces
    (the desktop-mode keyboard and mouse, silent in xinput mode)."""
    cfg, events = None, []
    for d in sorted(glob.glob(f"{root}/*/")):
        if os.path.exists(f"{d}gamepad_mode"):
            raw = glob.glob(f"{d}hidraw/hidraw*")
            if raw:
                cfg = f"{dev}/{os.path.basename(raw[0])}"
        events += [f"{dev}/input/{os.path.basename(e)}" for e in sorted(glob.glob(f"{d}input/input*/event*"))]
    return cfg, events


def _procs(proc: str = "/proc") -> dict[int, tuple[str, int]]:
    out = {}
    for d in os.listdir(proc):
        if d.isdigit():
            s = sysfs.read(f"{proc}/{d}/stat")
            if s and ")" in s:
                f = s[s.rindex(")") + 2:].split()
                try:
                    out[int(d)] = (s[s.index("(") + 1:s.rindex(")")], int(f[11]) + int(f[12]))   # utime + stime
                except (IndexError, ValueError):
                    pass
    return out


class DiagLog:
    def __init__(self, folder: str, paths: hw.Paths, fan_hw: str | None, plugged: Callable[[], bool | None]):
        self.folder, self.p, self.plugged = folder, paths, plugged
        self.fan_rpm = {n: os.path.join(fan_hw, f"fan{n}_input") for n in ([1] if fan.kind(fan_hw) == "oxp" else fan.fans(fan_hw))} if fan_hw else {}
        cur = paths.gpu_cur_freq
        act = cur.replace("cur_freq", "act_freq") if cur else None
        self.gpu_act = act if act and os.path.exists(act) else None
        freq0 = os.path.dirname(cur) if cur else ""
        idle = [f"{os.path.dirname(freq0)}/gtidle/idle_residency_ms", f"{paths.gpu_card}/power/rc6_residency_ms"]
        self.gpu_idle = next((f for f in idle if paths.gpu_card and os.path.exists(f)), None)
        self.throttle = f"{freq0}/throttle/reasons" if freq0 and os.path.exists(f"{freq0}/throttle/reasons") else None
        self._prev: tuple[float, int | None, int | None] | None = None   # monotonic, energy µJ, gpu idle ms
        self._procs: tuple[float, dict] | None = None
        self._n = 0
        self._day: str | None = None
        # the controller, read on the event loop
        self._loop = None
        self._fds: dict[int, str] = {}       # fd -> "cfg" or "eventN"
        self._moves: dict[str, int] = {}     # desktop-mode mouse/axis events this second, per node
        self._missing_since = 0

    # ---- one row a second (runs in a worker thread) -------------------------------------------------------------
    def row(self, state: dict) -> None:
        """state: what the event loop knows: game, at (autotune.status()), guard_w."""
        now = time.monotonic()
        day = time.strftime("%Y-%m-%d")
        if day != self._day:
            self._day = day
            self.rotate()
        e = sysfs.read_int(self.p.rapl_energy) if self.p.rapl_energy else None
        gi = sysfs.read_int(self.gpu_idle) if self.gpu_idle else None
        pkg_w = busy = None
        if self._prev and now > self._prev[0]:
            dt = now - self._prev[0]
            if e is not None and self._prev[1] is not None and e >= self._prev[1]:   # else the counter wrapped
                pkg_w = round((e - self._prev[1]) / 1e6 / dt, 1)
            if gi is not None and self._prev[2] is not None:
                busy = max(0, min(100, round(100 - (gi - self._prev[2]) / (dt * 10))))
        self._prev = (now, e, gi)
        self._n += 1
        top = ""
        if self._n % TOP_EVERY == 0 or self._procs is None:
            top = self._top(now)
        b = self.p.battery
        ua = sysfs.read_int(f"{b}/current_now") if b else None
        uv = sysfs.read_int(f"{b}/voltage_now") if b else None
        rpm = {n: sysfs.read_int(p) for n, p in self.fan_rpm.items()} if self._n % FAN_EVERY == 1 else {}
        at = state.get("at") or {}
        action = " ".join(x for x in (at.get("last_action"), at.get("last_delta") and f"({at['last_delta']})") if x)
        pl = [sysfs.read_int(x) if x else None for x in (self.p.rapl_pl1, self.p.rapl_pl2)]
        temp = sysfs.read_int(self.p.pkg_temp) if self.p.pkg_temp else None
        cpu = sysfs.read_int(CPU_FREQ)
        ac = self.plugged()
        vals = [time.strftime("%Y-%m-%d %H:%M:%S"), pkg_w, *[None if v is None else v // 1_000_000 for v in pl],
                None if temp is None else round(temp / 1000), None if cpu is None else cpu // 1000, busy,
                sysfs.read_int(self.gpu_act) if self.gpu_act else None,
                sysfs.read_int(self.p.gpu_cur_freq) if self.p.gpu_cur_freq else None,
                "+".join((sysfs.read(self.throttle) or "").split()) if self.throttle else None,
                sysfs.read(f"{b}/status") if b else None, None if ua is None else int(ua / 1000),
                None if uv is None else uv // 1000, sysfs.read_int(f"{b}/capacity") if b else None,
                None if ac is None else int(ac), rpm.get(1), rpm.get(2), state.get("game"),
                at.get("phase") if at.get("running") else None, at.get("tdp") if at.get("running") else None,
                action or None, state.get("guard_w"), top]
        path = os.path.join(self.folder, f"{day}.csv")
        try:
            new = not os.path.exists(path)
            with open(path, "a", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(FIELDS)
                w.writerow(["" if v is None else v for v in vals])
        except OSError as ex:
            log.debug("diag csv: %s", ex)

    def _top(self, now: float) -> str:
        """The three busiest processes since the last walk, in % of one core."""
        cur = _procs()
        prev, self._procs = self._procs, (now, cur)
        if not prev or now <= prev[0]:
            return ""
        hz, span, old = os.sysconf("SC_CLK_TCK"), now - prev[0], prev[1]
        used = sorted(((t - old.get(pid, (None, 0))[1], name) for pid, (name, t) in cur.items()), reverse=True)[:3]
        return ";".join(f"{name}:{t * 100 / hz / span:.0f}%" for t, name in used if t * 100 / hz / span >= 0.5)

    def rotate(self) -> None:
        """Earlier days gzipped; then days past KEEP_DAYS, and the oldest while over MAX_BYTES, deleted."""
        try:
            os.makedirs(self.folder, exist_ok=True)
            today = time.strftime("%Y-%m-%d")
            for p in glob.glob(os.path.join(self.folder, "*.csv")) + glob.glob(os.path.join(self.folder, "*.txt")):
                if not os.path.basename(p).startswith(today):
                    with open(p, "rb") as src, gzip.open(p + ".gz", "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    os.remove(p)
            files = sorted(glob.glob(os.path.join(self.folder, "*")))
            days = sorted({os.path.basename(f)[:10] for f in files})
            drop = set(days[:-KEEP_DAYS])
            size = sum(os.path.getsize(f) for f in files)
            for d in days[:-1]:                        # never today
                if size <= MAX_BYTES and d not in drop:
                    break
                for f in [f for f in files if os.path.basename(f).startswith(d)]:
                    size -= os.path.getsize(f)
                    os.remove(f)
        except OSError as ex:
            log.warning("diag log rotation: %s", ex)

    # ---- the controller (on the event loop) ----------------------------------------------------------------------
    def start(self, loop) -> None:
        self._loop = loop
        self._open()

    def stop(self) -> None:
        for fd in list(self._fds):
            self._close(fd)
        self._loop = None

    def tick(self) -> None:
        """Once a second on the event loop: the mouse moves summed, and a controller that went away looked for."""
        for name, n in self._moves.items():
            if n:
                self._line(f"{name} {n} move events")
        self._moves = dict.fromkeys(self._moves, 0)
        if self._loop and not any(v == "cfg" for v in self._fds.values()):
            self._missing_since += 1
            if self._missing_since % FIND_EVERY == 0:
                self._open()

    def _open(self) -> None:
        cfg, events = find_controller()
        for path, name in [(cfg, "cfg")] + [(e, os.path.basename(e)) for e in events]:
            if not path or name in self._fds.values():
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as ex:
                log.debug("diag log: %s: %s", path, ex)
                continue
            self._fds[fd] = name
            self._loop.add_reader(fd, self._read, fd)
            self._line(f"{name} {path} opened")

    def _close(self, fd: int) -> None:
        name = self._fds.pop(fd, None)
        try:
            if self._loop:
                self._loop.remove_reader(fd)
            os.close(fd)
        except (OSError, ValueError):
            pass
        self._moves.pop(name, None)
        self._missing_since = 0

    def _read(self, fd: int) -> None:
        name = self._fds.get(fd)
        try:
            data = os.read(fd, 64 if name == "cfg" else EV.size * 64)
        except BlockingIOError:
            return
        except OSError as ex:                          # unplugged, re-enumerated
            self._line(f"{name} gone: {ex}")
            self._close(fd)
            return
        if not data:
            self._line(f"{name} closed")
            self._close(fd)
            return
        if name == "cfg":
            self._line(f"cfg {decode_report(data)}")
            return
        keys = []
        for i in range(0, len(data) - EV.size + 1, EV.size):
            _s, _us, typ, code, value = EV.unpack_from(data, i)
            if typ == EV_KEY:
                keys.append(f"key {code} {KEY_STATES.get(value, value)}")
            elif typ != EV_SYN:
                self._moves[name] = self._moves.get(name, 0) + 1
        if keys:
            self._line(f"{name} " + ", ".join(keys))

    def _line(self, text: str) -> None:
        t = time.time()
        path = os.path.join(self.folder, time.strftime("%Y-%m-%d", time.localtime(t)) + "-controller.txt")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t % 1 * 1000):03d} {text}\n")
        except OSError as ex:
            log.debug("diag controller log: %s", ex)


def status(folder: str) -> dict:
    """Setup's line: how many days are kept and how much room they take."""
    files = glob.glob(os.path.join(folder, "*"))
    return {"folder": folder, "days": len({os.path.basename(f)[:10] for f in files}),
            "bytes": sum(os.path.getsize(f) for f in files if os.path.isfile(f))}


def _tail(path: str, lines: int, chunk: int = 256_000) -> list[str]:
    """The last lines of a file without reading all of it (a day's CSV is about 13 MB)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - chunk))
            return f.read().decode("utf-8", "replace").splitlines()[-lines:]
    except OSError:
        return []


def report(folder: str, rows: int = 600, lines: int = 300) -> dict | None:
    """For the exported report: the last 10 minutes of rows and the last controller lines, plus which days are kept
    (attach those files for anything older)."""
    if not os.path.isdir(folder):
        return None
    day = time.strftime("%Y-%m-%d")
    csv_path = os.path.join(folder, f"{day}.csv")
    return {"files": sorted(os.path.basename(f) for f in glob.glob(os.path.join(folder, "*"))),
            "fields": FIELDS,
            "rows": [r for r in _tail(csv_path, rows) if not r.startswith("time,")],
            "controller": _tail(os.path.join(folder, f"{day}-controller.txt"), lines)}
