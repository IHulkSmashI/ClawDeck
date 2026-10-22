"""Read-only D-Bus readouts from ShadowBlip's services, which current Bazzite runs instead of Handheld Daemon.

PowerStation (org.shadowblip.PowerStation): GpuBusyPercent on /org/shadowblip/Performance/GPU/card0, interface
org.shadowblip.GPU.Card (PowerStation src/performance/gpu/dbus/gpu.rs). The xe driver has no gpu_busy_percent file,
so this is the only GPU load figure on the Claw 8 EX.


Through `busctl`, as PowerControl does: no D-Bus library is bundled with Decky's Python. Nothing here writes.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger("clawdeck.shadowblip")

POWERSTATION = "org.shadowblip.PowerStation"
GPU_PATH = "/org/shadowblip/Performance/GPU/card0"
GPU_IFACE = "org.shadowblip.GPU.Card"


def _busctl(*args: str) -> str | None:
    if not shutil.which("busctl"):
        return None
    try:
        r = subprocess.run(["busctl", "--system", *args], capture_output=True, text=True, timeout=1, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def parse_property(out: str | None):
    """busctl get-property output ('y 15', 'd 15.000000', 's "MSI Claw"', 'b true') -> Python value."""
    if not out:
        return None
    parts = out.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    kind, val = parts
    try:
        if kind in ("y", "n", "q", "i", "u", "x", "t"):
            return int(val)
        if kind == "d":
            return float(val)
        if kind == "b":
            return val == "true"
        if kind in ("s", "o"):
            return val[1:-1] if len(val) >= 2 and val[0] == val[-1] == '"' else val
    except ValueError:
        return None
    return None


def get(service: str, path: str, iface: str, prop: str):
    return parse_property(_busctl("get-property", service, path, iface, prop))


def gpu_busy_percent() -> int | None:
    v = get(POWERSTATION, GPU_PATH, GPU_IFACE, "GpuBusyPercent")
    return v if isinstance(v, int) and 0 <= v <= 100 else None
