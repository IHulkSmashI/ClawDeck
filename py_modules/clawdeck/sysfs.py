"""Safe access to sysfs: every write is remembered so it can be reverted, and a global dry-run mode prints instead of
writing. Nothing here persists across reboots; sysfs resets at boot, and so does this module's memory.

Rules (from the project's engineering requirements):
- every write goes through write(): it records the previous value the first time a path is touched;
- revert_all() restores every touched path to the value it had before ClawDeck touched it (the firmware default
  if nothing else changed it) - called on plugin unload and after any write error;
- dry_run=True (CLAWDECK_DRY_RUN=1 or set_dry_run) logs the write and does nothing;
- files are opened in 'with' blocks and closed immediately; no handles are kept open.
"""
from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("clawdeck.sysfs")

_lock = threading.Lock()
_originals: dict[str, str | None] = {}   # path -> value before our first write (None: could not read)
_dry_run = os.environ.get("CLAWDECK_DRY_RUN", "0") not in ("", "0")


def set_dry_run(on: bool) -> None:
    global _dry_run
    _dry_run = on


def dry_run() -> bool:
    return _dry_run


def exists(path: str) -> bool:
    return os.path.exists(path)


def read(path: str, default: str | None = None) -> str | None:
    """Trimmed contents of a sysfs file, or default when it cannot be read."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


def read_int(path: str, default: int | None = None) -> int | None:
    v = read(path)
    if v is None:
        return default
    try:
        return int(v.split()[0])
    except (ValueError, IndexError):
        return default


def write(path: str, value: str | int, persist: bool = False) -> bool:
    """Write value to path. Remembers the original value for revert. Returns True on success (or in dry-run).
    persist=True is for the one setting that must survive revert_all (the charge limit): it is not recorded, so
    closing a game or reverting power never undoes it."""
    value = str(value)
    with _lock:
        if persist:
            _originals.pop(path, None)
        elif path not in _originals:
            _originals[path] = read(path)
    if _dry_run:
        log.info("dry-run: would write %r to %s (was %r)", value, path, _originals.get(path))
        return True
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(value + "\n")
        return True
    except OSError as e:
        log.error("write %r to %s failed: %s", value, path, e)
        return False


def revert(path: str) -> bool:
    with _lock:
        orig = _originals.get(path)
        if _dry_run:
            # keep the record: the real write it would undo may have happened before dry-run was switched on
            log.info("dry-run: would revert %s to %r", path, orig)
            return True
        _originals.pop(path, None)
    if orig is None:
        return True
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(orig + "\n")
        return True
    except OSError as e:
        log.error("revert of %s to %r failed: %s", path, orig, e)
        return False


def revert_all() -> int:
    """Restore every path ClawDeck wrote. Returns the number of paths reverted."""
    with _lock:
        paths = list(_originals.keys())
    n = 0
    for p in paths:
        if revert(p):
            n += 1
    return n


def touched() -> dict[str, str | None]:
    with _lock:
        return dict(_originals)
