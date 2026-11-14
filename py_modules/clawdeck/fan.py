"""Fan presets and the battery charge limit, through the MSI platform driver (msi-wmi-platform) the Claw's kernel ships.

Fans: hwmon "msi_wmi_platform" has two fans (fan1 CPU, fan2 GPU), each with a six-point table pwmN_auto_point1..6
(_temp on the EC's fixed axis, _pwm on hwmon's 0-255 scale; the driver converts to the EC's 0-100 %) and
pwmN_enable: 1 = the fans follow the tables, 2 = MSI's automatic control (the driver writes back the tables it read at
boot, temperatures included). Every preset starts from MSI's own tables (enable=2), then writes its duties and, if it
has them, its own temperatures (PRESET_TEMPS), then enable=1; "Automatic" (enable=2) is the revert. Quiet and Cooling
are the Claw's known-good presets (GamingWidget.MsiFanControl.cs) on MSI's temperatures: duties in EC percent, the
1..39 band snapped to 40 (the fan's minimum-spin plateau: 1..39 all spin like 40), 0 = fan off at idle.

Charge limit: power_supply BAT1/charge_control_end_threshold (0 = no limit on the Claw 8 EX, accepts 80, back to 0;
tested 2026-11-04). Steam's own charge limit (steamos-manager) reports -1 on the Claw, so nothing else offers it.

Both are written with sysfs.write(persist=True): closing a game or reverting power never undoes them.
"""
from __future__ import annotations

import glob
import os

from . import userexec, sysfs

POINTS = 6
FLOOR = 40          # EC %: below this the fan spins at its minimum anyway
PRESETS: dict[str, list[int] | None] = {
    "auto": None,                               # MSI automatic (factory tables)
    "quiet": [0, 40, 40, 45, 60, 75],           # off at idle, gentle ramp
    "cooling": [40, 45, 58, 68, 75, 75],        # on at idle, earlier ramp
    "extreme": [0, 45, 70, 90, 100, 100],       # its own temperatures (PRESET_TEMPS): full speed from 82 °C
    "full": [100] * POINTS,                     # full speed
}
PRESET_TEMPS: dict[str, list[int]] = {
    "extreme": [38, 48, 60, 72, 82, 90],        # off under 38 °C, 100 % from 82 °C
}
LABELS = {"auto": "Automatic (MSI)", "quiet": "Quiet", "cooling": "Cooling", "extreme": "Extreme",
          "full": "Full speed", "custom": "Custom"}
CHARGE_PRESETS = {"off": None, "80": 80, "90": 90}


# OneXPlayer: the kernel's oxpec driver (hwmon "oxp_ec") has one fan, pwm1 (0-255) and pwm1_enable (1 = manual,
# 2 = the EC's automatic control), no tables. Only fixed speeds that can't overheat the chip are offered (no Quiet,
# no custom curve): Cooling 70 %, Full 100 %; Automatic hands it back to the EC (the revert).
OXP_PRESETS: dict[str, int | None] = {"auto": None, "cooling": 70, "full": 100}


def find_hwmon(root: str = "/sys/class/hwmon") -> str | None:
    for h in sorted(glob.glob(os.path.join(root, "hwmon*"))):
        if sysfs.read(os.path.join(h, "name")) in ("msi_wmi_platform", "oxp_ec") and os.path.exists(os.path.join(h, "pwm1_enable")):
            return h
    return None


def kind(h: str | None) -> str | None:
    """"msi" (six-point tables per fan), "oxp" (one fan, fixed speeds) or None."""
    if not h:
        return None
    return "oxp" if sysfs.read(os.path.join(h, "name")) == "oxp_ec" else "msi"


def presets_for(h: str | None) -> list[str]:
    return list(OXP_PRESETS) if kind(h) == "oxp" else [*PRESETS, "custom"]


def _apply_oxp(h: str, preset: str) -> dict:
    what = "Fan"
    if preset not in OXP_PRESETS:
        return {"ok": False, "what": what, "summary": f"{LABELS.get(preset, preset)} isn't offered on this fan", "error": f"preset {preset!r} not on oxp_ec"}
    duty = OXP_PRESETS[preset]
    sysfs.clear_error()
    if duty is None:
        ok = sysfs.write(os.path.join(h, "pwm1_enable"), 2, persist=True)
    else:
        ok = sysfs.write(os.path.join(h, "pwm1_enable"), 1, persist=True) and sysfs.write(os.path.join(h, "pwm1"), to_pwm(duty), persist=True)
    en = sysfs.read_int(os.path.join(h, "pwm1_enable"))
    ok = ok and en == (2 if duty is None else 1) and (duty is None or to_pct(sysfs.read_int(os.path.join(h, "pwm1"))) == duty)
    label = "Automatic" if duty is None else LABELS.get(preset, preset)
    return {"ok": ok, "what": what, "wanted": preset, "read": status(h), "unit": "",
            "summary": f"Fan: {label}" if ok else f"Fan: {label} didn't apply",
            "error": None if ok else (sysfs.last_error() or "the fan reads back different")}


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
    out = {"supported": True, "fans": [], "kind": kind(h), "presets": presets_for(h)}
    if kind(h) == "oxp":
        out["fans"].append({"fan": 1, "rpm": sysfs.read_int(os.path.join(h, "fan1_input")),
                            "enable": sysfs.read_int(os.path.join(h, "pwm1_enable")),
                            "duty": to_pct(sysfs.read_int(os.path.join(h, "pwm1")))})
        return out
    for n in fans(h):
        out["fans"].append({
            "fan": n,
            "rpm": sysfs.read_int(os.path.join(h, f"fan{n}_input")),
            "enable": sysfs.read_int(os.path.join(h, f"pwm{n}_enable")),
            "temps": [sysfs.read_int(os.path.join(h, f"pwm{n}_auto_point{i}_temp")) for i in range(1, POINTS + 1)],
            "duties": [to_pct(sysfs.read_int(os.path.join(h, f"pwm{n}_auto_point{i}_pwm"))) for i in range(1, POINTS + 1)],
        })
    return out


def _write_temps(h: str, n: int, temps: list[int]) -> bool:
    """Points that go up are written from the top down and points that go down from the bottom up, so the table
    stays in order after every single write."""
    cur = [sysfs.read_int(os.path.join(h, f"pwm{n}_auto_point{i}_temp")) for i in range(1, POINTS + 1)]
    up = [i for i in range(POINTS, 0, -1) if cur[i - 1] is None or temps[i - 1] > cur[i - 1]]
    down = [i for i in range(1, POINTS + 1) if cur[i - 1] is not None and temps[i - 1] < cur[i - 1]]
    ok = True
    for i in up + down:
        ok &= sysfs.write(os.path.join(h, f"pwm{n}_auto_point{i}_temp"), temps[i - 1], persist=True)
    return ok


def apply(h: str | None, preset: str, custom: list[int] | None = None) -> dict:
    """Writes the preset to both fans and reads it back. Returns a WriteResult-like dict."""
    what = "Fan"
    if not h:
        return {"ok": False, "what": what, "summary": "Fan control isn't available on this kernel", "error": "no msi_wmi_platform fan control"}
    if kind(h) == "oxp":
        return _apply_oxp(h, preset)
    if preset != "custom" and preset not in PRESETS:
        return {"ok": False, "what": what, "summary": f"Unknown fan preset {preset}", "error": f"unknown preset {preset!r}"}
    duties = [snap(x) for x in (custom or [])] if preset == "custom" else PRESETS[preset]
    if preset == "custom" and len(duties) != POINTS:
        return {"ok": False, "what": what, "summary": "Custom needs six points", "error": "custom needs six duties"}
    temps = PRESET_TEMPS.get(preset)
    sysfs.clear_error()
    ok = True
    # MSI's own tables first (temperatures included), so a preset never inherits another one's temperatures. Done for
    # both fans before writing either: enable=2 on one fan makes the driver put back the tables of both.
    for n in fans(h):
        ok &= sysfs.write(os.path.join(h, f"pwm{n}_enable"), 2, persist=True)
    if duties is not None:
        for n in fans(h):
            if temps:
                ok &= _write_temps(h, n, temps)
            for i, d in enumerate(duties, 1):
                ok &= sysfs.write(os.path.join(h, f"pwm{n}_auto_point{i}_pwm"), to_pwm(d), persist=True)
        for n in fans(h):
            ok &= sysfs.write(os.path.join(h, f"pwm{n}_enable"), 1, persist=True)
    st = status(h)
    want_enable = 2 if duties is None else 1
    readback = all(f["enable"] == want_enable for f in st["fans"])
    if duties is not None:
        readback = readback and all(f["duties"] == [to_pct(to_pwm(d)) for d in duties] for f in st["fans"])
        if temps:
            readback = readback and all(f["temps"] == temps for f in st["fans"])
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


POWERDEVIL_RC = ".config/powerdevilrc"


def kde_charge_limit(home: str, limit: int | None) -> bool:
    """Tell KDE's power manager the same limit. In Desktop Mode PowerDevil's charge-threshold helper writes its own
    setting (none = 100 %) to the battery at every start, which reset ClawDeck's 80 % (Claw 8 EX, 2026-11-04: the
    journal shows org.kde.powerdevil.chargethresholdhelper running at each switch to Desktop Mode)."""
    import configparser
    path = os.path.join(home, POWERDEVIL_RC)
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return False
    if limit is None:
        if cp.has_section("BatteryManagement"):
            cp.remove_option("BatteryManagement", "BatteryChargeStopThreshold")
            cp.remove_option("BatteryManagement", "BatteryChargeStartThreshold")
    else:
        if not cp.has_section("BatteryManagement"):
            cp.add_section("BatteryManagement")
        cp.set("BatteryManagement", "BatteryChargeStopThreshold", str(int(limit)))
        cp.set("BatteryManagement", "BatteryChargeStartThreshold", str(max(0, int(limit) - 5)))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            cp.write(f, space_around_delimiters=False)
        userexec.chown_tree(path, home)
        return True
    except OSError:
        return False
