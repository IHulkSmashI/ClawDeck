"""The two MSI face buttons on the Claw 8 EX AI+ (MS-1T91): why they do nothing on a stock Bazzite and the fix.

The Claw's face buttons are keys on its internal "AT Translated Set 2 keyboard", scancodes 0xb9 (right, MSI
Center / Steam menu) and 0xba (left, Quick Access). systemd's hwdb (/usr/lib/udev/hwdb.d/60-keyboard.hwdb) turns
them into F15 / F16, and InputPlumber's Claw profiles turn F15 / F16 into the gamepad's Guide and Quick Access
events. The hwdb entry lists the earlier Claws (MS-1T41, 1T42, 1T52, 1T8K) but not the 8 EX (MS-1T91): on it the
scancodes stay unmapped, InputPlumber never sees a key, and the buttons are dead in Game Mode. Found on a Claw 8 EX
with Bazzite deck 44.20261012.1 (InputPlumber 0.82.0), 2026-10-21.

The fix is one hwdb file for that board, applied the way systemd documents it (systemd-hwdb update, udevadm
trigger on the keyboard) and a restart of InputPlumber so it re-reads the device. Removing the file and running
systemd-hwdb update again restores the stock behaviour; a future systemd/Bazzite that ships the entry makes the
check pass without the file, which the health check reports so the local copy can be removed.
"""
from __future__ import annotations

import glob
import logging
import os
import subprocess

from . import sysfs

log = logging.getLogger("clawdeck.buttons")

BOARD = "MS-1T91"
HWDB_PATH = "/etc/udev/hwdb.d/61-clawdeck-msi-claw-8-ex.hwdb"
KEYBOARD_NAME = "AT Translated Set 2 keyboard"
SCANCODES = {"b9": "f15", "ba": "f16"}  # right face button, left face button
HWDB_TEXT = (
    "# MSI Claw 8 EX AI+ (MS-1T91): the face buttons use the same scancodes as the other Claws.\n"
    "# Written by ClawDeck (Setup tab); delete this file and run `systemd-hwdb update` to undo.\n"
    # the DMI modalias has pn/pvr/rvn between the vendor and the board, hence the second *
    "evdev:name:AT Translated Set 2 keyboard:dmi:*:svnMicro-StarInternationalCo.,Ltd.:*:rnMS-1T91:*\n"
    + "".join(f" KEYBOARD_KEY_{sc}={key}\n" for sc, key in SCANCODES.items())
)


def _run(cmd: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def keyboard_event(proc_devices: str = "/proc/bus/input/devices") -> str | None:
    """/dev/input/eventN of the internal keyboard the face buttons belong to."""
    try:
        with open(proc_devices, "r", encoding="utf-8") as f:
            block = []
            for line in list(f) + ["\n"]:
                if line.strip():
                    block.append(line)
                    continue
                if any(KEYBOARD_NAME in b for b in block if b.startswith("N:")):
                    for b in block:
                        if b.startswith("H:"):
                            for h in b.split("=", 1)[1].split():
                                if h.startswith("event"):
                                    return f"/dev/input/{h}"
                block = []
    except OSError:
        pass
    return None


def mapped(event: str | None = None) -> dict[str, str]:
    """The hwdb key mappings udev currently applies to the keyboard, for our scancodes only."""
    event = event or keyboard_event()
    if not event:
        return {}
    try:
        r = _run(["udevadm", "info", event], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return {}
    out = {}
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("E: KEYBOARD_KEY_"):
            k, _, v = line[len("E: KEYBOARD_KEY_"):].partition("=")
            if k.lower() in SCANCODES:
                out[k.lower()] = v
    return out


def status(board: str | None) -> dict:
    """What the health check shows. ok = the buttons reach InputPlumber (mapping present), whatever provides it."""
    applies = board == BOARD
    m = mapped() if applies else {}
    ok = all(m.get(sc) == key for sc, key in SCANCODES.items())
    return {"applies": applies, "ok": not applies or ok, "mapped": m, "local_file": os.path.exists(HWDB_PATH),
            "event": keyboard_event() if applies else None}


def apply() -> dict:
    """Write the hwdb entry, rebuild the hwdb, re-trigger the keyboard and restart InputPlumber."""
    event = keyboard_event()
    steps = [["systemd-hwdb", "update"]] + ([["udevadm", "trigger", "--action=change", event]] if event else []) \
        + [["systemctl", "restart", "inputplumber"]]
    if sysfs.dry_run():
        return {"ok": True, "dry_run": True, "would": f"write {HWDB_PATH}; " + "; ".join(" ".join(s) for s in steps)}
    try:
        os.makedirs(os.path.dirname(HWDB_PATH), exist_ok=True)
        with open(HWDB_PATH, "w", encoding="utf-8") as f:
            f.write(HWDB_TEXT)
    except OSError as e:
        return {"ok": False, "error": f"cannot write {HWDB_PATH}: {e}"}
    logs = []
    for s in steps:
        try:
            r = _run(s, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            return {"ok": False, "error": f"{' '.join(s)}: {e}", "log": "\n".join(logs)}
        logs.append(f"$ {' '.join(s)}\n{(r.stdout + r.stderr).strip()}")
        if r.returncode != 0 and s[0] != "systemctl":  # no InputPlumber (HHD users) is not a failure
            return {"ok": False, "error": f"{' '.join(s)} failed ({r.returncode})", "log": "\n".join(logs)[-2000:]}
    m = mapped(event)
    ok = all(m.get(sc) == key for sc, key in SCANCODES.items())
    log.info("face-button hwdb fix applied, mapping now %s", m)
    return {"ok": ok, "mapped": m, "log": "\n".join(logs)[-2000:],
            "next": "press the buttons in Game Mode: right = Steam menu, left = Quick Access" if ok else
                    "mapping did not take; see the log and report it with a hardware survey"}


def remove() -> dict:
    """Undo apply(): delete the file and rebuild the hwdb."""
    if not os.path.exists(HWDB_PATH):
        return {"ok": True, "removed": False}
    if sysfs.dry_run():
        return {"ok": True, "dry_run": True, "would": f"rm {HWDB_PATH}; systemd-hwdb update"}
    try:
        os.remove(HWDB_PATH)
        _run(["systemd-hwdb", "update"], timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "removed": True}


def stale_local_copies() -> list[str]:
    """Other hwdb files for this board (e.g. one made by hand), so the health check can mention them."""
    return [f for f in glob.glob("/etc/udev/hwdb.d/*.hwdb") if f != HWDB_PATH and _mentions_board(f)]


def _mentions_board(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return BOARD in f.read()
    except OSError:
        return False
