"""Proton picker: which Proton / GE-Proton builds are installed, and which one each game uses.

Choosing the tool is done by the frontend through Steam's own client API (SteamClient.Apps.SpecifyCompatTool), so
Steam stays the owner of config.vdf and nothing is overwritten while Steam runs. This module lists what is installed
(ProtonUp-Qt puts GE-Proton into compatibilitytools.d; Valve's Protons live in steamapps/common) and records the
choice in the game's ClawDeck profile so an exported profile carries it.
"""
from __future__ import annotations

import os
import re

from .injector import _steam_libraries


def _vdf_display_name(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            m = re.search(r'"display_name"\s+"([^"]+)"', f.read())
            return m.group(1) if m else None
    except OSError:
        return None


def _internal_name(path: str) -> str | None:
    """compatibilitytool.vdf: "compat_tools" { "<internal name>" { ... } } - Steam addresses the tool by that name."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except OSError:
        return None
    m = re.search(r'"compat_tools"\s*\{\s*"([^"]+)"', txt)
    return m.group(1) if m else None


def _version_key(name: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", name)[:3]) or (0,)


def installed(home: str) -> list[dict]:
    tools = []
    custom = os.path.join(home, ".local", "share", "Steam", "compatibilitytools.d")
    try:
        for d in sorted(os.listdir(custom)):
            vdf = os.path.join(custom, d, "compatibilitytool.vdf")
            if os.path.exists(vdf):
                tools.append({"name": _internal_name(vdf) or d, "display": _vdf_display_name(vdf) or d,
                              "kind": "ge" if "GE" in d else "custom", "path": os.path.join(custom, d)})
    except OSError:
        pass
    for lib in _steam_libraries(home):
        common = os.path.join(lib, "steamapps", "common")
        try:
            names = sorted(os.listdir(common))
        except OSError:
            continue
        for d in names:
            if d.startswith("Proton") and os.path.exists(os.path.join(common, d, "proton")):
                # Steam's internal names: proton_experimental, proton_9, proton_10 ...
                v = re.findall(r"\d+", d)
                internal = "proton_experimental" if "Experimental" in d else (f"proton_{v[0]}" if v else d)
                tools.append({"name": internal, "display": d, "kind": "valve", "path": os.path.join(common, d)})
    ge = sorted([t for t in tools if t["kind"] == "ge"], key=lambda t: _version_key(t["name"]), reverse=True)
    rest = [t for t in tools if t["kind"] != "ge"]
    return ge + rest
