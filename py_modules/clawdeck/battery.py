"""Battery: charge limit (50/60/70/80/90/100 %) and readouts.

How: the kernel exposes /sys/class/power_supply/BAT*/charge_control_end_threshold when the platform driver
supports it (msi-ec / msi-wmi-platform on the Claw). Handheld Daemon's battery driver offers 70-95 % through its
own setting and re-applies it; for 50 and 60 % ClawDeck sets HHD's option to 'disabled' and writes the threshold
itself. 100 % = no limit. The value is written at every plugin start (sysfs resets at boot) and is the one setting
ClawDeck persists on purpose, because that is what a charge limit is for. A threshold of 0 means "not set" (the
EX's firmware reports 0 until something writes it) and is shown as 100 %.

Units differ between models. Some batteries report energy_* (uWh) and power_now (uW); the Claw 8 EX reports only
charge_* (uAh) and voltage_now (uV), and its current_now is not credible (-65 A at idle). So energy is
charge x voltage when energy_* is missing, and power is power_now, else current_now when the product is plausible,
else the change of charge_now between two readouts.
"""
from __future__ import annotations

import logging
import os
import time

from . import hhd, hw, sysfs

log = logging.getLogger("clawdeck.battery")

CHOICES = [50, 60, 70, 80, 90, 100]
MAX_PLAUSIBLE_W = 100.0     # a handheld never draws more; anything above is a unit or firmware error


class Battery:
    def __init__(self, hardware: hw.Hardware):
        self.p = hardware.paths
        self._last_charge: tuple[float, int] | None = None   # (monotonic s, charge_now uAh) for derived power

    def supported(self) -> bool:
        return self.p.charge_limit is not None

    def status(self) -> dict:
        b = self.p.battery
        if not b:
            return {"present": False}
        return {
            "present": True,
            "status": sysfs.read(f"{b}/status"),
            "capacity_pct": sysfs.read_int(f"{b}/capacity"),
            "power_w": self._power_w(b),
            "energy_wh": self._wh(b, "now"),
            "energy_full_wh": self._wh(b, "full"),
            "health_pct": self._health(b),
            "plugged": self.plugged(),
            "charge_limit_supported": self.supported(),
            "charge_limit": self.charge_limit(),
        }

    def charge_limit(self) -> int | None:
        if not self.p.charge_limit:
            return None
        v = sysfs.read_int(self.p.charge_limit)
        return 100 if v == 0 else v      # 0 = never set = charges to full

    def _wh(self, b: str, which: str) -> float | None:
        e = sysfs.read_int(f"{b}/energy_{which}")          # uWh
        if e is not None:
            return round(e / 1e6, 2)
        q = sysfs.read_int(f"{b}/charge_{which}")          # uAh
        v = sysfs.read_int(f"{b}/voltage_now") or sysfs.read_int(f"{b}/voltage_min_design")   # uV
        if q is not None and v:
            return round(q * v / 1e12, 2)
        return None

    def _power_w(self, b: str) -> float | None:
        """Battery power in watts, positive either way (charging or discharging)."""
        pw = sysfs.read_int(f"{b}/power_now")                # uW
        if pw is not None:
            return round(abs(pw) / 1e6, 2)
        v = sysfs.read_int(f"{b}/voltage_now")
        i = sysfs.read_int(f"{b}/current_now")               # uA
        if v and i is not None and abs(i) * v / 1e12 <= MAX_PLAUSIBLE_W:
            return round(abs(i) * v / 1e12, 2)
        q = sysfs.read_int(f"{b}/charge_now")
        if not v or q is None:
            return None
        now = time.monotonic()
        prev = self._last_charge
        if not prev or now - prev[0] > 600:      # first readout, or the panel was closed for long: start over
            self._last_charge = (now, q)
            return None
        if q == prev[1] or now - prev[0] < 5:    # the gauge moves in steps: keep the older point until it does
            return None
        self._last_charge = (now, q)
        w = abs(q - prev[1]) * 3600 / (now - prev[0]) * v / 1e12   # uAh/s -> uA, x uV -> W
        return round(w, 2) if w <= MAX_PLAUSIBLE_W else None

    def _health(self, b: str) -> float | None:
        full = sysfs.read_int(f"{b}/energy_full") or sysfs.read_int(f"{b}/charge_full")
        design = sysfs.read_int(f"{b}/energy_full_design") or sysfs.read_int(f"{b}/charge_full_design")
        if full and design:
            return min(100.0, round(100.0 * full / design, 1))   # a new pack can report over 100 %
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
