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

# OptiScaler INI keys ClawDeck sets (the rest stays default); names from OptiScaler.ini of the release in use
INI_XMX = {
    "Upscalers": {"Dx12Upscaler": "xess", "Dx11Upscaler": "xess", "VulkanUpscaler": "xess"},
    "XeSS": {"BuildPipelines": "true", "NetworkModel": "0"},
    "FrameGen": {"FGType": "xess"},       # XeSS frame generation instead of the game's / OptiFG
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
    def launch_env(mode: str) -> dict[str, str]:
        """Environment the launch wrapper exports for the chosen mode."""
        if mode == "optiscaler":
            return {"WINEDLLOVERRIDES": "dxgi=n,b"}
        if mode == "lsfg":
            return {"LSFG_LEGACY": "1"}   # lsfg-vk reads its per-game config; this key is a placeholder until verified on the Claw
        return {}
