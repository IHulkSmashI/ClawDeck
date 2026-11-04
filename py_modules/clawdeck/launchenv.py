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
  opti         off | xess | fsr4 | fsr4_xmx | fsr4_xmx_lite
                                    OptiScaler (Decky Framegen) upscaler for this game: XeSS on XMX, FSR 4 INT8
                                    (experimental on Intel), or FSR 4.1.2 on XMX (nitec's build, regular or lite);
                                    only takes effect when the game runs through fgmod
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
    # FSR 4.1.2 on the XMX cores: nitec's "B Boost" build of AMD's upscaler (OptiScaler Discord). The user puts its
    # files in ~/fsr4-xmx; clawdeck-launch.sh copies them over fgmod's FSR 4 after fgmod ran (CLAWDECK_FSR4_XMX).
    # OptiScaler: the FSR 3.1 path with the FSR 4 provider. With the B Boost files OptiScaler lists 4.0.2 first (AMD's
    # original, kept as fsr-original.dll) and 4.1.2 (the XMX build) at index 6 (CONTROL, Claw 8 EX, 2026-11-04).
    "fsr4_xmx": {"Dx12Upscaler": "fsr31", "FSR_UpscalerIndex": "6", "FSR_Fsr4Update": "true",
                 "CLAWDECK_FSR4_XMX": "regular", "FSR4_XMX_NO_DIALOG": "1"},
    "fsr4_xmx_lite": {"Dx12Upscaler": "fsr31", "FSR_UpscalerIndex": "6", "FSR_Fsr4Update": "true",
                      "CLAWDECK_FSR4_XMX": "lite", "FSR4_XMX_LITE": "1", "FSR4_XMX_NO_DIALOG": "1"},
}
FSR4_XMX = ("fsr4_xmx", "fsr4_xmx_lite")
OPTI_XEFG = {"FrameGen_Enabled": "true", "FrameGen_FGOutput": "xefg"}
# OptiScaler's frame generation, always out through XeSS-FG (on XMX): where the frames come from
OPTI_FG = {
    "off": {"FrameGen_Enabled": "false", "FrameGen_FGInput": "nofg", "FrameGen_FGOutput": "nofg"},
    "dlssg": {"FrameGen_Enabled": "true", "FrameGen_FGInput": "dlssg", "FrameGen_FGOutput": "xefg"},      # the game's DLSS FG
    "fsrfg": {"FrameGen_Enabled": "true", "FrameGen_FGInput": "fsrfg", "FrameGen_FGOutput": "xefg"},      # the game's FSR FG
    "upscaler": {"FrameGen_Enabled": "true", "FrameGen_FGInput": "upscaler", "FrameGen_FGOutput": "xefg"},  # any game
}
KEYS = {"xmx", "fg", "fsr", "xess_latest", "low_latency", "opti", "opti_xefg", "opti_fg", "opti_ini", "reframework", "opti_dll"}
# OptiScaler.ini sections (0.9.4), for turning a preset into ini lines for ClawDeck's own OptiScaler (optiscaler.py)
INI_SECTIONS = {"Upscalers", "FrameGen", "FSRFG", "XeFG", "OptiFG", "Inputs", "FSRFGInputs", "Framerate", "XeSS", "FSR",
                "DLSS", "DLSSD", "Nukems", "Libraries", "Menu", "Spoofing", "Plugins", "NvApi", "Dx11withDx12", "Hooks",
                "Sharpness", "OutputScaling", "CAS", "Log", "InitFlags", "UpscaleRatio", "QualityOverrides", "DRS", "HDR",
                "VSync", "Anisotropy", "Mipmap", "ProcessFilter", "Hotfix"}
UNPREFIXED = {"Dx12Upscaler": "Upscalers", "Dx11Upscaler": "Upscalers", "VulkanUpscaler": "Upscalers"}
INI_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+$")     # Decky Framegen's Section_Key form
INI_VAL_RE = re.compile(r"^(true|false|auto|-?[0-9]+(\.[0-9]+)?)$")


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
    # automatic + something that runs through xess-xmx-linux's hook (an FSR game on XeSS, frame generation, the
    # latest XeSS): that game needs the XMX driver even though it ships no XeSS of its own (CONTROL Resonant: FSR
    # only). OptiScaler games are found by the scope itself (optiscaler.ini).
    if per_game and x is None and (fg or (launch.get("fsr") or "off") != "off" or launch.get("xess_latest")
                                   or launch.get("opti") in (*FSR4_XMX, "fsr4", "xess")):
        env["CLAWDECK_XMX"] = "1"
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
    # low latency (XeLL) is always on: an old "off" choice is ignored (maintainer, 2026-11-04)
    opti = launch.get("opti") or "off"
    if opti == "fsr4":                             # FSR 4 INT8 (not on XMX) was retired: everything runs on XMX
        opti = "fsr4_xmx"
    if opti not in OPTI_PRESETS:
        raise ValueError(f"unknown OptiScaler preset {opti!r}")
    env.update(OPTI_PRESETS[opti])
    if launch.get("opti_xefg"):
        env.update(OPTI_XEFG)
    ofg = launch.get("opti_fg")
    if ofg is not None:
        if ofg not in OPTI_FG:
            raise ValueError(f"unknown OptiScaler frame generation {ofg!r}")
        env.update(OPTI_FG[ofg])
    ini = launch.get("opti_ini") or {}
    if not isinstance(ini, dict):
        raise ValueError("opti_ini must be a map of Section_Key to value")
    for k, v in ini.items():                       # the game's OptiScaler settings (assets/games.json)
        v = str(v)
        if not INI_KEY_RE.match(k) or not INI_VAL_RE.match(v) or k in env:
            raise ValueError(f"bad OptiScaler setting {k}={v}")
        env[k] = v
    # native DLLs instead of Wine's own: REFramework's dinput8.dll; the LZMS-capable cabinet.dll the FSR 4 XMX build
    # needs (tools/cabinet-lzms). Ends in ";" because Decky Framegen's fgmod appends its proxy as ",dxgi=n,b"; Wine
    # splits entries at ";", so the result still reads as separate entries.
    od = launch.get("opti_dll")
    if od is not None and (not isinstance(od, str) or not re.fullmatch(r"[a-z0-9_]+", od)):
        raise ValueError(f"bad OptiScaler proxy name {od!r}")
    dlls = ([od] if od else []) + (["dinput8"] if launch.get("reframework") else []) + (["cabinet"] if opti in FSR4_XMX else [])
    if dlls:
        env["WINEDLLOVERRIDES"] = "".join(f"{d}=n,b;" for d in dlls)
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


def opti_ini(launch: dict) -> dict[str, str]:
    """The OptiScaler.ini lines for ClawDeck's own OptiScaler: the preset, XeSS frame generation, the game's settings.
    Variables that aren't ini settings (FSR 4 XMX's own switches, ClawDeck's) stay in the launch environment."""
    o = "fsr4_xmx" if launch.get("opti") == "fsr4" else launch.get("opti") or "off"
    env = {**OPTI_PRESETS.get(o, {}), **(OPTI_XEFG if launch.get("opti_xefg") else {}),
           **OPTI_FG.get(launch.get("opti_fg") or "", {}), **(launch.get("opti_ini") or {})}
    fg = launch.get("fg") or 0                     # XeSS-FG's multiplier: 2x-4x through OptiScaler (1-3 extra frames)
    if fg and (launch.get("opti_fg") not in (None, "off") or launch.get("opti_xefg")):
        env["XeFG_InterpolationCount"] = str(min(max(fg - 1, 1), 3))
    out = {}
    for k, v in env.items():
        if k in UNPREFIXED:
            out[f"{UNPREFIXED[k]}_{k}"] = str(v)
        elif "_" in k and k.split("_", 1)[0] in INI_SECTIONS:
            out[k] = str(v)
    return out
