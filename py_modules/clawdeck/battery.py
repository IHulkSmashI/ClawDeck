"""Battery: only what the power profiles need, whether the Claw is on the charger (profiles and autotune's
ceiling differ on battery and plugged in). Charge limit, Wh and watt readouts left the plugin in the 2026-10-21
scope reset (Steam's own battery panel shows charge; docs/HARDWARE-FINDINGS.md §6 keeps the EX's units for later).
"""
from __future__ import annotations

import os

from . import hw, sysfs


class Battery:
    def __init__(self, hardware: hw.Hardware):
        self.p = hardware.paths

    def plugged(self) -> bool | None:
        for name in ("ACAD", "AC", "AC0", "ADP1", "ucsi-source-psy-USBC000:001"):
            v = sysfs.read_int(f"/sys/class/power_supply/{name}/online")
            if v is not None:
                return v == 1
        for d in os.listdir("/sys/class/power_supply") if os.path.isdir("/sys/class/power_supply") else []:
            t = sysfs.read(f"/sys/class/power_supply/{d}/type")
            if t in ("Mains", "USB", "USB_PD", "USB_C"):
                v = sysfs.read_int(f"/sys/class/power_supply/{d}/online")
                if v is not None:
                    return v == 1
        return None
