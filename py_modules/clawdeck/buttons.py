"""The two MSI face buttons on the Claw 8 EX AI+ (MS-1T91): why they do nothing on a stock Bazzite and the fix.

Measured on a Claw 8 EX (BIOS E1T91IMS.10D, Bazzite deck 44.20261012.1, InputPlumber 0.82.0, 2026-10-21) with
evtest on every input device while InputPlumber was stopped:
  right face button (MSI Center)   -> KEY_F15 from the "MSI WMI hotkeys" device (and scancode 0xb9 on the AT keyboard)
  left face button (Quick Settings) -> KEY_LEFTMETA + KEY_G (Win+G) from the "AT Translated Set 2 keyboard"
InputPlumber's device config for the board (50-msi_claw8_ex_ai_cg3em.yaml) uses capability map "claw2", whose
Guide needs the chord F15 + G and whose QuickAccess needs F16. Neither ever happens on this firmware, so both
buttons are dropped. (The AT keyboard's include list already passes KeyG and drops KeyLeftMeta, so the left button
arrives as a bare G.)

The fix is InputPlumber's own override mechanism: a copy of the device config in /etc/inputplumber/devices.d
pointing at a new capability map in /etc/inputplumber/capability_maps.d (both directories take precedence over
/usr/share), with Guide = KeyG (left button, Steam menu) and QuickAccess = KeyF15 (right button, Quick Access),
following the Steam Deck's left/right convention. A restart of InputPlumber picks it up. Removing the two files
restores the stock behaviour. The same two-line change is what we send upstream (ShadowBlip/InputPlumber).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess

from . import sysfs

log = logging.getLogger("clawdeck.buttons")

BOARD = "MS-1T91"
STOCK_DEVICE = "/usr/share/inputplumber/devices/50-msi_claw8_ex_ai_cg3em.yaml"
DEVICE_OVERRIDE = "/etc/inputplumber/devices.d/50-msi_claw8_ex_ai_cg3em.yaml"
MAP_OVERRIDE = "/etc/inputplumber/capability_maps.d/msiclaw_claw8ex.yaml"
MAP_ID = "claw8ex"
LEGACY_HWDB = ("/etc/udev/hwdb.d/61-clawdeck-msi-claw-8-ex.hwdb", "/etc/udev/hwdb.d/61-msi-claw-8-ex.hwdb")
MAP_TEXT = f"""# ClawDeck: MSI Claw 8 EX AI+ (MS-1T91). Its firmware sends Win+G for the left face button and F15 alone for
# the right one; the stock map (claw2) waits for the chord F15+G and for F16, so both buttons are dropped.
# Delete this file and {DEVICE_OVERRIDE}, then restart inputplumber, to go back to the stock config.
version: 1
kind: CapabilityMap
name: MSI Claw 8 EX
id: {MAP_ID}
mapping:
  - name: Guide
    source_events:
      - keyboard: KeyG
    target_event:
      gamepad:
        button: Guide
  - name: QuickAccess
    source_events:
      - keyboard: KeyF15
    target_event:
      gamepad:
        button: QuickAccess
  - name: M1
    source_events:
      - keyboard: KeyRightBrace
    target_event:
      gamepad:
        button: RightPaddle1
  - name: M2
    source_events:
      - keyboard: KeyLeftBrace
    target_event:
      gamepad:
        button: LeftPaddle1
filtered_events: []
"""


def _run(cmd: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False, env=sysfs.clean_env())


def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def stock_map_id(stock: str | None = None) -> str | None:
    """capability_map_id of the image's own config for the board (None if InputPlumber has no config for it)."""
    s = _read(stock or STOCK_DEVICE)
    if s is None:
        return None
    m = re.search(r"^capability_map_id:\s*(\S+)", s, re.M)
    return m.group(1) if m else None


def device_override_text(stock: str | None = None) -> str | None:
    """The stock device config with its map swapped for ours; None if the stock file is missing or has no map id."""
    s = _read(stock or STOCK_DEVICE)
    if s is None or not re.search(r"^capability_map_id:\s*\S+", s, re.M):
        return None
    s = re.sub(r"^capability_map_id:\s*\S+", f"capability_map_id: {MAP_ID}", s, count=1, flags=re.M)
    return "# ClawDeck override of the stock file (only capability_map_id differs); delete to restore it\n" + s


def status(board: str | None) -> dict:
    """What the health check shows. ok = InputPlumber is configured so both buttons produce an event."""
    applies = board == BOARD
    if not applies:
        return {"applies": False, "ok": True, "installed": False, "stock_map": None, "legacy_hwdb": []}
    installed = os.path.exists(DEVICE_OVERRIDE) and os.path.exists(MAP_OVERRIDE) \
        and f"capability_map_id: {MAP_ID}" in (_read(DEVICE_OVERRIDE) or "")
    stock = stock_map_id()
    # a future InputPlumber that ships its own fixed map (anything but claw2) makes the override unnecessary
    upstream_fixed = stock is not None and stock != "claw2"
    return {"applies": True, "ok": installed or upstream_fixed, "installed": installed, "stock_map": stock,
            "upstream_fixed": upstream_fixed, "legacy_hwdb": [p for p in LEGACY_HWDB if os.path.exists(p)]}


def apply() -> dict:
    """Write the two override files and restart InputPlumber."""
    dev = device_override_text()
    if dev is None:
        return {"ok": False, "error": f"{STOCK_DEVICE} not found: this InputPlumber has no config for the Claw 8 EX"}
    if sysfs.dry_run():
        return {"ok": True, "dry_run": True,
                "would": f"write {MAP_OVERRIDE} and {DEVICE_OVERRIDE}; systemctl restart inputplumber"}
    try:
        for path, text in ((MAP_OVERRIDE, MAP_TEXT), (DEVICE_OVERRIDE, dev)):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
    except OSError as e:
        return {"ok": False, "error": f"cannot write InputPlumber override: {e}"}
    try:
        r = _run(["systemctl", "restart", "inputplumber"], timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": f"systemctl restart inputplumber: {e}"}
    if r.returncode != 0:
        return {"ok": False, "error": f"inputplumber did not restart ({r.returncode})", "log": (r.stdout + r.stderr)[-2000:]}
    log.info("InputPlumber override for the Claw 8 EX face buttons installed")
    return {"ok": True, "next": "press the buttons in Game Mode: left = Steam menu, right = Quick Access"}


def remove() -> dict:
    """Undo apply(): delete the override (and any hwdb file an earlier ClawDeck wrote) and restart InputPlumber."""
    targets = [p for p in (DEVICE_OVERRIDE, MAP_OVERRIDE, *LEGACY_HWDB) if os.path.exists(p)]
    if not targets:
        return {"ok": True, "removed": []}
    if sysfs.dry_run():
        return {"ok": True, "dry_run": True, "would": f"rm {' '.join(targets)}; systemctl restart inputplumber"}
    try:
        for p in targets:
            os.remove(p)
        if any(p.endswith(".hwdb") for p in targets):
            _run(["systemd-hwdb", "update"], timeout=60)
        _run(["systemctl", "restart", "inputplumber"], timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e), "removed": targets}
    return {"ok": True, "removed": targets}
