"""XMX injector: make games that do not ship XeSS use the Claw's XMX cores anyway, per game, three modes.

  optiscaler  OptiScaler (github.com/optiscaler/OptiScaler) in the game folder, as `dxgi.dll` (the proxy name that
              works under Proton), with an INI that routes DLSS / FSR inputs to XeSS and, when the game has frame
              generation, swaps it for XeSS frame generation. Needs WINEDLLOVERRIDES="dxgi=n,b" in the launch
              options, which the launch wrapper adds. The XeSS libraries it loads are the 2.x set from Intel's SDK
              so that the xess-xmx-linux shim can run them on XMX.
  lsfg        lsfg-vk (github.com/PancakeTAS/lsfg-vk), a Vulkan layer version of Lossless Scaling frame generation,
              for games with no upscaler at all: enabled per game through its config file / environment.
  native      the game ships XeSS: nothing to inject; the xess-xmx-linux driver module makes it run on XMX, and the
              XeLL driver-mode state is read from the shim's status.

This module does the file work and produces the launch-option environment; it never modifies a game while it is
running, keeps a backup of every file it replaces (<file>.clawdeck.bak) and can undo. Downloads are done by the
Setup tab (releases from GitHub, pinned versions in assets/versions.json), not by this module.
"""
from __future__ import annotations

import logging
import os
import shutil
import json

log = logging.getLogger("clawdeck.injector")

OPTISCALER_FILES = ("OptiScaler.dll", "OptiScaler.ini", "amd_fidelityfx_dx12.dll", "amd_fidelityfx_vk.dll",
                    "libxess.dll", "libxess_dx11.dll", "libxess_fg.dll", "libxell.dll", "nvngx.dll")
PROXY_NAME = "dxgi.dll"
BACKUP_SUFFIX = ".clawdeck.bak"

# OptiScaler INI keys ClawDeck sets (the rest stays default). Checked against OptiScaler.ini of v0.9.4 (2026-07):
#   [Upscalers] Dx12Upscaler xess | Dx11Upscaler xess_12 (DX11 through D3D12; native DX11 XeSS needs Intel's
#   Windows driver, which Proton does not have) | VulkanUpscaler xess
#   [FrameGen] FGInput upscaler (works in any game the upscaler hooks; HUD may ghost without Hudfix) and
#   FGOutput xefg (XeSS frame generation, needs libxess_fg.dll + libxell.dll next to it)
#   [XeSS] BuildPipelines true
INI_XMX = {
    "Upscalers": {"Dx12Upscaler": "xess", "Dx11Upscaler": "xess_12", "VulkanUpscaler": "xess"},
    "XeSS": {"BuildPipelines": "true"},
    "FrameGen": {"Enabled": "true", "FGInput": "upscaler", "FGOutput": "xefg"},
}


def _ini_apply(path: str, sections: dict[str, dict[str, str]]) -> None:
    """Minimal INI editor that keeps comments and order: sets key=value inside [section], adds missing ones."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    out: list[str] = []
    cur = None
    pending = {s: dict(kv) for s, kv in sections.items()}
    for line in lines:
        st = line.strip()
        if st.startswith("[") and st.endswith("]"):
            if cur in pending:
                for k, v in pending.pop(cur).items():
                    out.append(f"{k}={v}")
            cur = st[1:-1]
        elif cur in pending and "=" in st and not st.startswith(";"):
            k = st.split("=", 1)[0].strip()
            if k in pending[cur]:
                line = f"{k}={pending[cur].pop(k)}"
        out.append(line)
    if cur in pending:
        for k, v in pending.pop(cur).items():
            out.append(f"{k}={v}")
    for s, kv in pending.items():
        out.append(f"[{s}]")
        out.extend(f"{k}={v}" for k, v in kv.items())
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def find_game_exe_dir(install_dir: str) -> str:
    """The folder that holds the game's main executable (where the proxy DLL must go). Heuristic: the directory with
    the largest .exe that is not a launcher/redist; falls back to install_dir."""
    best, best_size = install_dir, -1
    skip = ("redist", "unitycrashhandler", "crashreport", "launcher", "setup", "vcredist", "dxsetup", "easyanticheat")
    for root, dirs, files in os.walk(install_dir):
        dirs[:] = [d for d in dirs if d.lower() not in ("_commonredist", "redist", "engine")]
        for fn in files:
            if fn.lower().endswith(".exe") and not any(s in fn.lower() for s in skip):
                try:
                    sz = os.path.getsize(os.path.join(root, fn))
                except OSError:
                    continue
                if sz > best_size:
                    best, best_size = root, sz
    return best


class Injector:
    def __init__(self, assets_dir: str):
        self.assets = assets_dir   # where downloaded OptiScaler / XeSS / lsfg-vk releases live

    def assets_ready(self) -> dict:
        return {"optiscaler": os.path.exists(os.path.join(self.assets, "optiscaler", "OptiScaler.dll")),
                "xess": os.path.exists(os.path.join(self.assets, "xess", "libxess.dll"))}

    def stage(self, kind: str, extracted: str) -> dict:
        """Copy what a downloaded release contains into assets/<kind>: OptiScaler's folder (the one holding
        OptiScaler.dll) or the XeSS SDK's runtime DLLs (libxess*.dll, libxell.dll)."""
        dest = os.path.join(self.assets, kind)
        os.makedirs(dest, exist_ok=True)
        copied = []
        for root, _dirs, files in os.walk(extracted):
            if kind == "optiscaler" and "OptiScaler.dll" in files:
                for fn in files:
                    shutil.copy2(os.path.join(root, fn), os.path.join(dest, fn))
                    copied.append(fn)
                break
            if kind == "xess":
                for fn in files:
                    if fn.lower() in ("libxess.dll", "libxess_dx11.dll", "libxess_fg.dll", "libxell.dll"):
                        shutil.copy2(os.path.join(root, fn), os.path.join(dest, fn))
                        copied.append(fn)
        if not copied:
            raise FileNotFoundError(f"nothing usable for {kind} in the downloaded release")
        return {"kind": kind, "copied": sorted(set(copied))}

    def state(self, game_dir: str) -> dict:
        d = find_game_exe_dir(game_dir)
        proxy = os.path.join(d, PROXY_NAME)
        marker = os.path.join(d, ".clawdeck-inject.json")
        info = None
        try:
            with open(marker, "r", encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            pass
        return {"dir": d, "mode": (info or {}).get("mode", "none"), "optiscaler_present": os.path.exists(proxy) and info is not None,
                "native_xess": os.path.exists(os.path.join(d, "libxess.dll")) and info is None, "info": info}

    def install_optiscaler(self, game_dir: str, xess_fg: bool = True) -> dict:
        src = os.path.join(self.assets, "optiscaler")
        xess_src = os.path.join(self.assets, "xess")
        if not os.path.exists(os.path.join(src, "OptiScaler.dll")):
            raise FileNotFoundError("OptiScaler is not downloaded yet (Setup tab)")
        d = find_game_exe_dir(game_dir)
        copied, backed = [], []
        for fn in OPTISCALER_FILES:
            s = os.path.join(xess_src if fn.startswith("libx") else src, fn)
            if not os.path.exists(s):
                continue
            dst = os.path.join(d, PROXY_NAME if fn == "OptiScaler.dll" else fn)
            if os.path.exists(dst) and not os.path.exists(dst + BACKUP_SUFFIX):
                shutil.copy2(dst, dst + BACKUP_SUFFIX)
                backed.append(dst)
            shutil.copy2(s, dst)
            copied.append(dst)
        ini = os.path.join(d, "OptiScaler.ini")
        sections = {k: dict(v) for k, v in INI_XMX.items()}
        if not xess_fg:
            sections.pop("FrameGen", None)
        _ini_apply(ini, sections)
        info = {"mode": "optiscaler", "copied": copied, "backed_up": backed, "xess_fg": xess_fg}
        with open(os.path.join(d, ".clawdeck-inject.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
        return info

    def uninstall(self, game_dir: str) -> dict:
        d = find_game_exe_dir(game_dir)
        marker = os.path.join(d, ".clawdeck-inject.json")
        try:
            with open(marker, "r", encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            return {"mode": "none"}
        for p in info.get("copied", []):
            try:
                os.remove(p)
            except OSError:
                pass
        for p in info.get("backed_up", []):
            try:
                os.replace(p + BACKUP_SUFFIX, p)
            except OSError:
                pass
        try:
            os.remove(marker)
        except OSError:
            pass
        return {"mode": "none", "restored": info.get("backed_up", [])}

    @staticmethod
    def launch_env(mode: str, lsfg: dict | None = None) -> dict[str, str]:
        """Environment the launch wrapper exports for the chosen mode.
        lsfg-vk (lsfg-vk.dev, docs/configuration/environment-variables): LSFGVK_ENV=1 makes it read its options from
        the environment instead of its config file, so a per-game choice needs no edit of the user's conf.toml."""
        if mode == "optiscaler":
            return {"WINEDLLOVERRIDES": "dxgi=n,b"}
        if mode == "lsfg":
            o = {**LSFG_DEFAULTS, **(lsfg or {})}
            mult = max(2, min(int(o["multiplier"]), 4))
            flow = max(0.25, min(float(o["flow_scale"]), 1.0))
            return {"LSFGVK_ENV": "1", "LSFGVK_MULTIPLIER": str(mult), "LSFGVK_FLOW_SCALE": f"{flow:.2f}",
                    "LSFGVK_PERFORMANCE_MODE": "1" if o["performance_mode"] else "0"}
        return {}


LSFG_DEFAULTS = {"multiplier": 2, "flow_scale": 0.8, "performance_mode": True}
LSFG_LAYER_DIRS = ("/usr/share/vulkan/implicit_layer.d", "/etc/vulkan/implicit_layer.d",
                   "~/.local/share/vulkan/implicit_layer.d")


def lsfg_state(home: str) -> dict:
    """Is the lsfg-vk Vulkan layer installed, and is Lossless Scaling (the paid Steam app whose DLL it loads) present?
    ClawDeck never ships Lossless.dll; the user owns it."""
    layer = None
    for d in LSFG_LAYER_DIRS:
        d = d.replace("~", home, 1)
        try:
            for fn in os.listdir(d):
                if "lsfg" in fn.lower() and fn.endswith(".json"):
                    layer = os.path.join(d, fn)
                    break
        except OSError:
            continue
        if layer:
            break
    dll = None
    for lib in _steam_libraries(home):
        cand = os.path.join(lib, "steamapps", "common", "Lossless Scaling", "Lossless.dll")
        if os.path.exists(cand):
            dll = cand
            break
    return {"layer": layer, "lossless_dll": dll, "ready": bool(layer and dll)}


def steam_install_dir(home: str, appid: str | int) -> str | None:
    """A Steam game's install folder, from steamapps/appmanifest_<appid>.acf ("installdir") in whichever library holds
    it. Steam's frontend exposes no install path for the running app, so the backend reads Steam's own manifest."""
    if not str(appid).isdigit():
        return None
    for lib in _steam_libraries(home):
        acf = os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf")
        try:
            with open(acf, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    parts = line.strip().split('"')
                    if len(parts) >= 4 and parts[1] == "installdir":
                        d = os.path.join(lib, "steamapps", "common", parts[3])
                        return d if os.path.isdir(d) else None
        except OSError:
            continue
    return None


def _steam_libraries(home: str) -> list[str]:
    root = os.path.join(home, ".local", "share", "Steam")
    libs = [root]
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith('"path"'):
                    p = line.split('"')[3]
                    if p not in libs:
                        libs.append(p)
    except (OSError, IndexError):
        pass
    return libs
