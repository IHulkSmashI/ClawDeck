"""Which Linux the handheld runs: Bazzite, SteamOS, CachyOS or another. ClawDeck works on all of them (the plugin
only needs Decky Loader, Steam's Game Mode and Proton); this is for the words it shows and for the few features that
depend on the system: Bazzite's update check and the boot-time VRR method (rpm-ostree)."""
from __future__ import annotations

import functools
import shutil

from . import sysfs


@functools.lru_cache(maxsize=1)
def info() -> dict:
    txt = sysfs.read("/etc/os-release") or ""
    vals = {k: v.strip('"') for k, _, v in (line.partition("=") for line in txt.splitlines()) if k}
    ids = {vals.get("ID", "").lower(), *vals.get("ID_LIKE", "").lower().split()}
    kind = ("bazzite" if "bazzite" in ids or "bazzite" in vals.get("NAME", "").lower() else
            "steamos" if "steamos" in ids else
            "cachyos" if "cachyos" in ids else
            "other")
    name = vals.get("NAME") or {"bazzite": "Bazzite", "steamos": "SteamOS", "cachyos": "CachyOS"}.get(kind, "Linux")
    return {"kind": kind, "name": name, "atomic": shutil.which("rpm-ostree") is not None,
            "like": vals.get("ID_LIKE", "").lower()}


def name() -> str:
    return info()["name"]


def package_hint(pkg: str) -> str:
    """How to add a missing program on this system (shown when an unpacker is missing)."""
    k = info()["kind"]
    if info()["atomic"]:
        return f"sudo rpm-ostree install {pkg}"
    if k == "steamos":
        return "Discover → install it as a Flatpak, or ask in r/SteamOS (SteamOS's system is read-only)"
    if k == "cachyos":
        return f"sudo pacman -S {pkg}"
    return f"install {pkg} with your package manager"
