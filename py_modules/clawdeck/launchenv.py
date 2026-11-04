"""Per-game launch environment: the one file tools/clawdeck-launch.sh reads (<runtime>/launch/<appid>.env).

Every per-game choice that has to be in the game's environment lands here, composed from the game's launch settings
(profiles.py, key "launch"). The xess-xmx-linux variables are read by the package's Proton hook and shim
(its docs/launch-options.md); the OptiScaler ones by Decky Framegen's fgmod wrapper, which writes them into the
game's OptiScaler.ini at each launch (its README, "Configuring OptiScaler via Environment Variables").

  xmx          bool | unset         per-game scope (xmxscope.py, the default since 0.2): unset = automatic (the XMX
                                    driver only for a game that ships XeSS or OptiScaler), on/off = CLAWDECK_XMX=1/0.
                                    With the package still session-wide: off -> the stock Vulkan drivers (stock_env)
  fg           0 | 2..8             XESS_FG=<n>: XeSS frame generation at n x whatever the game's menu offers
                                    (5..8 experimental upstream); 0 = the game decides
  fsr          off | xess | fg | inject
                                    xess   -> FSR=xess: FSR 3.1+ upscaling on XeSS SR, its frame generation on XeSS-FG
                                    fg     -> FSR_FG=xess: only FSR frame generation on XeSS-FG
                                    inject -> FSR=xess FSR_FG=inject: XeSS-FG for a game with FSR upscaling and no
                                              FSR frame generation
  xess_latest  bool                 XESS=latest: Intel's current XeSS libraries instead of the game's older ones
  low_latency  bool (default on)    off -> IGDEXT_XELL_CAP=off (the shim's low latency with frame generation)
  opti         off | xess | fsr4    OptiScaler (Decky Framegen) upscaler for this game: XeSS on XMX, or FSR 4 INT8
                                    (experimental on Intel); only takes effect when the game runs through fgmod
  opti_xefg    bool                 OptiScaler's frame-generation output = XeSS-FG (on XMX through the shim)

The plugin never edits Steam's launch options itself; the user adds the wrapper once per game (Per-game does it on
the first change) and the file does the rest. An empty composition removes the file, so a game with nothing set
runs exactly as without ClawDeck.
"""
from __future__ import annotations

import os
import re

from . import xess

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

FSR_ROUTES = {
    "off": {},
    "xess": {"FSR": "xess"},
    "fg": {"FSR_FG": "xess"},
    "inject": {"FSR": "xess", "FSR_FG": "inject"},
}
OPTI_PRESETS = {
    "off": {},
    "xess": {"Dx12Upscaler": "xess"},
    # OptiScaler 0.9.4+: FSR 4 on GPUs without official support, INT8 model (Decky Framegen README)
    "fsr4": {"Dx12Upscaler": "fsr31", "FSR_Fsr4ForceEnableInt8": "true"},
}
OPTI_XEFG = {"FrameGen_Enabled": "true", "FrameGen_FGOutput": "xefg"}
KEYS = {"xmx", "fg", "fsr", "xess_latest", "low_latency", "opti", "opti_xefg"}


def compose(launch: dict, per_game: bool = True) -> dict[str, str]:
    env: dict[str, str] = {}
    x = launch.get("xmx")
    if per_game:
        if x is True:
            env["CLAWDECK_XMX"] = "1"
        elif x is False:
            env["CLAWDECK_XMX"] = "0"
    elif x is False:
        env.update(xess.stock_env())
    fg = launch.get("fg") or 0
    if not isinstance(fg, int) or isinstance(fg, bool) or not (fg == 0 or 2 <= fg <= 8):
        raise ValueError(f"frame generation multiplier must be 2 to 8 (or 0 for the game's own), not {fg!r}")
    if fg:
        env["XESS_FG"] = str(fg)
    route = launch.get("fsr") or "off"
    if route not in FSR_ROUTES:
        raise ValueError(f"unknown FSR route {route!r}")
    env.update(FSR_ROUTES[route])
    if launch.get("xess_latest"):
        env["XESS"] = "latest"
    if launch.get("low_latency") is False:
        env["IGDEXT_XELL_CAP"] = "off"
    opti = launch.get("opti") or "off"
    if opti not in OPTI_PRESETS:
        raise ValueError(f"unknown OptiScaler preset {opti!r}")
    env.update(OPTI_PRESETS[opti])
    if launch.get("opti_xefg"):
        env.update(OPTI_XEFG)
    return env


def render(env: dict[str, str]) -> str:
    lines = ["# Written by ClawDeck for tools/clawdeck-launch.sh. KEY=VALUE, one per line."]
    for k, v in sorted(env.items()):
        if not KEY_RE.match(k) or "\n" in v:
            raise ValueError(f"bad launch variable {k!r}")
        lines.append(f"{k}={v}")
    return "\n".join(lines) + "\n"


def path_for(runtime_dir: str, appid: str) -> str:
    if not str(appid).isdigit():
        raise ValueError("appid must be numeric")
    return os.path.join(runtime_dir, "launch", f"{appid}.env")


def write(runtime_dir: str, appid: str, launch: dict, per_game: bool = True) -> dict:
    env = compose(launch, per_game)
    p = path_for(runtime_dir, appid)
    if not env:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
        return {"path": None, "env": {}}
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(render(env))
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)
    return {"path": p, "env": env}
