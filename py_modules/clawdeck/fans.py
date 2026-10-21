"""Fans: curve presets on top of Handheld Daemon's MSI fan curve, plus fan speed and temperature readouts.

HHD's MSI driver (adjustor/drivers/msi/settings.yml) offers tdp.msi.fan.mode = disabled (firmware curve) | manual,
and in manual six points tdp.msi.fan.manual.st<N> (percent at 40/50/60/70/80/90 °C, titled "40C" ... "90C").
ClawDeck's presets are just values for those points; the point keys and their temperatures are read from HHD's
schema, so a firmware/HHD change in the number of points still works. "Auto" hands control back to the firmware.
Readouts are sysfs only (no writes): fan RPM from the msi_wmi_platform hwmon, package temperature from coretemp.
"""
from __future__ import annotations

import glob
import re

from . import hhd, hw, sysfs

KEY_MODE = "tdp.msi.fan.mode"
POINT_PREFIX = "tdp.msi.fan.manual."

# percent at a given temperature, linearly interpolated between these anchors
PRESETS: dict[str, list[tuple[int, int]] | None] = {
    "auto":  None,                                                       # firmware curve
    "quiet": [(40, 0), (50, 25), (60, 35), (70, 50), (80, 70), (90, 100)],
    "balanced": [(40, 0), (50, 40), (60, 49), (70, 58), (80, 67), (90, 75)],   # HHD's own default curve
    "cool":  [(40, 30), (50, 45), (60, 60), (70, 75), (80, 90), (90, 100)],
}


def curve_at(curve: list[tuple[int, int]], temp: float) -> int:
    if temp <= curve[0][0]:
        return curve[0][1]
    for (t0, p0), (t1, p1) in zip(curve, curve[1:]):
        if temp <= t1:
            return round(p0 + (p1 - p0) * (temp - t0) / (t1 - t0))
    return curve[-1][1]


def _temp_of(desc: dict) -> int | None:
    m = re.match(r"\s*(\d+)\s*°?C", str(desc.get("title") or ""))
    if m:
        return int(m.group(1))
    return None


def point_keys(schema: dict) -> list[dict]:
    """[{key, temp, min, max, step}] for HHD's manual curve points, sorted by temperature."""
    pts = []
    for d in hhd.describe(schema):
        if d["key"].startswith(POINT_PREFIX) and d["type"] == "int":
            t = _temp_of(d)
            if t is not None:
                pts.append({"key": d["key"], "temp": t, "min": d.get("min", 0), "max": d.get("max", 100),
                            "step": d.get("step", 1)})
    return sorted(pts, key=lambda p: p["temp"])


def preset_values(name: str, points: list[dict]) -> dict:
    if name not in PRESETS:
        raise ValueError(f"fan preset must be one of {list(PRESETS)}")
    curve = PRESETS[name]
    if curve is None:
        return {KEY_MODE: "disabled"}
    vals = {KEY_MODE: "manual"}
    for p in points:
        v = curve_at(curve, p["temp"])
        step = p["step"] or 1
        v = max(p["min"], min(p["max"], int(round(v / step) * step)))
        vals[p["key"]] = v
    return vals


class Fans:
    def __init__(self, hardware: hw.Hardware):
        self.p = hardware.paths

    def readout(self) -> dict:
        rpm = []
        if self.p.msi_fan_hwmon:
            for f in sorted(glob.glob(f"{self.p.msi_fan_hwmon}/fan*_input")):
                v = sysfs.read_int(f)
                if v is not None:
                    rpm.append(v)
        temp = None
        for h in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            if sysfs.read(f"{h}/name") == "coretemp":
                v = sysfs.read_int(f"{h}/temp1_input")
                if v is not None:
                    temp = v / 1000.0
                break
        return {"fan_rpm": rpm, "cpu_temp_c": temp}

    def status(self) -> dict:
        st = hhd.get_state()
        out = self.readout()
        out["available"] = st is not None and KEY_MODE in st
        out["mode"] = None if st is None else st.get(KEY_MODE)
        out["curve"] = None if st is None else {k[len(POINT_PREFIX):]: v for k, v in st.items() if k.startswith(POINT_PREFIX)}
        out["preset"] = self._which_preset(st) if st else None
        out["presets"] = list(PRESETS)
        return out

    def _which_preset(self, st: dict) -> str | None:
        if st.get(KEY_MODE) == "disabled":
            return "auto"
        schema = hhd.get_settings()
        if not schema:
            return None
        pts = point_keys(schema)
        for name in PRESETS:
            if name == "auto":
                continue
            want = preset_values(name, pts)
            if all(st.get(k) == v for k, v in want.items()):
                return name
        return "custom"

    def apply_preset(self, name: str) -> dict:
        schema = hhd.get_settings()
        if schema is None:
            return {"ok": False, "error": "Handheld Daemon is not running"}
        pts = point_keys(schema)
        if PRESETS.get(name) is not None and not pts:
            return {"ok": False, "error": "this Handheld Daemon version offers no manual fan curve for this device"}
        vals = preset_values(name, pts)
        st = hhd.set_values(vals)
        ok = st is not None and all(st.get(k) == v for k, v in vals.items())
        return {"ok": ok, "preset": name, "applied": vals}
