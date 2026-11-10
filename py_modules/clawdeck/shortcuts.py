"""Non-Steam games: Windows games added to Steam as shortcuts and run through Proton (#161, from maltyjava's report
and patch, tested by them on a Claw 8 AI+ with CachyOS: Stellar Blade, Palworld, Sifu, The Ascent, Onimusha, Slime
Rancher 2, Elden Ring, Sekiro, The Blood of Dawnwalker).

Steam keeps them in userdata/<user>/config/shortcuts.vdf (binary KeyValues), not in appmanifest_*.acf, so the Game
list never saw them. Three things differ from a Steam game:
  - the id: the shortcut's 32-bit appid (unsigned), the one Steam names compatdata/<appid> and SteamAppId after;
  - the folder: a shortcut usually starts in a build folder (Binaries/Win64, x64), from where an Unreal game's
    Engine/Plugins upscalers can't be seen, so the game's top folder is found by climbing out of build folders and
    out of an Unreal project folder that sits next to Engine;
  - the prefix: STEAM_COMPAT_DATA_PATH=... in the launch options puts it elsewhere than compatdata/<appid>.
Only shortcuts that start a Windows .exe are kept (Moonlight, emulators and Linux tools are left out); the support
check in xess.py still decides which ones the Game list shows.
"""
from __future__ import annotations

import base64
import glob
import os
import re
import struct

USERDATA = ".local/share/Steam/userdata"
# folders a game's exe sits in below its top folder
BUILD_DIRS = {"binaries", "win64", "wingdk", "win32", "x64", "x86", "x86_64", "bin", "bin64", "bin32", "binwin64",
              "retail", "shipping"}
ICON_MAX = 512 * 1024
ICON_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".ico": "image/x-icon",
              ".bmp": "image/bmp", ".webp": "image/webp"}

_cache: dict[str, tuple[float, list[dict]]] = {}


def parse_binary_vdf(data: bytes) -> dict:
    """Valve's binary KeyValues (shortcuts.vdf): 0x00 map, 0x01 string, 0x02 int32, 0x08 end of map."""
    pos = 0

    def cstr() -> str:
        nonlocal pos
        end = data.index(b"\x00", pos)
        s = data[pos:end].decode("utf-8", "replace")
        pos = end + 1
        return s

    def node() -> dict:
        nonlocal pos
        out: dict = {}
        while pos < len(data):
            t = data[pos]
            pos += 1
            if t == 0x08:
                return out
            key = cstr()
            if t == 0x00:
                out[key] = node()
            elif t == 0x01:
                out[key] = cstr()
            elif t == 0x02:
                out[key] = struct.unpack_from("<i", data, pos)[0]
                pos += 4
            elif t == 0x07:                                # uint64 (not in shortcuts.vdf today)
                out[key] = struct.unpack_from("<Q", data, pos)[0]
                pos += 8
            else:
                raise ValueError(f"unknown KeyValues type 0x{t:02x} at {pos - 1}")
        return out

    return node()


def _get(entry: dict, name: str):
    """Steam writes the keys in different cases over the years (AppName / appname, Exe / exe)."""
    for k, v in entry.items():
        if k.lower() == name.lower():
            return v
    return None


def top_folder(exe: str) -> str:
    """The game's top folder from its exe: out of build folders, then out of an Unreal project folder next to
    Engine (<Game>/<Project>/Binaries/Win64/x.exe -> <Game>)."""
    d = os.path.dirname(exe)
    while os.path.basename(d).lower() in BUILD_DIRS and os.path.dirname(d) != d:
        d = os.path.dirname(d)
    parent = os.path.dirname(d)
    if parent != d and os.path.isdir(os.path.join(parent, "Engine")) and os.path.basename(d) != "Engine":
        d = parent
    return d


def compat_path(launch_options: str) -> str | None:
    """STEAM_COMPAT_DATA_PATH=<path> in a shortcut's launch options (quoted or not)."""
    m = re.search(r"""STEAM_COMPAT_DATA_PATH=(?:"([^"]+)"|'([^']+)'|(\S+))""", launch_options or "")
    return next((g for g in m.groups() if g), None) if m else None


def shortcuts(home: str) -> list[dict]:
    """Every non-Steam shortcut that starts a Windows .exe: appid, name, exe, dir (the game's top folder),
    compat (its prefix folder, the one holding pfx/), icon (file path or None)."""
    out, seen = [], set()
    for path in sorted(glob.glob(os.path.join(home, USERDATA, "*", "config", "shortcuts.vdf"))):
        try:
            mtime = os.path.getmtime(path)
            hit = _cache.get(path)
            if hit and hit[0] == mtime:
                items = hit[1]
            else:
                with open(path, "rb") as f:
                    root = parse_binary_vdf(f.read())
                items = []
                for entry in (_get(root, "shortcuts") or {}).values():
                    if not isinstance(entry, dict):
                        continue
                    exe = str(_get(entry, "Exe") or "").strip().strip('"')
                    appid = _get(entry, "appid")
                    if not exe.lower().endswith(".exe") or not isinstance(appid, int) or not os.path.isfile(exe):
                        continue
                    aid = str(appid & 0xFFFFFFFF)
                    compat = compat_path(str(_get(entry, "LaunchOptions") or ""))
                    icon = str(_get(entry, "icon") or "").strip().strip('"') or None
                    items.append({"appid": aid, "name": str(_get(entry, "AppName") or os.path.basename(exe)),
                                  "exe": exe, "dir": top_folder(exe),
                                  "compat": compat or os.path.join(home, ".local/share/Steam/steamapps/compatdata", aid),
                                  "icon": icon if icon and os.path.isfile(icon) else None, "shortcut": True})
                _cache[path] = (mtime, items)
        except (OSError, ValueError, struct.error, IndexError):
            continue
        for it in items:
            if it["appid"] not in seen:
                seen.add(it["appid"])
                out.append(it)
    return out


def find(home: str, appid: str) -> dict | None:
    return next((s for s in shortcuts(home) if s["appid"] == str(appid)), None)


def icon_url(home: str, appid: str) -> str | None:
    """The icon file a shortcut names, as a data URL (at most ICON_MAX bytes): Steam has no icon URL for a shortcut
    until something has shown it once, so the Game list's closed dropdown stayed blank."""
    s = find(home, appid)
    p = s and s.get("icon")
    mime = ICON_TYPES.get(os.path.splitext(p or "")[1].lower())
    try:
        if not p or not mime or os.path.getsize(p) > ICON_MAX:
            return None
        with open(p, "rb") as f:
            return f"data:{mime};base64," + base64.b64encode(f.read()).decode()
    except OSError:
        return None
