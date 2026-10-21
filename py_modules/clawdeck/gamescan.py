"""What a game ships: which upscalers and frame generation it has, which engine it runs on, and whether it has
anti-cheat. The injector uses this to pick the right tool for each game, the way OptiClick does on Windows.

Everything is read from file names in the install folder (no game file is opened):
  DLSS            nvngx_dlss.dll                      DLSS frame gen   nvngx_dlssg.dll, sl.dlss_g.dll
  Streamline      sl.interposer.dll                   XeSS             libxess.dll (XeSS FG: libxess_fg.dll)
  FSR 2           ffx_fsr2_api_*x64.dll               FSR 3 / 3.1      ffx_fsr3*.dll, amd_fidelityfx_dx12.dll / _vk.dll
  FSR 3 FG        ffx_frameinterpolation*.dll, amd_fidelityfx_framegeneration_dx12.dll
  RE Engine       re_chunk_000.pak next to the exe    Unreal           <Name>/Binaries/Win64/<Name>-Win64-Shipping.exe
  Anti-cheat      EasyAntiCheat*, BattlEye / BEService*, nProtect / GameGuard, vgk (Vanguard)
Files ClawDeck itself put there (its OptiScaler install) are ignored, so a second scan sees the game as shipped.
"""
from __future__ import annotations

import fnmatch
import json
import os

UPSCALER_FILES = {
    "dlss": ("nvngx_dlss.dll",),
    "dlss_fg": ("nvngx_dlssg.dll", "sl.dlss_g.dll"),
    "streamline": ("sl.interposer.dll",),
    "xess": ("libxess.dll",),
    "xess_fg": ("libxess_fg.dll",),
    "fsr2": ("ffx_fsr2_api_x64.dll", "ffx_fsr2_api_dx12_x64.dll", "ffx_fsr2_api_vk_x64.dll"),
    "fsr3": ("ffx_fsr3_x64.dll", "ffx_fsr3upscaler_x64.dll", "amd_fidelityfx_dx12.dll", "amd_fidelityfx_vk.dll",
             "amd_fidelityfx_upscaler_dx12.dll"),
    "fsr3_fg": ("ffx_frameinterpolation_x64.dll", "amd_fidelityfx_framegeneration_dx12.dll"),
}
ANTI_CHEAT = {
    "Easy Anti-Cheat": ("easyanticheat*",),
    "BattlEye": ("battleye*", "beservice*"),
    "nProtect GameGuard": ("gameguard*", "npgamemon*"),
    "Riot Vanguard": ("vgk*",),
}
SKIP_DIRS = {"_commonredist", "redist", "directx", "dotnet", "vcredist", "support", "__installer", "shadercache"}
MAX_FILES = 40000          # a scan stops here; big games have ~10k files


def _ours(exe_dir: str) -> set[str]:
    """Files ClawDeck's injector installed (from its marker), lower-cased, so they don't count as the game's."""
    try:
        with open(os.path.join(exe_dir, ".clawdeck-inject.json"), "r", encoding="utf-8") as f:
            info = json.load(f)
        return {os.path.basename(p).lower() for p in info.get("copied", [])}
    except (OSError, ValueError):
        return set()


def scan(install_dir: str, exe_dir: str) -> dict:
    ours = _ours(exe_dir)
    found: dict[str, list[str]] = {k: [] for k in UPSCALER_FILES}
    anti: set[str] = set()
    exes: list[str] = []
    re_engine = False
    n = 0
    target = os.path.normpath(exe_dir)
    for root, dirs, files in os.walk(install_dir):
        in_exe_dir = os.path.normpath(root) == target
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
        for d in dirs:
            for name, pats in ANTI_CHEAT.items():
                if any(fnmatch.fnmatch(d.lower(), p) for p in pats):
                    anti.add(name)
        for fn in files:
            n += 1
            low = fn.lower()
            if low.endswith(".exe") and in_exe_dir:
                exes.append(fn)
            if low == "re_chunk_000.pak":
                re_engine = True
            if in_exe_dir and low in ours:
                continue
            for kind, names in UPSCALER_FILES.items():
                if low in names:
                    found[kind].append(os.path.relpath(os.path.join(root, fn), install_dir))
            for name, pats in ANTI_CHEAT.items():
                if any(fnmatch.fnmatch(low, p) for p in pats):
                    anti.add(name)
        if n > MAX_FILES:
            break
    unreal = any(e.lower().endswith(("-win64-shipping.exe", "-wingdk-shipping.exe")) for e in exes) or \
        os.path.isdir(os.path.join(install_dir, "Engine"))
    has = {k: bool(v) for k, v in found.items()}
    return {
        "exe_dir": exe_dir,
        "exes": sorted(exes),
        "engine": "re" if re_engine else "unreal" if unreal else None,
        "upscalers": has,
        "files": {k: v[:3] for k, v in found.items() if v},
        "has_upscaler": has["dlss"] or has["xess"] or has["fsr2"] or has["fsr3"],
        "anti_cheat": sorted(anti),
    }


def summary(s: dict) -> list[str]:
    """Plain words for what the scan found, for the UI."""
    u = s["upscalers"]
    out = []
    names = [n for n, k in (("DLSS", "dlss"), ("FSR 3", "fsr3"), ("FSR 2", "fsr2"), ("XeSS", "xess")) if u[k]]
    out.append("Upscalers in this game: " + (", ".join(names) if names else "none found"))
    fg = [n for n, k in (("DLSS frame generation", "dlss_fg"), ("FSR frame generation", "fsr3_fg"),
                         ("XeSS frame generation", "xess_fg")) if u[k]]
    if fg:
        out.append("Frame generation in this game: " + ", ".join(fg))
    if s["engine"] == "re":
        out.append("Capcom RE Engine game")
    elif s["engine"] == "unreal":
        out.append("Unreal Engine game")
    if s["anti_cheat"]:
        out.append("Anti-cheat: " + ", ".join(s["anti_cheat"]))
    return out
