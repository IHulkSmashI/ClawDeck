"""Is a Bazzite update waiting? (rpm-ostree, read-only.)

Bazzite's kernel, Intel graphics driver (xe), GPU firmware, Mesa and gamescope come from the OS image, not from ClawDeck:
the way users get Intel's newest Linux pieces is the Bazzite update (Steam → Settings → System → Update, or
`ujust update`). Setup shows a line when one is waiting, so nobody sits on an old kernel (maintainer, 2026-11-05).

  booted / staged   `rpm-ostree status --json`: the running version, and one already downloaded for the next boot
  available         `rpm-ostree upgrade --check`: asks the image server (network, a few seconds), at most every
                    CHECK_EVERY_S, in the background (main.py); nothing is downloaded or changed
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time

from . import sysfs

log = logging.getLogger("clawdeck.osupdate")

CHECK_EVERY_S = 6 * 3600
_cache: dict = {"at": 0.0, "available": None, "error": None}


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess | None:
    if not shutil.which(args[0]):
        return None
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False, env=sysfs.clean_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        log.info("%s: %s", " ".join(args), e)
        return None


def deployments() -> dict:
    """{'booted': version, 'staged': version or None} from rpm-ostree, or {} off rpm-ostree systems."""
    r = _run(["rpm-ostree", "status", "--json"], 20)
    if r is None or r.returncode != 0:
        return {}
    try:
        deps = json.loads(r.stdout).get("deployments", [])
    except ValueError:
        return {}
    booted = next((d for d in deps if d.get("booted")), None)
    staged = next((d for d in deps if d.get("staged")), None)
    return {"booted": (booted or {}).get("version"), "staged": (staged or {}).get("version")}


def parse_check(out: str) -> str | None:
    """The version `rpm-ostree upgrade --check` offers ("AvailableUpdate: ... Version: 45.20261104 (...)"), or None."""
    if "AvailableUpdate" not in out:
        return None
    m = re.search(r"^\s*Version:\s*(\S+)", out[out.index("AvailableUpdate"):], re.M)
    return m.group(1) if m else "a new version"


def refresh(force: bool = False) -> dict:
    """Ask the image server, at most every CHECK_EVERY_S (blocking: run it off the event loop)."""
    if not force and time.monotonic() - _cache["at"] < CHECK_EVERY_S and _cache["at"]:
        return dict(_cache)
    r = _run(["rpm-ostree", "upgrade", "--check"], 90)
    _cache["at"] = time.monotonic()
    if r is None:
        _cache.update(available=None, error="rpm-ostree not available")
    elif r.returncode in (0, 77):                      # 77: no update
        _cache.update(available=parse_check(r.stdout), error=None)
    else:
        _cache.update(available=None, error=(r.stderr or r.stdout).strip()[-200:] or f"exit {r.returncode}")
    return dict(_cache)


def status() -> dict:
    """For Setup / the checks: what runs, what is downloaded, what the server offers (last check)."""
    d = deployments()
    if not d:
        return {"applies": False}
    waiting = d.get("staged") or _cache.get("available")
    if d.get("staged"):
        line = f"Bazzite {d['staged']} is downloaded: restart the Claw to use it (running {d.get('booted') or '?'})"
    elif _cache.get("available"):
        line = (f"Bazzite {_cache['available']} is available (running {d.get('booted') or '?'}): Steam → Settings → "
                "System → Update, or `ujust update`. It brings the newest kernel, Intel graphics driver, Mesa and gamescope")
    elif _cache.get("at"):
        line = f"Bazzite {d.get('booted') or '?'} is up to date"
    else:
        line = f"Bazzite {d.get('booted') or '?'} (update check pending)"
    return {"applies": True, "booted": d.get("booted"), "staged": d.get("staged"), "available": _cache.get("available"),
            "waiting": bool(waiting), "line": line, "error": _cache.get("error")}
