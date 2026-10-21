"""Per-game upscaling and frame generation: the right tool for each game, set up the way OptiClick does on Windows.

The choice, in this order (plan()):
  native      the game ships XeSS. Nothing to install: pick XeSS in the game; with the xess-xmx-linux driver it runs
              on the Claw's XMX matrix units.
  optiscaler  the game has DLSS or FSR (2, 3, 3.1). OptiScaler swaps them for XeSS (XMX with the driver) and adds
              XeSS frame generation (XeFG, 2x-4x on Arc). The newest stable OptiScaler, installed exactly as its own
              setup_linux.sh does: every file of the release next to the game's main executable (for Unreal games
              <Name>/Binaries/Win64), OptiScaler.dll renamed to the first free name of dxgi / winmm / version /
              dbghelp / d3d12 / wininet / winhttp, `WINEDLLOVERRIDES=<name>=n,b`, Dxgi spoofing off unless the game
              only offers DLSS. Plus what OptiClick adds per game: OptiPatcher when the game is on its list (DLSS
              inputs without spoofing; OptiScaler/plugins/OptiPatcher.asi + LoadAsiPlugins=true), REFramework for
              Capcom RE Engine games (only its dinput8.dll, `dinput8=n,b`), and the INI fixes the OptiScaler
              compatibility list names for that game.
  lsfg        last resort, for games with no DLSS, FSR or XeSS: lsfg-vk, Lossless Scaling's frame generation as a
              Vulkan layer. Needs the user's own copy of Lossless Scaling (Steam app 993090). It runs on the GPU's
              shader cores, not on the XMX units, which only XeSS uses.
Games with kernel-level or online anti-cheat get nothing by default (injected DLLs can get an account banned).

Every file ClawDeck adds or replaces is listed in <exe dir>/.clawdeck-inject.json; uninstall() removes exactly
those and restores the backups (<file>.clawdeck.bak). Nothing is changed while the game runs.
"""
from __future__ import annotations

import json
import logging
import os
import shutil

log = logging.getLogger("clawdeck.injector")

LOSSLESS_APPID = 993090
PROXY_NAMES = ("dxgi.dll", "winmm.dll", "version.dll", "dbghelp.dll", "d3d12.dll", "wininet.dll", "winhttp.dll")
BACKUP_SUFFIX = ".clawdeck.bak"
MARKER = ".clawdeck-inject.json"
# files in an OptiScaler release that stay out of the game folder
SKIP_FROM_RELEASE = {"setup_linux.sh", "setup_windows.bat", "version", "!! readme_extract all files to game folder !!.txt",
                     "!! extract all files to game folder !!"}

# OptiScaler INI keys ClawDeck sets (the rest stays default). Checked against OptiScaler.ini of v0.9.4 / main:
#   [Upscalers] Dx12Upscaler xess | Dx11Upscaler xess_12 (DX11 through D3D12; native DX11 XeSS needs Intel's
#   Windows driver, which Proton does not have) | VulkanUpscaler xess
#   [XeSS] BuildPipelines true
#   [FrameGen] Enabled, FGInput (dlssg | fsrfg | xefg | upscaler), FGOutput xefg (needs libxess_fg + libxell, both in
#   the release)   [XeFG] InterpolationCount 1 = 2x, 2 = 3x, 3 = 4x
#   [Spoofing] Dxgi false = no Nvidia spoofing   [Plugins] LoadAsiPlugins true for OptiPatcher
INI_XMX = {
    "Upscalers": {"Dx12Upscaler": "xess", "Dx11Upscaler": "xess_12", "VulkanUpscaler": "xess"},
    "XeSS": {"BuildPipelines": "true"},
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
    """The folder that holds the game's main executable (where OptiScaler must go). Unreal games: the folder of
    <Name>-Win64-Shipping.exe, never Engine/. Otherwise the folder with the largest .exe that is not a launcher or
    redistributable; falls back to install_dir."""
    best, best_size, shipping = install_dir, -1, None
    skip = ("redist", "unitycrashhandler", "crashreport", "launcher", "setup", "vcredist", "dxsetup", "easyanticheat",
            "uninst", "unins000")
    for root, dirs, files in os.walk(install_dir):
        dirs[:] = [d for d in dirs if d.lower() not in ("_commonredist", "redist", "engine")]
        for fn in files:
            low = fn.lower()
            if not low.endswith(".exe") or any(s in low for s in skip):
                continue
            if low.endswith(("-win64-shipping.exe", "-wingdk-shipping.exe")) and shipping is None:
                shipping = root
            try:
                sz = os.path.getsize(os.path.join(root, fn))
            except OSError:
                continue
            if sz > best_size:
                best, best_size = root, sz
    return shipping or best


def choose_proxy(exe_dir: str) -> str:
    """First OptiScaler file name the game folder does not already use (ReShade, Special K and some games ship a
    dxgi.dll of their own; overwriting it would break them)."""
    taken = {f.lower() for f in os.listdir(exe_dir)} if os.path.isdir(exe_dir) else set()
    for name in PROXY_NAMES:
        if name not in taken:
            return name
    raise FileExistsError("every file name OptiScaler can use is already taken in this game folder")


def plan(scan: dict, compat: dict, xess_driver_ok: bool) -> dict:
    """What to do for this game, and why, in plain words."""
    u = scan["upscalers"]
    entry = compat.get("entry") or {}
    reasons, warnings, steps = [], [], []
    vulkan = u.get("fsr2") and any("vk" in f for f in scan.get("files", {}).get("fsr2", [])) or \
        any("_vk" in f for f in scan.get("files", {}).get("fsr3", []))
    if scan["anti_cheat"]:
        return {"mode": "none", "blocked": True,
                "reasons": [f"This game uses {', '.join(scan['anti_cheat'])}. Changing its files can get your account "
                            "banned, so ClawDeck leaves it alone."], "warnings": [], "steps": [], "options": {}}
    if u["xess"]:
        reasons.append("This game has XeSS built in: choose XeSS in its graphics settings.")
        reasons.append("With the XeSS driver it runs on the Claw's XMX cores." if xess_driver_ok else
                       "Install the XeSS driver above so it runs on the XMX cores instead of the slower fallback.")
        alt = "optiscaler" if not (u["xess_fg"] or u["dlss_fg"] or u["fsr3_fg"]) and not vulkan else None
        return {"mode": "native", "blocked": False, "reasons": reasons, "warnings": [], "steps": [],
                "alternative": alt, "options": _optiscaler_options(scan, compat, xess_driver_ok, vulkan)}
    if scan["has_upscaler"] and entry.get("status") != "broken":
        names = [n for n, k in (("DLSS", "dlss"), ("FSR 3", "fsr3"), ("FSR 2", "fsr2")) if u[k]]
        reasons.append(f"This game has {' and '.join(names)}. OptiScaler turns it into XeSS"
                       + (" on the XMX cores" if xess_driver_ok else "") + ", and adds XeSS frame generation.")
        opts = _optiscaler_options(scan, compat, xess_driver_ok, vulkan)
        if entry:
            reasons.append(f"OptiScaler's compatibility list: {'works' if entry['status'] == 'works' else entry['status']}"
                           f" (tested inputs: {', '.join(entry['inputs']) or '?'}).")
        steps.append("Newest OptiScaler, installed next to the game's main program")
        if opts["optipatcher"]:
            steps.append("OptiPatcher, so DLSS works on the Claw without pretending to be an Nvidia card")
        if opts["reframework"]:
            steps.append("REFramework, which Capcom RE Engine games need for OptiScaler")
        if opts["ini_fixes"]:
            steps.append("This game's known fixes from the compatibility list")
        if opts["fg"]:
            warnings.append("XeSS frame generation needs Borderless or Fullscreen Windowed mode in the game, and HDR10 "
                            "(not scRGB) if you use HDR.")
        else:
            warnings.append("This is a Vulkan game: OptiScaler can upscale it, but XeSS frame generation only works "
                            "in DirectX 12 games.")
        if entry.get("notes"):
            warnings.append(f"Note for this game: {entry['notes']}")
        return {"mode": "optiscaler", "blocked": False, "reasons": reasons, "warnings": warnings, "steps": steps,
                "options": opts, "page": entry.get("page")}
    if entry.get("status") == "broken":
        reasons.append("OptiScaler's compatibility list says it does not work in this game.")
    else:
        reasons.append("This game has no DLSS, FSR or XeSS for OptiScaler to work with.")
    reasons.append("Lossless Scaling frame generation (lsfg-vk) works with any game, as the last option. "
                   "It runs on the GPU's regular shader cores, not the XMX cores.")
    return {"mode": "lsfg", "blocked": False, "reasons": reasons,
            "warnings": ["Needs your own copy of Lossless Scaling from Steam."], "steps": [], "options": {}}


def _optiscaler_options(scan: dict, compat: dict, xess_driver_ok: bool, vulkan: bool) -> dict:
    u = scan["upscalers"]
    patcher = bool(compat.get("optipatcher")) and u["dlss"]
    if u["xess_fg"]:
        fg_input = "xefg"
    elif u["dlss_fg"] or u["streamline"]:
        fg_input = "dlssg"
    elif u["fsr3_fg"]:
        fg_input = "fsrfg"
    else:
        fg_input = "upscaler"
    only_dlss = u["dlss"] and not (u["fsr2"] or u["fsr3"] or u["xess"])
    # spoofing is what lets DLSS inputs appear on a non-Nvidia GPU; OptiPatcher does that without spoofing
    spoof = (only_dlss or fg_input == "dlssg") and not patcher
    fixes = {k: dict(v) for k, v in (compat.get("ini_fixes") or {}).items()}
    if "Dxgi" in fixes.get("Spoofing", {}):
        spoof = fixes["Spoofing"]["Dxgi"] != "false"
    return {"fg": not vulkan, "fg_input": fg_input, "multiplier": 3 if xess_driver_ok else 2, "spoof": spoof,
            "optipatcher": patcher, "reframework": scan["engine"] == "re" or bool(compat.get("needs_reframework")),
            "ini_fixes": fixes}


def ini_for(opts: dict) -> dict[str, dict[str, str]]:
    sections = {k: dict(v) for k, v in INI_XMX.items()}
    if opts.get("fg", True):
        sections["FrameGen"] = {"Enabled": "true", "FGInput": opts.get("fg_input", "upscaler"), "FGOutput": "xefg"}
        sections["XeFG"] = {"InterpolationCount": str(max(1, min(int(opts.get("multiplier", 2)) - 1, 3)))}
    sections.setdefault("Spoofing", {})["Dxgi"] = "auto" if opts.get("spoof") else "false"
    if opts.get("optipatcher"):
        sections.setdefault("Plugins", {})["LoadAsiPlugins"] = "true"
    for sec, kv in (opts.get("ini_fixes") or {}).items():
        sections.setdefault(sec, {}).update(kv)
    return sections


class Injector:
    def __init__(self, assets_dir: str):
        self.assets = assets_dir   # updates.py unpacks optiscaler/, optipatcher/, reframework/ here

    def assets_ready(self) -> dict:
        return {"optiscaler": os.path.exists(os.path.join(self.assets, "optiscaler", "OptiScaler.dll")),
                "optipatcher": os.path.exists(os.path.join(self.assets, "optipatcher", "OptiPatcher.asi")),
                "reframework": os.path.exists(os.path.join(self.assets, "reframework", "dinput8.dll"))}

    @staticmethod
    def marker(exe_dir: str) -> dict | None:
        try:
            with open(os.path.join(exe_dir, MARKER), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def state(self, game_dir: str) -> dict:
        d = find_game_exe_dir(game_dir)
        info = self.marker(d)
        # OptiScaler put there by something else (Decky Framegen, by hand): never overwrite it
        foreign = info is None and os.path.exists(os.path.join(d, "OptiScaler.ini"))
        return {"dir": d, "mode": (info or {}).get("mode", "none"), "optiscaler_present": info is not None and info.get("mode") == "optiscaler",
                "foreign_optiscaler": foreign, "version": (info or {}).get("version"),
                "native_xess": os.path.exists(os.path.join(d, "libxess.dll")) and info is None, "info": info}

    # ---- install / remove ----------------------------------------------------------------------------------------
    def _put(self, src: str, dst: str, info: dict) -> None:
        if os.path.exists(dst) and not os.path.exists(dst + BACKUP_SUFFIX) and dst not in info["copied"]:
            shutil.copy2(dst, dst + BACKUP_SUFFIX)
            info["backed_up"].append(dst)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        if dst not in info["copied"]:
            info["copied"].append(dst)

    def install_optiscaler(self, game_dir: str, opts: dict, versions: dict | None = None) -> dict:
        src = os.path.join(self.assets, "optiscaler")
        if not os.path.exists(os.path.join(src, "OptiScaler.dll")):
            raise FileNotFoundError("OptiScaler is not downloaded yet")
        d = find_game_exe_dir(game_dir)
        old = self.marker(d)
        if old is None and os.path.exists(os.path.join(d, "OptiScaler.ini")):
            raise FileExistsError("OptiScaler is already in this game, set up by another tool; remove it there first")
        if old:                                    # an update: start from the game's own files again
            self.uninstall(game_dir)
        proxy = choose_proxy(d)
        info = {"mode": "optiscaler", "proxy": proxy, "copied": [], "backed_up": [], "created_dirs": [],
                "options": opts, "version": (versions or {}).get("optiscaler"),
                "reframework_version": None, "optipatcher_version": None}
        for root, dirs, files in os.walk(src):
            rel = os.path.relpath(root, src)
            for dn in dirs:
                target = os.path.normpath(os.path.join(d, rel, dn))
                if not os.path.exists(target):
                    info["created_dirs"].append(target)
            for fn in files:
                if fn.lower() in SKIP_FROM_RELEASE:
                    continue
                name = proxy if (rel == "." and fn == "OptiScaler.dll") else fn
                self._put(os.path.join(root, fn), os.path.normpath(os.path.join(d, rel, name)), info)
        if opts.get("optipatcher") and self.assets_ready()["optipatcher"]:
            plugdir = os.path.join(d, "OptiScaler", "plugins")
            for p in (os.path.join(d, "OptiScaler"), plugdir):
                if not os.path.exists(p):
                    info["created_dirs"].append(p)
            self._put(os.path.join(self.assets, "optipatcher", "OptiPatcher.asi"), os.path.join(plugdir, "OptiPatcher.asi"), info)
            info["optipatcher_version"] = (versions or {}).get("optipatcher")
        elif opts.get("optipatcher"):
            opts = {**opts, "optipatcher": False, "spoof": True}
        if opts.get("reframework") and self.assets_ready()["reframework"]:
            self._put(os.path.join(self.assets, "reframework", "dinput8.dll"), os.path.join(d, "dinput8.dll"), info)
            info["reframework_version"] = (versions or {}).get("reframework")
        _ini_apply(os.path.join(d, "OptiScaler.ini"), ini_for(opts))
        info["options"] = opts
        with open(os.path.join(d, MARKER), "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
        return info

    def uninstall(self, game_dir: str) -> dict:
        d = find_game_exe_dir(game_dir)
        info = self.marker(d)
        if info is None:
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
        # folders ClawDeck created, deepest first; OptiScaler writes logs and caches into them, so remove the whole folder
        for p in sorted(info.get("created_dirs", []), key=len, reverse=True):
            shutil.rmtree(p, ignore_errors=True)
        for f in ("OptiScaler.log", "fakenvapi.log", "dlssg_to_fsr3.log", MARKER):
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass
        return {"mode": "none", "restored": info.get("backed_up", [])}

    @staticmethod
    def launch_env(mode: str, lsfg: dict | None = None, proxy: str | None = None, reframework: bool = False) -> dict[str, str]:
        """Environment the launch wrapper exports for the chosen mode.
        OptiScaler: the renamed DLL (and REFramework's dinput8) must be loaded native-first under Proton.
        lsfg-vk (lsfg-vk.dev, docs/configuration/environment-variables): LSFGVK_ENV=1 makes it read its options from
        the environment instead of its config file, so a per-game choice needs no edit of the user's conf.toml."""
        if mode == "optiscaler":
            dlls = [os.path.splitext(proxy or "dxgi.dll")[0]] + (["dinput8"] if reframework else [])
            return {"WINEDLLOVERRIDES": ";".join(f"{n}=n,b" for n in dlls)}
        if mode == "lsfg":
            o = {**LSFG_DEFAULTS, **(lsfg or {})}
            mult = max(2, min(int(o["multiplier"]), 4))
            flow = max(0.25, min(float(o["flow_scale"]), 1.0))
            return {"LSFGVK_ENV": "1", "LSFGVK_MULTIPLIER": str(mult), "LSFGVK_FLOW_SCALE": f"{flow:.2f}",
                    "LSFGVK_PERFORMANCE_MODE": "1" if o["performance_mode"] else "0"}
        return {}


LSFG_DEFAULTS = {"multiplier": 2, "flow_scale": 0.8, "performance_mode": True}
LSFG_LAYER_DIRS = ("~/.local/share/vulkan/implicit_layer.d", "/usr/local/share/vulkan/implicit_layer.d",
                   "/usr/share/vulkan/implicit_layer.d", "/etc/vulkan/implicit_layer.d")


def lsfg_state(home: str) -> dict:
    """Is the lsfg-vk Vulkan layer installed, and is Lossless Scaling (the paid Steam app whose frame generation it
    runs) installed? ClawDeck never ships Lossless.dll; the user owns it. Ownership itself is checked by the
    frontend with Steam's own app store (appStore.GetAppOverviewByAppID(993090).BIsOwned())."""
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
    return {"layer": layer, "v2": bool(layer and "LSFGVK" in os.path.basename(layer)), "lossless_dll": dll,
            "ready": bool(layer and dll)}


def install_lsfg_layer(staged: str, home: str) -> list[str]:
    """Copy an lsfg-vk v2 release (lib/, share/) into ~/.local, as Bazzite's `ujust get-lsfg install` does."""
    dest = os.path.join(home, ".local")
    copied = []
    for root, _dirs, files in os.walk(staged):
        rel = os.path.relpath(root, staged)
        for fn in files:
            if fn == "VERSION":
                continue
            target = os.path.normpath(os.path.join(dest, rel, fn))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(os.path.join(root, fn), target)
            copied.append(target)
    if not any(p.endswith("VkLayer_LSFGVK_frame_generation.json") for p in copied):
        raise FileNotFoundError("the lsfg-vk download did not contain its Vulkan layer")
    return copied


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
