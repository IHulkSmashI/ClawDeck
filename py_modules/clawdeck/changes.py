"""Notice when the system under ClawDeck changed, before it breaks something: a Bazzite update brings a new kernel
or Mesa, the controller service changes, the XeSS driver falls back after a Mesa update, Decky updates.

At every plugin start a snapshot is compared with the last one (settings dir, system-snapshot.json). Anything that
changed is reported with the health checks that now fail, so the UI can say "Bazzite updated: kernel A -> B, all
checks pass" or point at the one thing that needs attention. Read-only; costs one health check per start.
"""
from __future__ import annotations

import json
import os
import platform

from . import hw, sysfs, xess

LABELS = {"kernel": "Kernel", "mesa": "Mesa", "os": "Bazzite", "controller": "Controller service",
          "xess": "XeSS driver", "hhd": "Handheld Daemon", "decky": "Decky Loader"}


def os_version() -> str | None:
    txt = sysfs.read("/etc/os-release") or ""
    vals = dict(line.split("=", 1) for line in txt.splitlines() if "=" in line)
    v = (vals.get("OSTREE_VERSION") or vals.get("VERSION_ID") or vals.get("VERSION") or "").strip('"')
    name = vals.get("NAME", "").strip('"')
    return f"{name} {v}".strip() or None


def snapshot(home: str, stack: dict, decky_version: str | None) -> dict:
    xs = xess.state(home)
    return {"kernel": platform.release(), "mesa": hw.mesa_version(), "os": os_version(),
            "controller": stack.get("controller"), "xess": f"{xs['version']} {xs['status']['state']}" if xs["installed"] else None,
            "decky": decky_version}


def diff(old: dict, new: dict) -> list[dict]:
    return [{"what": LABELS.get(k, k), "key": k, "from": old.get(k), "to": new.get(k)}
            for k in new if k in old and old.get(k) != new.get(k)]


def check(path: str, current: dict) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = None
    changed = diff(old, current) if old else []
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    os.replace(path + ".tmp", path)
    return {"first_run": old is None, "changed": changed}
