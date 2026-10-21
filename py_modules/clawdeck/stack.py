"""Which system services own the Claw's controls on this install.

Bazzite changed in 2026: on handhelds it no longer ships Handheld Daemon (HHD). It enables InputPlumber (controller)
and PowerStation (power), and Steam's own performance slider sets the TDP through steamos-manager, which on the Claw
writes MSI's firmware attributes (bazzite: usr/libexec/hwsupport/steamos-manager-hardware, bazzite-hardware-setup).
Older installs, and users who add it, run HHD instead. Running HHD next to InputPlumber makes two daemons fight over
the controller, so ClawDeck must know which one is in charge before it suggests or changes anything.
"""
from __future__ import annotations

import os
import subprocess


def _active(units: list[str]) -> dict[str, bool]:
    try:
        r = subprocess.run(["systemctl", "is-active", *units], capture_output=True, text=True, timeout=5, check=False)
        states = r.stdout.split()
    except (OSError, subprocess.SubprocessError):
        states = []
    return {u: (states[i] == "active" if i < len(states) else False) for i, u in enumerate(units)}


def _hhd_unit_active() -> bool:
    try:
        r = subprocess.run(["systemctl", "list-units", "hhd*", "--state=active", "--no-legend", "--plain"],
                           capture_output=True, text=True, timeout=5, check=False)
        return bool(r.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def detect() -> dict:
    a = _active(["inputplumber.service", "powerstation.service"])
    hhd = os.path.exists("/run/hhd/api") or _hhd_unit_active()
    controller = "hhd" if hhd else ("inputplumber" if a["inputplumber.service"] else None)
    return {
        "hhd": hhd,
        "inputplumber": a["inputplumber.service"],
        "powerstation": a["powerstation.service"],
        "controller": controller,
        # who else may set the TDP: HHD, or Steam's slider through steamos-manager / PowerStation
        "tdp_owner": "hhd" if hhd else ("steam" if a["powerstation.service"] else None),
        "conflict": hhd and a["inputplumber.service"],
    }
