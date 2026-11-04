"""Fan presets and the battery charge limit, through the MSI platform driver (msi-wmi-platform) the Claw's kernel ships.

Fans: hwmon "msi_wmi_platform" has two fans (fan1 CPU, fan2 GPU), each with a six-point table pwmN_auto_point1..6
(_temp on the EC's fixed axis, _pwm on hwmon's 0-255 scale; the driver converts to the EC's 0-100 %) and
pwmN_enable: 1 = the fans follow the tables, 2 = MSI's automatic control (the driver puts the factory tables back).
ClawDeck writes only the duties, never the temperatures, then enable=1; "Automatic" (enable=2) is the revert. The
presets are ClawTweaks' for the Claw (GamingWidget.MsiFanControl.cs): duties in EC percent, the 1..39 band snapped
to 40 (the fan's minimum-spin plateau: 1..39 all spin like 40), 0 = fan off at idle.

Charge limit: power_supply BAT1/charge_control_end_threshold (0 = no limit on the Claw 8 EX, accepts 80, back to 0;
tested 2026-11-04). Steam's own charge limit (steamos-manager) reports -1 on the Claw, so nothing else offers it.

Both are written with sysfs.write(persist=True): closing a game or reverting power never undoes them.
"""
from __future__ import annotations

import glob
import os

from . import sysfs

POINTS = 6
FLOOR = 40          # EC %: below this the fan spins at its minimum anyway
PRESETS: dict[str, list[int] | None] = {
    "auto": None,                               # MSI automatic (factory tables)
    "quiet": [0, 40, 40, 45, 60, 75],           # off at idle, gentle ramp
    "cooling": [40, 45, 58, 68, 75, 75],        # on at idle, earlier ramp
    "full": [100] * POINTS,                     # full speed
}
LABELS = {"auto": "Automatic (MSI)", "quiet": "Quiet", "cooling": "Cooling", "full": "Full speed", "custom": "Custom"}
CHARGE_PRESETS = {"off": None, "80": 80, "90": 90}


def find_hwmon(root: str = "/sys/class/hwmon") -> str | None:
    for h in sorted(glob.glob(os.path.join(root, "hwmon*"))):
        if sysfs.read(os.path.join(h, "name")) == "msi_wmi_platform" and os.path.exists(os.path.join(h, "pwm1_enable")):
            return h
    return None


def fans(h: str) -> list[int]:
    return [n for n in (1, 2) if os.path.exists(os.path.join(h, f"pwm{n}_enable"))]


def snap(d: int | float) -> int:
    """{0} ∪ [40..100]: below half the floor → 0 (off), the dead zone → 40."""
    d = int(round(float(d)))
    if d <= 0:
        return 0
    if d < FLOOR:
        return 0 if d < FLOOR / 2 else FLOOR
    return min(d, 100)


def to_pwm(d: int) -> int:
    return max(0, min(255, round(d * 255 / 100)))


def to_pct(pwm: int | None) -> int | None:
    return None if pwm is None else round(pwm * 100 / 255)


def status(h: str | None) -> dict:
    if not h:
        return {"supported": False}
    out = {"supported": True, "fans": []}
    for n in fans(h):
        out["fans"].append({
            "fan": n,
            "rpm": sysfs.read_int(os.path.join(h, f"fan{n}_input")),
            "enable": sysfs.read_int(os.path.join(h, f"pwm{n}_enable")),
            "temps": [sysfs.read_int(os.path.join(h, f"pwm{n}_auto_point{i}_temp")) for i in range(1, POINTS + 1)],
            "duties": [to_pct(sysfs.read_int(os.path.join(h, f"pwm{n}_auto_point{i}_pwm"))) for i in range(1, POINTS + 1)],
        })
    return out


def apply(h: str | None, preset: str, custom: list[int] | None = None) -> dict:
    """Writes the preset to both fans and reads it back. Returns a WriteResult-like dict."""
    what = "Fan"
    if not h:
        return {"ok": False, "what": what, "summary": "Fan control isn't available on this kernel", "error": "no msi_wmi_platform fan control"}
    if preset != "custom" and preset not in PRESETS:
        return {"ok": False, "what": what, "summary": f"Unknown fan preset {preset}", "error": f"unknown preset {preset!r}"}
    duties = [snap(x) for x in (custom or [])] if preset == "custom" else PRESETS[preset]
    if preset == "custom" and len(duties) != POINTS:
        return {"ok": False, "what": what, "summary": "Custom needs six points", "error": "custom needs six duties"}
    sysfs.clear_error()
    ok = True
    for n in fans(h):
        if duties is None:
            ok &= sysfs.write(os.path.join(h, f"pwm{n}_enable"), 2, persist=True)
            continue
        for i, d in enumerate(duties, 1):
            ok &= sysfs.write(os.path.join(h, f"pwm{n}_auto_point{i}_pwm"), to_pwm(d), persist=True)
        ok &= sysfs.write(os.path.join(h, f"pwm{n}_enable"), 1, persist=True)
    st = status(h)
    want_enable = 2 if duties is None else 1
    readback = all(f["enable"] == want_enable for f in st["fans"])
    if duties is not None:
        readback = readback and all(f["duties"] == [to_pct(to_pwm(d)) for d in duties] for f in st["fans"])
    ok = ok and readback
    label = LABELS.get(preset, preset)
    rpm = " / ".join(f"{f['rpm']}" for f in st["fans"] if f["rpm"] is not None)
    return {"ok": ok, "what": what, "wanted": preset, "read": st, "unit": "",
            "summary": f"Fan: {label}" + (f" ({rpm} rpm now)" if rpm and ok else "") if ok else f"Fan: {label} didn't apply",
            "error": None if ok else (sysfs.last_error() or "the fan tables read back different")}


def charge_status(path: str | None) -> dict:
    if not path:
        return {"supported": False}
    return {"supported": True, "limit": sysfs.read_int(path)}


def set_charge(path: str | None, limit: int | None, original: int | None) -> dict:
    """limit None = off (the value the file had before ClawDeck, 0 = no limit on the Claw)."""
    what = "Charge limit"
    if not path:
        return {"ok": False, "what": what, "summary": "This battery has no charge limit control", "error": "no charge_control_end_threshold"}
    want = (original if original is not None else 0) if limit is None else max(50, min(int(limit), 100))
    sysfs.clear_error()
    ok = sysfs.write(path, want, persist=True)
    got = sysfs.read_int(path)
    ok = ok and got == want
    label = "off" if limit is None else f"{want} %"
    return {"ok": ok, "what": what, "wanted": want, "read": got, "unit": "%",
            "summary": f"Charge limit {label}" if ok else f"Charge limit {label} didn't apply (reads {got})",
            "error": None if ok else (sysfs.last_error() or f"reads {got}")}
