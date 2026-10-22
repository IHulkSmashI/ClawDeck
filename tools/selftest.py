#!/usr/bin/env python3
"""ClawDeck self-test: prove each control on THIS machine, through the same backend code the plugin runs.

For every control: write it through the backend, read the value back from sysfs, measure the effect where one can
be measured (package power under an all-core load, GPU clock ceiling, charge threshold), then revert and check the
revert too. Prints a PASS/FAIL table; nothing is left changed. Run as root on the Claw:

    sudo python3 ~/homebrew/plugins/ClawDeck/tools/selftest.py          # ~40 s, fans may spin up
    sudo python3 .../selftest.py --quick                                  # skip the power measurements

Exit code is the number of failures, so it can gate a release.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py_modules"))

from clawdeck import buttons, hw, power, sysfs  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:34} {detail}")


def read_int(path: str | None) -> int | None:
    return sysfs.read_int(path) if path else None


def package_watts(energy: str, seconds: float = 6.0) -> float | None:
    """Average package power under an all-core busy loop, from RAPL's energy counter."""
    n = os.cpu_count() or 4
    procs = [subprocess.Popen(["sh", "-c", "while :; do :; done"]) for _ in range(n)]
    try:
        time.sleep(2.5)
        a = read_int(energy)
        time.sleep(seconds)
        b = read_int(energy)
    finally:
        for p in procs:
            p.kill()
        for p in procs:
            p.wait()
    if a is None or b is None or b < a:
        return None
    return (b - a) / seconds / 1e6


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="skip the power measurements")
    args = ap.parse_args()
    if os.geteuid() != 0:
        print("run as root (sudo): the controls are sysfs writes")
        return 1
    h = hw.detect()
    p = h.paths
    print(f"ClawDeck self-test on {h.model['name']} ({h.model_id}), kernel {h.kernel}\n")
    pw = power.Power(h)

    # ---- TDP ---------------------------------------------------------------------------------------------------
    if p.rapl_pl1:
        orig1, orig2 = read_int(p.rapl_pl1), read_int(p.rapl_pl2)
        target = max(h.model["tdp"]["min"], 12)
        ok = pw.set_tdp(target)
        back = read_int(p.rapl_pl1)
        check("TDP write (RAPL PL1)", ok and back == target * 1_000_000, f"asked {target} W, sysfs reads {back and back / 1e6:g} W")
        if not args.quick and p.rapl_energy:
            w = package_watts(p.rapl_energy)
            # the short-term limit (PL2) is what a 6 s load sees; allow it plus a little slack
            pl2 = (read_int(p.rapl_pl2) or target * 1_000_000) / 1e6
            check("TDP effect (power under load)", w is not None and w <= pl2 + 2, f"measured {w and round(w, 1)} W, limit {pl2:g} W")
        pw.revert_all()
        check("TDP revert", read_int(p.rapl_pl1) == orig1 and read_int(p.rapl_pl2) == orig2,
              f"back to {orig1 and orig1 / 1e6:g}/{orig2 and orig2 / 1e6:g} W")
        if not args.quick and p.rapl_energy:
            w = package_watts(p.rapl_energy)
            check("TDP effect after revert", w is not None and w > target + 3, f"measured {w and round(w, 1)} W (should exceed {target} W)")
    else:
        check("TDP write", False, "no RAPL package domain found")

    # ---- CPU ceiling -------------------------------------------------------------------------------------------
    if p.cpu_max_perf_pct:
        orig = read_int(p.cpu_max_perf_pct)
        ok = pw.set_cpu_max_pct(80)
        check("CPU ceiling write", ok and read_int(p.cpu_max_perf_pct) == 80, f"asked 80 %, reads {read_int(p.cpu_max_perf_pct)} %")
        pw.revert_all()
        check("CPU ceiling revert", read_int(p.cpu_max_perf_pct) == orig, f"back to {orig} %")
    else:
        check("CPU ceiling", False, "intel_pstate max_perf_pct not found")

    # ---- GPU window --------------------------------------------------------------------------------------------
    if p.gpu_min_freq and p.gpu_max_freq:
        omin, omax = read_int(p.gpu_min_freq), read_int(p.gpu_max_freq)
        rp0 = read_int(p.gpu_rp0_freq) or omax
        want_max = rp0 - 300
        ok = pw.set_gpu_window(None, want_max)
        check("GPU ceiling write", ok and read_int(p.gpu_max_freq) == want_max, f"asked {want_max} MHz, reads {read_int(p.gpu_max_freq)} MHz")
        pw.revert_all()
        check("GPU ceiling revert", read_int(p.gpu_min_freq) == omin and read_int(p.gpu_max_freq) == omax, f"back to {omin}-{omax} MHz")
    else:
        check("GPU window", False, "no xe/i915 frequency files")

    # ---- GPU efficiency mode (xe power_profile, findings §22.3) --------------------------------------------------
    if p.gpu_power_profile:
        orig, offered = pw.gpu_power_profile()
        for v in offered:
            try:
                ok = pw.set_gpu_power_profile(v)
            except ValueError:
                ok = False
            check(f"GPU efficiency mode {v}", ok and pw.gpu_power_profile()[0] == v, f"asked {v}, reads {pw.gpu_power_profile()[0]}")
        pw.revert_all()
        check("GPU efficiency mode revert", pw.gpu_power_profile()[0] == orig, f"back to {orig}")
    else:
        check("GPU efficiency mode", True, "no power_profile on this kernel (skipped)")

    # ---- charge limit ------------------------------------------------------------------------------------------
    if p.charge_limit:
        orig = read_int(p.charge_limit)
        ok = sysfs.write(p.charge_limit, 80, persist=True)       # the kernel's charge threshold (not in the UI)
        back = read_int(p.charge_limit)
        check("Charge limit write", ok and back == 80, f"asked 80 %, sysfs reads {back} %")
        # the charge limit is written with persist=True (no revert record), so restore it explicitly: the original
        # value first (0 = firmware default on the EX), 100 if the firmware refuses the original
        restored = sysfs.write(p.charge_limit, orig if orig is not None else 100, persist=True) or \
            sysfs.write(p.charge_limit, 100, persist=True)
        check("Charge limit restore", restored and read_int(p.charge_limit) in (orig, 100),
              f"back to {read_int(p.charge_limit)} % (was {orig})")
    else:
        check("Charge limit", False, "charge_control_end_threshold not found")

    # ---- every profile and the slider, as the panel applies them --------------------------------------------
    # each held 6 s: longer than thermald's rewrite interval, so a value that holds shows nothing else fights it
    if p.rapl_pl1 and p.rapl_pl2:
        lim = h.model["tdp"]
        def held(tag: str, watts: int) -> None:
            time.sleep(6)
            pl1, pl2 = (read_int(p.rapl_pl1) or 0) // 1_000_000, (read_int(p.rapl_pl2) or 0) // 1_000_000
            want2 = pw._sppt_for(watts, lim)
            check(f"{tag} holds", pl1 == watts and pl2 == want2, f"want {watts}/{want2} W, reads {pl1}/{pl2} W")
        for name in hw.profiles_for(h.model_key):
            res = pw.apply_profile(name)
            if res["failed"]:
                check(f"profile {name}", False, f"failed: {', '.join(res['failed'])}")
                continue
            held(f"profile {name}", res["applied"].get("tdp", -1))
        for watts in (h.model["tdp"]["min"], 14, 21):
            pw.set_tdp(watts)
            held(f"slider {watts} W", watts)
        check("thermald paused while held", not power.thermald_active() or not pw._thermald_paused,
              "paused" if pw._thermald_paused else "was not running")
        pw.revert_all()
        time.sleep(2)

    # ---- face buttons ------------------------------------------------------------------------------------------
    bs = buttons.status(h.model_id)
    if bs["applies"]:
        check("Face buttons mapped", bs["ok"], "InputPlumber override installed" if bs["installed"] else
              ("InputPlumber ships its own fix" if bs.get("upstream_fixed") else "not mapped: tap the fix in Setup"))

    # ---- nothing left behind -----------------------------------------------------------------------------------
    left = {k: v for k, v in sysfs.touched().items()}
    check("Nothing left changed", not left, "all values reverted" if not left else f"still tracked: {list(left)[:3]}")

    fails = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{len(results) - fails} passed, {fails} failed")
    return fails


if __name__ == "__main__":
    sys.exit(main())
