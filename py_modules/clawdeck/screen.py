"""Screen brightness on OLED handhelds where Steam's slider does nothing (ONEXPLAYER 3).

On the ONEXPLAYER 3, gamescope drives the internal OLED in HDR10 (connector colorspace BT2020) even though Steam shows
no HDR toggle, and in that mode the panel ignores its backlight, so Steam's brightness slider (and the backlight file)
change nothing. What does change the picture is gamescope's SDR-on-HDR level: the X root-window property
GAMESCOPE_SDR_ON_HDR_CONTENT_BRIGHTNESS, a float's bit pattern in nits, which gamescope uses to draw SDR content
(Steam's UI, normal games) inside the HDR output. Setting it dims or brightens the picture with no display mode
change; removing it returns gamescope to its built-in default (the revert). It lives only as long as gamescope runs,
so a restart of Game Mode always starts from Steam's default.

What ClawDeck never does here: switch the output between HDR and SDR, or touch PSR / Panel Replay. On the ONEXPLAYER 3
both turn the screen off until a long power press or a second reboot (reported by OXP3 owners, 2026-10).

Adapted from handheld-control's gamescope.py (github.com/kupecjan1980-pixel/handheld-control, GPL-2.0-or-later; see
CREDITS.md): finding Steam's X display from the session's processes, and the property's float encoding.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import struct

from . import userexec

ATOM = "GAMESCOPE_SDR_ON_HDR_CONTENT_BRIGHTNESS"
NITS_MIN, NITS_MAX = 20, 600          # the slider's range; gamescope's own default sits in between


def nits_to_cardinal(nits: float) -> int:
    return struct.unpack("<I", struct.pack("<f", float(nits)))[0]


def cardinal_to_nits(value: int) -> float:
    return struct.unpack("<f", struct.pack("<I", value & 0xFFFFFFFF))[0]


def _environ(proc_dir: str) -> dict[str, str]:
    try:
        with open(os.path.join(proc_dir, "environ"), "rb") as f:
            raw = f.read().split(b"\0")
    except OSError:
        return {}
    out = {}
    for item in raw:
        k, sep, v = item.partition(b"=")
        if sep:
            out[k.decode(errors="replace")] = v.decode(errors="replace")
    return out


def x_env(uid: int, proc_root: str = "/proc") -> dict[str, str] | None:
    """DISPLAY (and XAUTHORITY) of the Xwayland Steam runs on in Game Mode: from Steam's own environment, else from
    any of the user's processes started by gamescope."""
    fallback = None
    for comm in sorted(glob.glob(os.path.join(proc_root, "[0-9]*", "comm"))):
        try:
            if os.stat(comm).st_uid != uid:
                continue
            with open(comm) as f:
                name = f.read().strip()
        except OSError:
            continue
        env = _environ(os.path.dirname(comm))
        if not env.get("DISPLAY") or "GAMESCOPE_WAYLAND_DISPLAY" not in env:
            continue
        x = {"DISPLAY": env["DISPLAY"], **({"XAUTHORITY": env["XAUTHORITY"]} if env.get("XAUTHORITY") else {})}
        if name == "steam":
            return x
        fallback = fallback or x
    return fallback


def _xprop(home: str, args: list[str]) -> dict:
    u = userexec.user_of(home)
    if u is None:
        return {"rc": -1, "out": "no desktop user"}
    x = x_env(u.pw_uid)
    if not x:
        return {"rc": -1, "out": "Game Mode isn't running (no Steam X display)"}
    if not shutil.which("xprop"):
        return {"rc": -1, "out": "xprop isn't installed"}
    return userexec.run(["xprop", "-root", *args], home, timeout=10, env=x)


def get(home: str) -> dict:
    r = _xprop(home, [ATOM])
    m = re.search(r"=\s*(\d+)", r["out"] or "")
    if r["rc"] != 0:
        return {"supported": False, "error": r["out"].strip()[-200:]}
    return {"supported": True, "nits": round(cardinal_to_nits(int(m.group(1)))) if m else None,
            "min": NITS_MIN, "max": NITS_MAX}


def set_nits(home: str, nits: int | None) -> dict:
    """nits None = gamescope's default (removes the property)."""
    if nits is None:
        r = _xprop(home, ["-remove", ATOM])
    else:
        nits = max(NITS_MIN, min(int(nits), NITS_MAX))
        r = _xprop(home, ["-f", ATOM, "32c", "-set", ATOM, str(nits_to_cardinal(nits))])
    ok = r["rc"] == 0
    return {"ok": ok, "what": "Brightness", "wanted": nits,
            "summary": ("Brightness: gamescope's default" if nits is None else f"Brightness {nits} nits") if ok else "Brightness didn't apply",
            "error": None if ok else r["out"].strip()[-200:]}
