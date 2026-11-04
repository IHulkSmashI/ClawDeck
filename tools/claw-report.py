#!/usr/bin/env python3
"""claw-report.py - what a claw-watch.sh recording shows, per game: frame rate, power, frames per watt, and whether
XeSS / FSR 4 / frame generation / XeLL ran where ClawDeck set them up (on the XMX driver).

    python3 claw-report.py              newest recording in ~/clawdeck-test
    python3 claw-report.py <folder>     a given one
Writes report.txt next to the recording and prints it.
"""
from __future__ import annotations

import csv
import glob
import os
import re
import statistics
import sys


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))]


def num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    root = os.path.expanduser("~/clawdeck-test")
    d = sys.argv[1] if len(sys.argv) > 1 else max(glob.glob(os.path.join(root, "*")), key=os.path.getmtime, default=None)
    if not d or not os.path.isdir(d):
        print("no recording found; start one with: bash claw-watch.sh start")
        return 1
    rows = list(csv.DictReader(open(os.path.join(d, "samples.csv"), encoding="utf-8", errors="replace")))
    out = [f"ClawDeck test report: {d}", open(os.path.join(d, "session.txt"), encoding="utf-8", errors="replace").read().strip(), ""]
    idle = [num(r["pkg_w"]) for r in rows if not r["appid"] and num(r["pkg_w"]) is not None]
    if idle:
        out.append(f"Steam's menus / idle: {len(idle)} s, package {statistics.median(idle):.1f} W median")
    by_game: dict[str, list[dict]] = {}
    for r in rows:
        if r["appid"]:
            by_game.setdefault(r["appid"], []).append(r)
    for appid, rs in by_game.items():
        name = rs[0]["game"]
        fps = [x for x in (num(r["fps"]) for r in rs) if x]
        ft = [x for x in (num(r["frametime_ms"]) for r in rs) if x]
        w = [x for x in (num(r["pkg_w"]) for r in rs) if x is not None]
        bw = [x for x in (num(r["bat_w"]) for r in rs) if x is not None]
        g = [x for x in (num(r["gpu_mhz"]) for r in rs) if x]
        c = [x for x in (num(r["cpu_c"]) for r in rs) if x]
        pl = [x for x in (num(r["pl1_w"]) for r in rs) if x]
        out += ["", f"=== {name} (appid {appid}): {len(rs)} s"]
        if fps:
            low = 1000 / pct(ft, 99) if ft and pct(ft, 99) else None
            out.append(f"  frame rate: median {statistics.median(fps):.1f} fps, average {statistics.mean(fps):.1f}"
                       + (f", 1% low {low:.1f}" if low else "") + " (MangoHud counts generated frames too)")
        else:
            out.append("  frame rate: none recorded (ClawDeck isn't in this game's launch options, or MangoHud is off)")
        if w:
            out.append(f"  package power: median {statistics.median(w):.1f} W (CPU + graphics), TDP limit {statistics.median(pl):.0f} W" if pl else
                       f"  package power: median {statistics.median(w):.1f} W")
        if bw:
            out.append(f"  battery: median {statistics.median(bw):.1f} W (negative = draining)")
        if fps and w and statistics.median(w) > 0:
            out.append(f"  frames per watt: {statistics.median(fps) / statistics.median(w):.2f} fps/W")
        if g:
            out.append(f"  GPU clock: median {statistics.median(g):.0f} MHz, max {max(g):.0f}")
        if c:
            out.append(f"  CPU temperature: median {statistics.median(c):.0f} °C, max {max(c):.0f}")
        snap = os.path.join(d, "games", f"{appid}.txt")
        t = open(snap, encoding="utf-8", errors="replace").read() if os.path.exists(snap) else ""
        drv = re.search(r"^VK_DRIVER_FILES=(.*)$", t, re.M)
        xmx = bool(drv and "clawdeck" in drv.group(1)) or "xess-xmx-linux" in t.split("== GPU driver files")[-1][:2000]
        out.append(f"  XMX driver: {'yes' if xmx else 'NO (Bazzite stock driver)'}")
        for k in ("WINEDLLOVERRIDES", "FSR", "FSR_FG", "XESS_FG", "CLAWDECK_FSR4_XMX", "FSR4_XMX_LITE"):
            m = re.search(rf"^{k}=(.*)$", t, re.M)
            if m:
                out.append(f"  {k} = {m.group(1)}")
        m = re.search(r'"preset": "([^"]+)"', t)
        if m:
            out.append(f"  ClawDeck's OptiScaler in the game, preset {m.group(1)}")
        if "dinput8.dll" in t:
            out.append("  REFramework (dinput8.dll) in the game folder")
        xell = re.findall(r"xellSleep stats: (\d+) calls in ([\d.]+) ms \(([\d.]+) ms/frame\)", t)
        if xell:
            last = xell[-1]
            out.append(f"  XeLL: running ({last[0]} calls, {last[2]} ms per rendered frame = {1000 / float(last[2]):.0f} rendered fps)")
        elif "igdext_xell.log" in t:
            out.append("  XeLL: log present, no frame stats (frame generation off?)")
        else:
            out.append("  XeLL: no log (no XeSS frame generation / XeLL in this game)")
        opti = [l for l in t.splitlines() if re.search(r"(?i)upscaler|xefg|fsr4|framegen|fginput|fg output", l)]
        if opti:
            out.append("  OptiScaler log (last lines about upscaler / frame generation):")
            out += ["    " + l.strip()[:150] for l in opti[-8:]]
        errs = [l for l in t.splitlines() if re.search(r"(?i)\berror\b|failed", l)]
        if errs:
            out.append("  errors seen:")
            out += ["    " + l.strip()[:150] for l in errs[-6:]]
    vrr = []
    cl = os.path.join(d, "clawdeck.log")
    if os.path.exists(cl):
        vrr = [l.strip() for l in open(cl, encoding="utf-8", errors="replace") if "ui: vrr" in l]
    out += ["", "VRR switching (ClawDeck log):"] + (["  " + l[-140:] for l in vrr[-12:]] or ["  none logged"])
    text = "\n".join(out) + "\n"
    open(os.path.join(d, "report.txt"), "w", encoding="utf-8").write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
