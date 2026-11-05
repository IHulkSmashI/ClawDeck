"""Per-game launch environment: the one file tools/clawdeck-launch.sh reads (<runtime>/launch/<appid>.env).

Every per-game choice that has to be in the game's environment lands here, composed from the game's launch settings
(profiles.py, key "launch"). The xess-xmx-linux variables are read by the package's Proton hook and shim
(its docs/launch-options.md). The OptiScaler settings go into the game's OptiScaler.ini when ClawDeck's own
OptiScaler is put in or re-applied (opti_ini, optiscaler.patch); the same Section_Key names are what Decky
Framegen's fgmod reads from the environment, so a game Decky Framegen patched still gets the presets.

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
                                    OptiScaler upscaler for this game: XeSS on XMX, FSR 4 INT8 (experimental on
                                    Intel), or FSR 4.1.2 on XMX (nitec's build, regular or lite); takes effect when
                                    ClawDeck's OptiScaler is in the game (or, for compatibility, through fgmod)
  opti_xefg    bool                 OptiScaler's frame-generation output = XeSS-FG (on XMX through the shim)
  reframework  bool                 a Capcom RE Engine game: REFramework (dinput8=n,b) and OptiClick's Capcom setup
                                    (capcom(): OptiScaler v10 in the OptiScaler/ subfolder, CAPCOM_INI)

FSR 4.1.2 (fsr4_xmx, fsr4_xmx_lite) is refused for a game with XMX switched off (xmx = false): nitec's build needs
the XMX cores and drew a black screen without them (Onimusha, Claw 8 EX, 2026-11-04).

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
    # original, kept as fsr-original.dll) first. The FFX Upscaler list with nitec's file (CONTROL, Claw 8 EX, 2026-11-04):
    # 0-5 FSR 4.0.2X (+ Skip, QSkip, Hybrid...), 6 FSR 4.1.2X, 7/8 its Skip/QSkip, 9 FSR 4.1.2X Lite, 10 Lite Skip,
    # 11+ DLSS-EX/FX/EFX/KX/MX/LX (DLSS presets on XMX from the game's nvngx_dlss.dll). Lite is its own entry.
    "fsr4_xmx": {"Dx12Upscaler": "fsr31", "FSR_UpscalerIndex": "6", "FSR_Fsr4Update": "true",
                 "CLAWDECK_FSR4_XMX": "regular", "FSR4_XMX_NO_DIALOG": "1"},
    "fsr4_xmx_lite": {"Dx12Upscaler": "fsr31", "FSR_UpscalerIndex": "9", "FSR_Fsr4Update": "true",
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
KEYS = {"xmx", "fg", "fsr", "xess_latest", "low_latency", "opti", "opti_xefg", "opti_fg", "opti_ini", "reframework", "opti_dll",
        "opti_overlay"}
# OptiScaler's own FPS overlay (OptiScaler.ini [Menu] FpsOverlayType): shows the upscaler and "(XeFG)" when frame
# generation runs, which Steam's overlay can't tell. Top right, clear of Steam's performance overlay at the top left.
OVERLAYS = {0: "FPS", 1: "Simple", 2: "Detailed", 3: "Detailed + graph", 4: "Full", 5: "Full + graph"}
# OptiScaler.ini sections (0.9.4, plus v10's DLSSG and NvngxFG), for turning a preset into ini lines for ClawDeck's own OptiScaler (optiscaler.py)
INI_SECTIONS = {"Upscalers", "FrameGen", "FSRFG", "XeFG", "DLSSG", "NvngxFG", "OptiFG", "Inputs", "FSRFGInputs", "Framerate", "XeSS", "FSR",
                "DLSS", "DLSSD", "Nukems", "Libraries", "Menu", "Spoofing", "Plugins", "NvApi", "Dx11withDx12", "Hooks",
                "Sharpness", "OutputScaling", "CAS", "Log", "InitFlags", "UpscaleRatio", "QualityOverrides", "DRS", "HDR",
                "VSync", "Anisotropy", "Mipmap", "ProcessFilter", "Hotfix"}
# Capcom RE Engine games, as OptiClick sets them up on Windows (its Onimusha install, read 2026-11-04) with OptiScaler
# v10: the game's own DLSS frame generation (DLSS-G, which needs the Nvidia spoofing OptiScaler does by default on
# Intel, so no Spoofing_Dxgi=false here although OptiScaler's wiki lists it for Onimusha) turned into XeSS-FG;
# OptiScaler's libraries in OptiScaler/ (OptiDllPath); plugins (OptiPatcher) loaded. OptiClick's FramerateLimit was
# the maintainer's own cap and isn't copied. The upscaler (Dx12Upscaler, FSR UpscalerIndex), the overlay and the
# frame count come from the user's picks (opti_ini).
CAPCOM_INI = {
    "Upscalers_Dx11Upscaler": "fsr31_12", "Upscalers_VulkanUpscaler": "xess",
    "FrameGen_Enabled": "true", "FrameGen_FGInput": "DLSSG", "FrameGen_FGOutput": "XeFG",
    "FrameGen_FGNvngxReplacement": "XeFG_NVNGX",
    "Libraries_OptiDllPath": ".\\OptiScaler", "Plugins_LoadAsiPlugins": "true", "NvngxFG_DisableHudless": "true",
    "Hotfix_RestoreComputeSignature": "true",           # OptiScaler's wiki: generally required in Onimusha
}
# not for the Capcom setup: the wiki's Dxgi=false (see above), 0.9.4-only FSR switches, XeFG's own count (DLSS-G's is used)
CAPCOM_DROP = ("Spoofing_Dxgi", "FSR_Fsr4Update", "FSR_Fsr4ForceEnableInt8", "XeFG_InterpolationCount")
# frames generated per rendered one when ClawDeck has no multiplier for the game: OptiClick's value (OptiScaler caps it
# at what the game's DLSS-G offers)
CAPCOM_FG_COUNT = "2"


def capcom(launch: dict) -> bool:
    """OptiClick's Capcom setup for this game (OptiScaler v10, subfolder layout, CAPCOM_INI): every game that gets
    REFramework, i.e. Capcom's RE Engine games (gamedb.recommend)."""
    return bool(launch.get("reframework"))


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
    if opti in FSR4_XMX and x is False:
        raise ValueError("FSR 4.1.2 needs the XMX cores, and XMX is off for this game: pick XeSS 2, or switch XMX on first")
    env.update(OPTI_PRESETS[opti])
    if launch.get("opti_xefg"):
        env.update(OPTI_XEFG)
    ofg = launch.get("opti_fg")
    if ofg is not None:
        if ofg not in OPTI_FG:
            raise ValueError(f"unknown OptiScaler frame generation {ofg!r}")
        env.update(OPTI_FG[ofg])
    ov = launch.get("opti_overlay")
    if ov is not None and (isinstance(ov, bool) or ov not in OVERLAYS):
        raise ValueError(f"unknown OptiScaler overlay {ov!r}")
    ini = launch.get("opti_ini") or {}
    if not isinstance(ini, dict):
        raise ValueError("opti_ini must be a map of Section_Key to value")
    for k, v in ini.items():                       # the game's OptiScaler settings (assets/games.json)
        v = str(v)
        if capcom(launch) and k in CAPCOM_DROP:
            continue
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
    ov = launch.get("opti_overlay")
    if isinstance(ov, int) and not isinstance(ov, bool) and ov in OVERLAYS:
        env.update({"Menu_ShowFps": "true", "Menu_FpsOverlayType": str(ov), "Menu_FpsOverlayPos": "1"})
    if capcom(launch):
        for k in CAPCOM_DROP:
            env.pop(k, None)
        for k in [k for k in env if k.startswith("FrameGen_")]:
            env.pop(k)
        env.update(CAPCOM_INI)
        if launch.get("opti_fg") == "off":                # the user picked frame generation off
            env["FrameGen_Enabled"] = "false"
        else:                                              # DLSS-G's count: 1 = 2x, 2 = 3x, 3 = 4x (OptiScaler)
            env["DLSSG_OverrideInterpolationCount"] = str(min(max(fg - 1, 1), 3)) if fg else CAPCOM_FG_COUNT
    out = {}
    for k, v in env.items():
        if k in UNPREFIXED:
            out[f"{UNPREFIXED[k]}_{k}"] = str(v)
        elif "_" in k and k.split("_", 1)[0] in INI_SECTIONS:
            out[k] = str(v)
    return out
