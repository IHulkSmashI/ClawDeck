"""Safe access to sysfs: every write is remembered so it can be reverted. Nothing here persists across reboots; sysfs resets at boot, and so does this module's memory.

Rules (from the project's engineering requirements):
- every write goes through write(): it records the previous value the first time a path is touched;
- revert_all() restores every touched path to the value it had before ClawDeck touched it (the firmware default
  if nothing else changed it) - called on plugin unload and after any write error;
- files are opened in 'with' blocks and closed immediately; no handles are kept open.
"""
from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("clawdeck.sysfs")

_lock = threading.Lock()
_originals: dict[str, str | None] = {}   # path -> value before our first write (None: could not read)
_last_error: str | None = None   # the most recent failed write or revert, for the UI ("Permission denied: ...")


def last_error() -> str | None:
    return _last_error


def clear_error() -> None:
    global _last_error
    _last_error = None


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
    """Write value to path. Remembers the original value for revert. Returns True on success.
    persist=True is for the one setting that must survive revert_all (the charge limit): it is not recorded, so
    closing a game or reverting power never undoes it."""
    value = str(value)
    with _lock:
        if persist:
            _originals.pop(path, None)
        elif path not in _originals:
            _originals[path] = read(path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(value + "\n")
        return True
    except OSError as e:
        global _last_error
        _last_error = f"writing {value} to {path}: {e.strerror or e}"
        log.error("write %r to %s failed: %s", value, path, e)
        return False


def revert(path: str) -> bool:
    with _lock:
        orig = _originals.get(path)
        _originals.pop(path, None)
    if orig is None:
        return True
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(orig + "\n")
        return True
    except OSError as e:
        global _last_error
        _last_error = f"restoring {orig} to {path}: {e.strerror or e}"
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


def clean_env() -> dict[str, str]:
    """The environment for system tools the plugin launches. Decky Loader's bundled Python (PyInstaller) exports
    LD_LIBRARY_PATH to its private libraries, and anything inheriting it loads the wrong OpenSSL: on the Claw
    rpm-ostree died with "libsolv.so.1: undefined symbol: EVP_MD_CTX_dup" before doing anything. Strip the loader
    and Python variables; keep the rest."""
    drop = ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONPATH", "PYTHONHOME", "_MEIPASS", "_MEIPASS2")
    env = {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("_PYI_")}
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    return env
