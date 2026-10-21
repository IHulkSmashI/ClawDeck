"""Battery: charge limit (50/60/70/80/90/100 %) and readouts.

How: the kernel exposes /sys/class/power_supply/BAT*/charge_control_end_threshold when the platform driver
supports it (msi-ec / msi-wmi-platform on the Claw). Handheld Daemon's battery driver offers 70-95 % through its
own setting and re-applies it; for 50 and 60 % ClawDeck sets HHD's option to 'disabled' and writes the threshold
itself. 100 % = no limit. The value is written at every plugin start (sysfs resets at boot) and is the one setting
ClawDeck persists on purpose, because that is what a charge limit is for.
"""
from __future__ import annotations

import logging
import os

from . import hhd, hw, sysfs

log = logging.getLogger("clawdeck.battery")

CHOICES = [50, 60, 70, 80, 90, 100]


class Battery:
    def __init__(self, hardware: hw.Hardware):
        self.p = hardware.paths

    def supported(self) -> bool:
        return self.p.charge_limit is not None

    def status(self) -> dict:
        b = self.p.battery
        if not b:
            return {"present": False}
        energy_now = sysfs.read_int(f"{b}/energy_now") or sysfs.read_int(f"{b}/charge_now")
        energy_full = sysfs.read_int(f"{b}/energy_full") or sysfs.read_int(f"{b}/charge_full")
        power_now = sysfs.read_int(f"{b}/power_now") or sysfs.read_int(f"{b}/current_now")
        return {
            "present": True,
            "status": sysfs.read(f"{b}/status"),
            "capacity_pct": sysfs.read_int(f"{b}/capacity"),
            "power_w": (power_now or 0) / 1e6 if power_now is not None else None,
            "energy_wh": (energy_now or 0) / 1e6 if energy_now is not None else None,
            "energy_full_wh": (energy_full or 0) / 1e6 if energy_full is not None else None,
            "health_pct": self._health(b),
            "plugged": self.plugged(),
            "charge_limit_supported": self.supported(),
            "charge_limit": sysfs.read_int(self.p.charge_limit) if self.p.charge_limit else None,
        }

    def _health(self, b: str) -> float | None:
        full = sysfs.read_int(f"{b}/energy_full") or sysfs.read_int(f"{b}/charge_full")
        design = sysfs.read_int(f"{b}/energy_full_design") or sysfs.read_int(f"{b}/charge_full_design")
        if full and design:
            return round(100.0 * full / design, 1)
        return None

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

    def set_charge_limit(self, percent: int) -> bool:
        if percent not in CHOICES:
            raise ValueError(f"charge limit must be one of {CHOICES}")
        if not self.supported():
            log.warning("this kernel does not expose charge_control_end_threshold for the battery")
            return False
        via_hhd = hhd.available() and percent in hhd.CHARGE_OPTIONS
        if hhd.available():
            # keep HHD and the kernel in agreement: HHD owns 70-95, ClawDeck owns the rest
            hhd.set_charge_limit(percent if via_hhd else None)
        if via_hhd:
            return True
        # 100 = no limit; the kernel default is 100
        return sysfs.write(self.p.charge_limit, percent)
