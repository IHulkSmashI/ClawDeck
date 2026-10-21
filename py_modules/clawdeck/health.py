"""Health check: what this machine has, what is missing, and the one-tap fix for each item.

Each check returns {"id", "title", "ok", "detail", "fix"}; fix is None or the name of a backend action the UI can
call (the Setup tab shows a button for it). Checks never change anything.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from . import hhd, hw, injector, sleep, sysfs, xess


def _run(cmd: list[str], timeout: float = 5.0) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return (r.stdout or r.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _version_of(cmd: list[str]) -> str | None:
    out = _run(cmd)
    return out.splitlines()[0] if out else None


def hhd_version() -> str | None:
    for py in ("/usr/bin/python3", "python3"):
        out = _run([py, "-c", "import importlib.metadata as m; print(m.version('hhd'))"], timeout=10)
        if out and out[0].isdigit():
            return out
    return _version_of(["hhd", "--version"])


def xess_state(home: str) -> dict:
    """The xess-xmx-linux package's state for this user (driver-status written by its session check)."""
    st = xess.state(home)
    return {"installed": st["installed"], "driver_status": st["status"]["line"], "state": st["status"]["state"],
            "version": st["version"]}


def checks(hardware: hw.Hardware, home: str) -> list[dict]:
    out: list[dict] = []
    p = hardware.paths
    out.append({"id": "model", "title": "Claw model recognised", "ok": hardware.known,
                "detail": f"{hardware.model['name']} ({hardware.product or 'no DMI name'}), {hardware.model['soc']}",
                "fix": None if hardware.known else "report_unknown_model"})
    mesa = hw.mesa_version()
    out.append({"id": "mesa", "title": "Intel Vulkan driver", "ok": mesa is not None,
                "detail": f"Mesa {mesa}, kernel {hardware.kernel}, {hardware.gpu_driver or 'no GPU driver'}" if mesa else
                          f"no libvulkan_intel.so found (kernel {hardware.kernel})", "fix": None})
    hhd_ok = hhd.available()
    hv = hhd_version() if hhd_ok else None
    out.append({"id": "hhd", "title": "Handheld Daemon running", "ok": hhd_ok,
                "detail": f"version {hv or '?'}; MSI buttons, TDP and fans come from here" if hhd_ok else
                          "not running or not installed: the MSI buttons and the TDP slider need it",
                "fix": None if hhd_ok else "enable_hhd"})
    if hhd_ok:
        ready = hhd.tdp_ready()
        out.append({"id": "hhd_tdp", "title": "TDP controls enabled in Handheld Daemon", "ok": ready,
                    "detail": "accepted" if ready else "open the Handheld Daemon overlay once and enable TDP controls",
                    "fix": None})
    out.append({"id": "msi_fw", "title": "MSI firmware controls (msi-wmi-platform)", "ok": p.msi_pl1 is not None,
                "detail": "TDP and fan curve attributes present" if p.msi_pl1 else
                          "kernel driver not loaded: TDP falls back to RAPL, no fan curves", "fix": None})
    out.append({"id": "gpu_clocks", "title": "GPU clock control", "ok": p.gpu_min_freq is not None,
                "detail": f"{p.gpu_min_freq}" if p.gpu_min_freq else "no xe/i915 frequency files", "fix": None})
    out.append({"id": "cpu_pct", "title": "CPU ceiling control (intel_pstate)", "ok": p.cpu_max_perf_pct is not None,
                "detail": p.cpu_max_perf_pct or "intel_pstate not active", "fix": None})
    out.append({"id": "charge", "title": "Battery charge limit", "ok": p.charge_limit is not None,
                "detail": p.charge_limit or "charge_control_end_threshold not exposed by this kernel", "fix": None})
    xs = xess_state(home)
    out.append({"id": "xess", "title": "XeSS XMX driver (xess-xmx-linux)", "ok": xs["installed"] and xs["state"] == "ok",
                "detail": (f"{xs['version'] or '?'}: " if xs["installed"] else "") + (xs["driver_status"] or ("not installed" if not xs["installed"] else "no session check yet: log out and in")),
                "fix": None if xs["installed"] else "install_xess"})
    ls = injector.lsfg_state(home)
    out.append({"id": "lsfg", "title": "lsfg-vk frame generation (optional)", "ok": ls["ready"],
                "detail": "layer and Lossless Scaling found" if ls["ready"] else
                          ("Vulkan layer missing" if not ls["layer"] else "Lossless Scaling (Steam) not installed: lsfg-vk needs its DLL"),
                "fix": None, "optional": True})
    out.append({"id": "mangohud", "title": "MangoHud (needed by autotune for frame times)", "ok": shutil.which("mangohud") is not None,
                "detail": "installed" if shutil.which("mangohud") else "not found", "fix": None})
    out.append({"id": "protonup", "title": "ProtonUp-Qt / GE-Proton", "ok": _ge_proton_present(home),
                "detail": "a GE-Proton is installed" if _ge_proton_present(home) else "no GE-Proton under compatibilitytools.d",
                "fix": None})
    for c in sleep.checks():
        c.setdefault("fix", None)
        out.append(c)
    return out


def _ge_proton_present(home: str) -> bool:
    d = os.path.join(home, ".local", "share", "Steam", "compatibilitytools.d")
    try:
        return any(n.startswith("GE-Proton") for n in os.listdir(d))
    except OSError:
        return False


def collect_logs(hardware: hw.Hardware, home: str, dest: str) -> str:
    """One-tap diagnostic bundle for a bug report: hardware, paths, health, versions. No personal data beyond the
    username in paths."""
    import json
    import time
    bundle = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hardware": hardware.to_dict(),
        "mesa": hw.mesa_version(),
        "health": checks(hardware, home),
        "hhd_state": hhd.get_state() if hhd.available() else None,
        "sysfs_touched": sysfs.touched(),
    }
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2, default=str)
    return dest
