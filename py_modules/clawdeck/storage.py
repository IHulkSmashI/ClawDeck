"""Which kind of drive a game is installed on.

Games on an NTFS or exFAT drive (an SD card or partition shared with Windows) run through a slower driver on Linux
(ntfs-3g is a FUSE driver: every file read passes through a user-space process). That shows up as long loads,
stutter and texture pop-in. Proton also needs features those file systems don't have (symlinks, case handling).
Found on the Claw 8 EX, 2026-11-04: Black Myth: Wukong on an NTFS SD card through fuseblk.
"""
from __future__ import annotations

import os

SLOW = {"fuseblk": "NTFS (ntfs-3g)", "ntfs": "NTFS", "ntfs3": "NTFS", "exfat": "exFAT", "vfat": "FAT32"}


def _unescape(s: str) -> str:
    return s.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


def mount_of(path: str, mounts: str = "/proc/mounts") -> dict | None:
    try:
        with open(mounts, "r", encoding="utf-8") as f:
            rows = [line.split() for line in f]
    except OSError:
        return None
    path = os.path.realpath(path)
    best = None
    for r in rows:
        if len(r) < 3:
            continue
        mp = _unescape(r[1])
        if (path == mp or path.startswith(mp.rstrip("/") + "/")) and (best is None or len(mp) > len(best["mount"])):
            best = {"device": r[0], "mount": mp, "fstype": r[2]}
    return best


def game_drive(path: str | None, mounts: str = "/proc/mounts") -> dict | None:
    if not path:
        return None
    m = mount_of(path, mounts)
    if not m:
        return None
    slow = SLOW.get(m["fstype"])
    return {**m, "slow": bool(slow), "kind": slow or m["fstype"],
            "sd": m["device"].startswith("/dev/mmcblk")}
