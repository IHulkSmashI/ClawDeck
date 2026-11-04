"""Stop steamos-manager flooding the system log with split-lock lines.

On SteamOS, steamos-manager's root daemon reads the kernel's split-lock trace and passes each hit to Valve's crash
reporter (steamos-log-submitter). Bazzite ships the daemon but not the reporter: every hit fails, and both the hit
and the failure land in the journal. Steam's own network threads trip it constantly (Claw 8 EX, 2026-11-04: ~70,000
lines in the first 5 minutes, about 14 % of a CPU core). The journal is capped at 50 MB, so within minutes it
erases every earlier boot, including the log of a crash or a sleep that didn't wake.

The fix is one systemd drop-in that turns off only that module's logging (RUST_LOG=...,steamos_manager::sls=off);
everything else steamos-manager logs is kept. Removing the file undoes it. Not applied where the reporter exists.
"""
from __future__ import annotations

import os
import subprocess

DROPIN_DIR = "/etc/systemd/system/steamos-manager.service.d"
DROPIN = os.path.join(DROPIN_DIR, "50-clawdeck-quiet-split-lock.conf")
TEXT = ("# ClawDeck: steamos-manager forwards every split-lock hit to steamos-log-submitter, which this system doesn't\n"
        "# have; the failures flood the journal. Only the forwarding module is silenced. Remove this file to undo.\n"
        "[Service]\nEnvironment=RUST_LOG=info,steamos_manager::sls=off\n")
UNIT_DIRS = ("/usr/lib/systemd/system", "/etc/systemd/system", "/lib/systemd/system")


def _unit(name: str, dirs=UNIT_DIRS) -> bool:
    return any(os.path.exists(os.path.join(d, name)) for d in dirs)


def _run(cmd: list[str], timeout: float = 20) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def flood_rate() -> int | None:
    """split-lock lines steamos-manager logged in the last minute (None: journal not readable)."""
    r = _run(["journalctl", "-u", "steamos-manager", "--since", "-60s", "-o", "cat", "--no-pager"], timeout=10)
    if r is None or r.returncode != 0:
        return None
    return sum(1 for line in r.stdout.splitlines() if "sls::ftrace" in line)


def status(dropin: str = DROPIN, dirs=UNIT_DIRS) -> dict:
    applies = _unit("steamos-manager.service", dirs) and not _unit("steamos-log-submitter.service", dirs)
    return {"applies": applies, "installed": os.path.isfile(dropin)}


def _restart() -> str | None:
    for cmd in (["systemctl", "daemon-reload"], ["systemctl", "try-restart", "steamos-manager.service"]):
        r = _run(cmd)
        if r is None or r.returncode != 0:
            return f"{' '.join(cmd)}: {(r.stderr.strip() if r else 'did not run')}"
    return None


def apply(dropin: str = DROPIN, restart: bool = True) -> dict:
    try:
        os.makedirs(os.path.dirname(dropin), exist_ok=True)
        with open(dropin, "w", encoding="utf-8") as f:
            f.write(TEXT)
    except OSError as e:
        return {"ok": False, "error": f"writing {dropin}: {e}", **status(dropin)}
    err = _restart() if restart else None
    return {"ok": err is None, "error": err, **status(dropin)}


def remove(dropin: str = DROPIN, restart: bool = True) -> dict:
    try:
        if os.path.exists(dropin):
            os.remove(dropin)
    except OSError as e:
        return {"ok": False, "error": f"removing {dropin}: {e}", **status(dropin)}
    err = _restart() if restart else None
    return {"ok": err is None, "error": err, **status(dropin)}
