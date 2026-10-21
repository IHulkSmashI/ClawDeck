#!/usr/bin/env python3
"""soak.py - measure the plugin backend against the battery budget (< 0.5 % CPU, < 30 MB RSS, flat RSS).

  python3 tools/soak.py [--pid PID] [--hours 2] [--interval 10] [--csv ~/clawdeck-soak.csv]

Without --pid it finds the Decky backend process that has ClawDeck's main.py open (Decky runs each plugin in its own
process). Samples /proc/<pid>/stat and /proc/<pid>/status; prints a verdict at the end: CPU % averaged over the run
(of one core), peak RSS, and the RSS slope in MB/hour (a leak shows as a positive slope over a 4-hour soak).
Read-only; safe to run while playing."""
import argparse
import csv
import os
import sys
import time

TICK = os.sysconf("SC_CLK_TCK")


def find_pid() -> int | None:
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if "ClawDeck" in cmd and ("main.py" in cmd or "plugin" in cmd.lower()):
            return int(p)
        try:
            for fd in os.listdir(f"/proc/{p}/fd"):
                if "ClawDeck" in os.readlink(f"/proc/{p}/fd/{fd}"):
                    return int(p)
        except OSError:
            continue
    return None


def cpu_ticks(pid: int) -> int:
    with open(f"/proc/{pid}/stat") as f:
        parts = f.read().rsplit(")", 1)[1].split()
    return int(parts[11]) + int(parts[12])          # utime + stime


def rss_kb(pid: int) -> int:
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return 0


def slope(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return 0.0 if den == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def verdict(cpu_pct: float, peak_mb: float, slope_mb_h: float, hours: float) -> list[str]:
    out = [f"CPU {cpu_pct:.3f} % (budget 0.5): {'ok' if cpu_pct < 0.5 else 'OVER'}",
           f"peak RSS {peak_mb:.1f} MB (budget 30): {'ok' if peak_mb < 30 else 'OVER'}",
           f"RSS slope {slope_mb_h:+.2f} MB/h: {'flat' if abs(slope_mb_h) < 0.5 else 'GROWING - possible leak'}"]
    if hours < 2:
        out.append(f"run was {hours:.2f} h; a release needs 2 h (budget) and 4 h (leak soak)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--csv", default=os.path.expanduser("~/clawdeck-soak.csv"))
    a = ap.parse_args()
    pid = a.pid or find_pid()
    if not pid:
        print("ClawDeck backend process not found; pass --pid", file=sys.stderr)
        return 1
    t0, c0 = time.monotonic(), cpu_ticks(pid)
    ts, rs = [], []
    end = t0 + a.hours * 3600
    with open(a.csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["elapsed_s", "cpu_pct", "rss_mb"])
        last_t, last_c = t0, c0
        try:
            while time.monotonic() < end:
                time.sleep(a.interval)
                now, c, r = time.monotonic(), cpu_ticks(pid), rss_kb(pid) / 1024
                pct = 100.0 * (c - last_c) / TICK / (now - last_t)
                last_t, last_c = now, c
                ts.append((now - t0) / 3600)
                rs.append(r)
                w.writerow([round(now - t0), round(pct, 3), round(r, 2)])
                f.flush()
        except KeyboardInterrupt:
            pass
        except OSError:
            print("process exited", file=sys.stderr)
    el = time.monotonic() - t0
    total = 100.0 * (last_c - c0) / TICK / el if el else 0.0
    for line in verdict(total, max(rs or [0]), slope(ts, rs), el / 3600):
        print(line)
    print(f"log: {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
