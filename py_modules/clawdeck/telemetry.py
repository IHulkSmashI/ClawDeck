"""Power readings for the performance overlay (Steam's, MangoHud).

The overlay runs as the desktop user and reads the chip's energy counters (RAPL `energy_uj`) to show CPU watts. Since
2020 Linux makes those counters root-only (mode 0400): sampling them fast enough can leak information about what
other programs compute (the "PLATYPUS" side channel). So on a stock Claw the overlay shows 0.0 W.

"Watts in the overlay" (Fixes) makes the package counters readable by everyone (0444) while it is on, reapplied by
ClawDeck at every start because sysfs resets at boot, and puts 0400 back when switched off. On the Claw 8 EX the
package domain covers CPU and graphics together (the xe driver has no separate GPU power sensor on this chip).

The battery's own reading is not touched here: the Claw 8 EX's firmware reports current_now about 100x too high
(battery_watts() corrects it for ClawDeck's own read-back).
"""
from __future__ import annotations

import glob
import logging
import os

log = logging.getLogger("clawdeck.telemetry")

POWERCAP = "/sys/class/powercap"
# package domains only: core/uncore/dram sub-domains are not what the overlay shows
DOMAINS = ("intel-rapl:0", "intel-rapl-mmio:0")
OPEN, CLOSED = 0o444, 0o400


def counters(root: str = POWERCAP) -> list[str]:
    return [p for d in DOMAINS for p in [os.path.join(root, d, "energy_uj")] if os.path.exists(p)]


def status(root: str = POWERCAP) -> dict:
    files = []
    for p in counters(root):
        try:
            mode = os.stat(p).st_mode & 0o777
        except OSError:
            continue
        files.append({"path": p, "mode": oct(mode), "readable": bool(mode & 0o004)})
    return {"available": bool(files), "on": bool(files) and all(f["readable"] for f in files), "files": files}


TMPFILES = "/etc/tmpfiles.d/clawdeck-overlay-power.conf"


def boot_rule(on: bool, path: str = TMPFILES, root: str = POWERCAP) -> str | None:
    """The performance overlay (mangoapp) checks the counter once, when Game Mode starts: opened later, it keeps
    showing no watts until the next session. A systemd-tmpfiles rule opens the counters at boot, before Game Mode;
    removing the file undoes it. Returns an error text or None."""
    try:
        if on:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("# ClawDeck: watts in the performance overlay (Fixes). Remove this file to undo.\n")
                for d in DOMAINS:
                    f.write(f"z {root}/{d}/energy_uj 0444 - - -\n")
        elif os.path.exists(path):
            os.remove(path)
    except OSError as e:
        return f"{path}: {e}"
    return None


def apply(on: bool, root: str = POWERCAP) -> dict:
    """Open (0444) or close (0400) the package energy counters. Returns what each one reads afterwards."""
    errors = []
    if root == POWERCAP:
        e = boot_rule(on)
        if e:
            errors.append(e)
    for p in counters(root):
        try:
            os.chmod(p, OPEN if on else CLOSED)
        except OSError as e:
            errors.append(f"{p}: {e.strerror or e}")
    st = status(root)
    if not st["available"]:
        return {"ok": False, "error": "no RAPL package energy counter on this kernel", **st}
    ok = not errors and st["on"] == on
    if errors:
        log.warning("overlay power counters: %s", "; ".join(errors))
    return {"ok": ok, "error": "; ".join(errors) or (None if ok else "the counters didn't change"), **st}


def battery_watts(bat: str | None = None, scale_fix: bool = True) -> float | None:
    """Battery power in W (negative = discharging), from power_now or current_now x voltage_now. The Claw 8 EX EC
    reports current_now in units 100x too large (−64.6 A at ~10 W): readings above 200 W are divided by 100."""
    bats = [bat] if bat else sorted(glob.glob("/sys/class/power_supply/BAT*"))
    for b in bats:
        def rd(name: str) -> int | None:
            try:
                with open(os.path.join(b, name), "r", encoding="utf-8") as f:
                    return int(f.read().strip())
            except (OSError, ValueError):
                return None
        p = rd("power_now")
        if p is not None:
            w = p / 1e6
        else:
            i, v = rd("current_now"), rd("voltage_now")
            if i is None or v is None:
                continue
            w = i * v / 1e12
        if scale_fix and abs(w) > 200:
            w /= 100
        st = None
        try:
            with open(os.path.join(b, "status"), "r", encoding="utf-8") as f:
                st = f.read().strip()
        except OSError:
            pass
        if st == "Discharging" and w > 0:
            w = -w
        return round(w, 1)
    return None


def external_display(drm: str = "/sys/class/drm") -> dict:
    """Is a display other than the Claw's own screen connected (DP/HDMI, a dock or USB-C monitor)? Then gamescope
    shows the picture there, and its VRR is the user's choice: ClawDeck's "VRR only in games" stands aside."""
    found = []
    for st in sorted(glob.glob(os.path.join(drm, "card*-*", "status"))):
        conn = os.path.basename(os.path.dirname(st)).split("-", 1)[1]
        if conn.startswith(("eDP", "LVDS", "DSI")) or conn.startswith("Writeback"):
            continue
        try:
            with open(st, "r", encoding="utf-8") as f:
                if f.read().strip() == "connected":
                    found.append(conn)
        except OSError:
            continue
    return {"connected": bool(found), "connectors": found}
