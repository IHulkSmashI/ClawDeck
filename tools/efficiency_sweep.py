#!/usr/bin/env python3
"""Panther Lake efficiency sweep (HARDWARE-FINDINGS §22.1): step the TDP, measure a game's benchmark at each step,
write one CSV row per step. The run itself is manual: you start the game's benchmark by hand at each step; this
script sets the TDP, tells you when, measures, and moves on. Run through tools/efficiency-sweep.sh (root).

At each TDP: an optional warm-up run (not measured), then one measured run of --seconds, sampled once a second:
  avg fps and the 1st percentile of the 1 s fps samples (MangoHud's CSV through ClawDeck's launch wrapper; frame
  generation off for the sweep, so these are rendered frames), package watts from the RAPL energy counter over the
  run, CPU busy, GPU busy (PowerStation), GPU clock, package temperature, and the PL1/PL2 the hardware read back.
With --power-saving-points the listed TDPs are measured a second time with the GPU efficiency mode (§22.3).

Everything ClawDeck changed is reverted at the end (or on Ctrl+C): the TDP, the GPU efficiency mode, thermald.
Output: the CSV (default docs/measurements/ptl-efficiency-<label>.csv) and, on screen, fps/W per step and the knee
(the first step where one more watt buys under 2 % more fps).
"""
from __future__ import annotations

import argparse
import csv
import os
import pwd
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py_modules"))

from clawdeck import autotune, hw, power, sysfs, thermal  # noqa: E402

COLUMNS = ["label", "tdp_w", "gpu_profile", "run_s", "samples", "avg_fps", "low_1pct_fps", "pkg_w", "fps_per_w",
           "cpu_pct", "gpu_busy_pct", "gpu_mhz", "pkg_temp_c", "pl1_w_read", "pl2_w_read"]
KNEE_GAIN = 0.02          # one more watt buying less than this much fps: the knee


def default_log_dir() -> str:
    """The desktop user's MangoHud folder (the launch wrapper's), also under sudo."""
    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"
    try:
        home = pwd.getpwnam(user).pw_dir
    except KeyError:
        home = os.path.expanduser("~")
    return os.path.join(home, "homebrew", "data", "ClawDeck", "mangohud")


def percentile(vals: list[float], pct: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def measure(h: hw.Hardware, smp: autotune.Sampler, seconds: int, sleep=time.sleep) -> dict:
    """One measured run: sample once a second for `seconds`; package watts from the energy counter over the run."""
    p = h.paths
    smp.frametime()                                     # drop whatever MangoHud logged before the run
    smp.cpu_pct()
    e0, t0 = (sysfs.read_int(p.rapl_energy) if p.rapl_energy else None), time.monotonic()
    fps, cpu, busy, mhz, temp = [], [], [], [], []
    for _ in range(int(seconds)):
        sleep(1.0)
        _, f = smp.frametime()
        if f:
            fps.append(f)
        c = smp.cpu_pct()
        if c is not None:
            cpu.append(c)
        b = smp.gpu_busy()
        if b is not None:
            busy.append(b)
        if p.gpu_cur_freq:
            m = sysfs.read_int(p.gpu_cur_freq)
            if m:
                mhz.append(m)
        t = thermal.read_c(p.pkg_temp)
        if t is not None:
            temp.append(t)
    e1, t1 = (sysfs.read_int(p.rapl_energy) if p.rapl_energy else None), time.monotonic()
    watts = (e1 - e0) / 1e6 / (t1 - t0) if e0 is not None and e1 is not None and e1 >= e0 and t1 > t0 else None
    avg = statistics.fmean(fps) if fps else None
    mean = lambda v: round(statistics.fmean(v), 1) if v else None
    return {"run_s": round(t1 - t0, 1), "samples": len(fps), "avg_fps": None if avg is None else round(avg, 1),
            "low_1pct_fps": None if not fps else round(percentile(fps, 1), 1),
            "pkg_w": None if watts is None else round(watts, 2),
            "fps_per_w": None if not (avg and watts) else round(avg / watts, 2),
            "cpu_pct": mean(cpu), "gpu_busy_pct": mean(busy), "gpu_mhz": mean(mhz), "pkg_temp_c": mean(temp)}


def knee(rows: list[dict]) -> dict | None:
    """The first step after which one more watt buys less than KNEE_GAIN more fps (rows of one GPU profile)."""
    pts = sorted((r for r in rows if r.get("avg_fps")), key=lambda r: r["tdp_w"])
    for a, b in zip(pts, pts[1:]):
        dw = b["tdp_w"] - a["tdp_w"]
        if dw > 0 and (b["avg_fps"] - a["avg_fps"]) / a["avg_fps"] / dw < KNEE_GAIN:
            return a
    return pts[-1] if pts else None


def sweep(h: hw.Hardware, pw: power.Power, smp: autotune.Sampler, tdps: list[int], seconds: int, label: str,
          out: str, warmup: bool = True, auto: bool = False, settle: int = 20, power_saving_points: list[int] | None = None,
          ask=input, say=print, sleep=time.sleep) -> list[dict]:
    plan = [(w, "base") for w in tdps]
    if power_saving_points and pw.p.gpu_power_profile and "power_saving" in pw.gpu_power_profile()[1]:
        plan += [(w, "power_saving") for w in power_saving_points]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    rows: list[dict] = []
    with open(out, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=COLUMNS)
        wr.writeheader()
        for i, (w, gp) in enumerate(plan, 1):
            if pw.p.gpu_power_profile and gp in pw.gpu_power_profile()[1]:
                pw.set_gpu_power_profile(gp)
            if not pw.set_tdp(w):
                say(f"[{i}/{len(plan)}] {w} W: the TDP could not be set ({sysfs.last_error()}); skipped")
                continue
            st = pw.status()
            say(f"\n[{i}/{len(plan)}] TDP {w} W (reads PL1 {st['rapl']['pl1_w']} W / PL2 {st['rapl']['pl2_w']} W), GPU {gp}")
            if auto:
                sleep(settle)                           # a looping benchmark: let the power settle, then measure
            else:
                if warmup:
                    ask("  warm-up: start the benchmark, press Enter when it has finished ")
                ask("  measured run: start the benchmark, press Enter the moment it begins ")
            r = {"label": label, "tdp_w": w, "gpu_profile": gp, **measure(h, smp, seconds, sleep=sleep),
                 "pl1_w_read": st["rapl"]["pl1_w"], "pl2_w_read": st["rapl"]["pl2_w"]}
            wr.writerow(r)
            f.flush()
            rows.append(r)
            say(f"  {r['avg_fps']} fps (1 % low {r['low_1pct_fps']}), {r['pkg_w']} W, {r['fps_per_w']} fps/W, "
                f"{r['pkg_temp_c']} °C, {r['samples']} samples")
            if not r["samples"]:
                say("  no MangoHud rows: is the game started through ClawDeck's launch wrapper?")
    base = [r for r in rows if r["gpu_profile"] == "base"]
    k = knee(base)
    if k:
        say(f"\nknee: {k['tdp_w']} W ({k['avg_fps']} fps) - one more watt buys under {KNEE_GAIN:.0%} more fps from there")
    say(f"CSV: {out}")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tdps", default="8 10 12 14 17 20 25 30 35", help="TDP steps in W (default: %(default)s)")
    ap.add_argument("--seconds", type=int, default=60, help="length of each measured run (default 60)")
    ap.add_argument("--label", default="wukong", help="the game / benchmark, for the CSV name and rows")
    ap.add_argument("--out", default=None, help="CSV path (default docs/measurements/ptl-efficiency-<label>.csv)")
    ap.add_argument("--no-warmup", action="store_true", help="skip the unmeasured warm-up run at each step")
    ap.add_argument("--auto", action="store_true", help="no prompts: for a benchmark that loops by itself")
    ap.add_argument("--settle", type=int, default=20, help="--auto: seconds at each TDP before measuring")
    ap.add_argument("--power-saving-points", default="10 12 17",
                    help="TDPs measured again with GPU efficiency mode (§22.3); '' to skip (default: %(default)s)")
    ap.add_argument("--log-dir", default=None, help="MangoHud CSV folder (default: the launch wrapper's)")
    a = ap.parse_args()
    if os.geteuid() != 0:
        print("run as root (sudo): the TDP is a sysfs write")
        return 1
    h = hw.detect()
    if not h.paths.rapl_pl1:
        print("no RAPL package domain: nothing to sweep")
        return 1
    lo, hi = h.model["tdp"]["min"], h.model["tdp"]["plugged_max"]
    tdps = sorted({max(lo, min(int(x), hi)) for x in a.tdps.split()})
    ps = [max(lo, min(int(x), hi)) for x in a.power_saving_points.split()]
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    out = a.out or os.path.join(root, "docs", "measurements", f"ptl-efficiency-{a.label}.csv")
    log_dir = a.log_dir or default_log_dir()
    print(f"ClawDeck efficiency sweep on {h.model['name']}: {tdps} W, {a.seconds} s runs, MangoHud logs in {log_dir}")
    print("Benchmark settings for the whole sweep: fixed preset, XeSS on XMX, frame generation OFF, frame limit off.")
    pw = power.Power(h)
    smp = autotune.Sampler(h, log_dir)
    try:
        sweep(h, pw, smp, tdps, a.seconds, a.label, out, warmup=not a.no_warmup, auto=a.auto, settle=a.settle,
              power_saving_points=ps)
    except KeyboardInterrupt:
        print("\nstopped; the rows so far are in the CSV")
    finally:
        n = pw.revert_all()
        print(f"reverted {n} value(s); thermald {'running' if power.thermald_active() else 'not running'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
