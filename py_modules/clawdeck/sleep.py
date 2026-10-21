"""Sleep: is suspend/resume healthy on this machine, and does the chip reach its deepest idle state while asleep?

Read-only checks (nothing here changes the system):
  mem_sleep           /sys/power/mem_sleep: Intel handhelds sleep with s2idle (Modern Standby); "[s2idle]" selected
  suspend_stats       /sys/power/suspend_stats/{success,fail,last_failed_dev,last_failed_errno}
  s0ix residency      /sys/devices/system/cpu/cpuidle/low_power_idle_system_residency_us: time the SoC spent in its
                      deepest state (S0ix). If it does not grow across a suspend, the battery drains while asleep -
                      the usual cause of "my Claw was warm in the bag".
  wake sources        /proc/acpi/wakeup: devices allowed to wake the system (an enabled XHCI/USB controller is the
                      common cause of instant wake-ups)
  kernel cmdline      /proc/cmdline: known sleep parameters in use
The cycle test (tools/suspend-cycle.sh) runs N rtcwake cycles and logs these numbers per cycle; nothing is claimed
about Panther Lake sleep until it passes 100 cycles on a real Claw 8 EX.
Known fixes are listed as text with the exact command; ClawDeck does not change kernel arguments for the user.
"""
from __future__ import annotations

from . import sysfs

RESIDENCY = "/sys/devices/system/cpu/cpuidle/low_power_idle_system_residency_us"
STATS = "/sys/power/suspend_stats"
KNOWN_PARAMS = ("mem_sleep_default", "acpi.ec_no_wakeup", "xe.enable_dc", "i915.enable_dc", "pcie_aspm",
                "acpi_osi", "rtc_cmos.use_acpi_alarm")

FIXES = [
    {"id": "instant_wake", "symptom": "wakes up immediately after sleeping",
     "fix": "disable USB controller wake for this boot: echo XHCI | sudo tee /proc/acpi/wakeup (repeat to re-enable)"},
    {"id": "no_s0ix", "symptom": "battery drains while asleep (S0ix residency does not grow)",
     "fix": "update first (sudo rpm-ostree upgrade); then post tools/suspend-cycle.sh output in a GitHub issue"},
    {"id": "black_screen", "symptom": "black screen after resume",
     "fix": "press the power button once more; if it persists, rpm-ostree rollback to the last good image"},
]


def selected_mem_sleep(text: str | None) -> str | None:
    if not text:
        return None
    for tok in text.split():
        if tok.startswith("[") and tok.endswith("]"):
            return tok[1:-1]
    return None


def wake_sources(text: str | None) -> list[dict]:
    out = []
    for line in (text or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            out.append({"device": parts[0], "state": parts[2].lstrip("*"), "sysfs": parts[3] if len(parts) > 3 else None})
    return out


def status() -> dict:
    mem = sysfs.read("/sys/power/mem_sleep")
    stats = {k: sysfs.read(f"{STATS}/{k}") for k in ("success", "fail", "failed_suspend", "failed_resume",
                                                     "last_failed_dev", "last_failed_errno", "last_failed_step")}
    cmdline = sysfs.read("/proc/cmdline") or ""
    params = [p for p in cmdline.split() if p.split("=", 1)[0] in KNOWN_PARAMS]
    wakes = wake_sources(sysfs.read("/proc/acpi/wakeup"))
    return {
        "mem_sleep": selected_mem_sleep(mem),
        "mem_sleep_options": (mem or "").replace("[", "").replace("]", "").split(),
        "stats": stats,
        "s0ix_residency_us": sysfs.read_int(RESIDENCY),
        "s0ix_supported": sysfs.exists(RESIDENCY),
        "kernel_params": params,
        "wake_enabled": [w["device"] for w in wakes if w["state"] == "enabled"],
        "fixes": FIXES,
    }


def checks() -> list[dict]:
    s = status()
    fails = int(s["stats"].get("fail") or 0)
    out = [
        {"id": "sleep_mode", "title": "Sleep mode", "ok": s["mem_sleep"] in ("s2idle", "deep"),
         "detail": f"{s['mem_sleep'] or 'unknown'} (options: {' '.join(s['mem_sleep_options']) or '?'})"},
        {"id": "sleep_failures", "title": "Suspend failures since boot", "ok": fails == 0,
         "detail": f"{s['stats'].get('success') or 0} ok, {fails} failed"
                   + (f"; last: {s['stats'].get('last_failed_dev')} ({s['stats'].get('last_failed_step')})" if fails else "")},
        {"id": "s0ix", "title": "Deep idle (S0ix) counter", "ok": s["s0ix_supported"],
         "detail": f"{(s['s0ix_residency_us'] or 0) / 1e6:.0f} s in S0ix since boot" if s["s0ix_supported"]
                   else "not exposed by this kernel"},
    ]
    return out
