"""Debug logs for one launch of one game, so a bug report shows why the XMX cores were missed.

Per-game -> "Debug logs for the next launch" writes <runtime>/launch/<appid>.debug (arm). At that game's next start
the launch wrapper (tools/clawdeck-launch.sh) removes the marker, so it is one launch only, and turns on:
  - Proton's log (PROTON_LOG=1) into <runtime>/launch/debug-<appid>/steam-<appid>.log: which Vulkan driver the game
    really loaded and any library it couldn't load inside Steam's container, plus the driver's own messages
    (ANV_CM_DEBUG=1, the XeSS XMX driver's placeholder and injection lines on stderr);
  - the XeSS XMX shim's trace (XESS_LOG=1 for xess-xmx-linux's Proton hook, IGDEXT_TRACE=1 directly):
    C:\\igdext_trace.log in the prefix; no file at all means XeSS never loaded the shim;
  - OptiScaler's own log (LogToFile, LogLevel 1) when the game has ClawDeck's OptiScaler: which path XeSS took.
    The wrapper puts both ini lines back at the game's next start (<runtime>/launch/<appid>.debug-restore).
Setup -> Save a bug report then carries the lines that matter from each (collect()), not whole logs.
Before this, a tester had to edit launch options and find three files by hand (#150, Claw 8 AI+ on SteamOS).
"""
from __future__ import annotations

import os
import re

from . import xess

MAX_LINES = 400
# Proton log lines worth a bug report: the XMX path and what the game found (driver, vendor, the shim, OptiScaler)
PROTON_KEEP = re.compile(r"xess|igdext|xell|ANV|anv_|vulkan|VK_|libvulkan|icd|driver|intel|vendor|0x8086|0x1002|"
                         r"0x10de|nvapi|dxgi|d3d12|vkd3d|optiscaler|cannot open shared object|err:|hiding|clawdeck|"
                         r"Proton:|Options:|SteamGameId|WINEDLLOVERRIDES", re.I)
# Wine's loaddll trace names every DLL: only the ones on the XeSS path
LOADDLL_KEEP = re.compile(r"xess|igdext|xell|nvapi|dxgi|d3d12|vulkan|optiscaler|fakenvapi|amd_fidelityfx|nvngx", re.I)
OPTI_KEEP = re.compile(r"xess|xmx|dp4a|adapter|spoof|vendor|intel|igdext|libxess|upscaler|error|fail|warn|"
                       r"optiscaler v|dx12|vulkan|nvapi", re.I)


def marker(runtime_dir: str, appid: str) -> str:
    return os.path.join(runtime_dir, "launch", f"{appid}.debug")


def folder(runtime_dir: str, appid: str) -> str:
    return os.path.join(runtime_dir, "launch", f"debug-{appid}")


def _check(appid: str) -> None:
    if not str(appid).isdigit():
        raise ValueError("appid must be numeric")


def arm(runtime_dir: str, appid: str, on: bool, opti_folder: str | None = None) -> dict:
    """Debug logs for this game's next launch (on), or not (off). opti_folder: the folder of ClawDeck's OptiScaler
    in this game, whose OptiScaler.ini gets its log switched on for that launch."""
    _check(appid)
    m = marker(runtime_dir, appid)
    if on:
        os.makedirs(os.path.dirname(m), exist_ok=True)
        with open(m, "w", encoding="utf-8") as f:
            if opti_folder and "\n" not in opti_folder and os.path.isfile(os.path.join(opti_folder, "OptiScaler.ini")):
                f.write(f"opti={opti_folder}\n")
    else:
        try:
            os.remove(m)
        except FileNotFoundError:
            pass
    return state(runtime_dir, appid)


def state(runtime_dir: str, appid: str) -> dict:
    """armed: the next launch logs; captured_at: when the last debug launch started (None: never)."""
    _check(appid)
    try:
        with open(os.path.join(folder(runtime_dir, appid), "started"), encoding="utf-8") as f:
            at = float(f.read().strip() or 0) or None
    except (OSError, ValueError):
        at = None
    return {"armed": os.path.isfile(marker(runtime_dir, appid)), "captured_at": at}


def _lines(path: str, keep: re.Pattern | None, limit: int, head: int = 0) -> dict | None:
    """The first `head` lines plus up to `limit` lines matching `keep` (repeats folded), from a log of any size."""
    try:
        f = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return None
    out, total, last, repeat = [], 0, None, 0
    with f:
        for line in f:
            total += 1
            line = line.rstrip("\r\n")[:400]
            if not line.strip():
                continue
            if total <= head:
                out.append(line)
                continue
            if keep is not None:
                if not keep.search(line):
                    continue
                if "loaddll" in line and not LOADDLL_KEEP.search(line):
                    continue
            # Wine's prefix (pid/tid/timestamp) differs on every line: compare what follows it
            core = re.sub(r"^[0-9a-f.:]+\s*", "", line)
            if core == last:
                repeat += 1
                continue
            if repeat:
                out.append(f"  (same line {repeat} more times)")
                repeat = 0
            last = core
            if len(out) < head + limit:
                out.append(line)
    if repeat:
        out.append(f"  (same line {repeat} more times)")
    return {"lines": out, "total_lines": total}


def _fresh(paths: list[str], since: float | None) -> str | None:
    """The first of paths written during the debug launch (the wrapper keeps the last launch's trace as .prev)."""
    for p in paths:
        try:
            if since is None or os.path.getmtime(p) >= since - 5:
                return p
        except OSError:
            continue
    return None


def collect(runtime_dir: str, home: str, appid: str) -> dict | None:
    """What the last debug launch of this game left, for the bug report. None: no debug launch yet."""
    st = state(runtime_dir, appid)
    d = folder(runtime_dir, appid)
    if st["captured_at"] is None:
        return {"armed": True} if st["armed"] else None
    since = st["captured_at"]
    drive_c = xess.prefix_dir(home, appid)
    trace = _fresh([os.path.join(drive_c, "igdext_trace.log"), os.path.join(drive_c, "igdext_trace.log.prev")], since)
    opti = None
    try:
        with open(os.path.join(d, "opti_folder"), encoding="utf-8") as f:
            od = f.read().strip()
        opti = _fresh([os.path.join(od, "OptiScaler.log")], since)
    except OSError:
        pass
    return {
        "armed": st["armed"],
        "started": since,
        "proton_log": _lines(os.path.join(d, f"steam-{appid}.log"), PROTON_KEEP, MAX_LINES, head=40),
        # no trace written during the debug launch: XeSS never loaded the XMX shim in it
        "igdext_trace": _lines(trace, None, 250) if trace else None,
        "igdext_trace_missing": trace is None,
        "optiscaler_log": _lines(opti, OPTI_KEEP, 250, head=15) if opti else None,
    }
