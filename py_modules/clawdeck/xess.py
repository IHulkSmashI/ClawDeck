"""XeSS driver: install, update and check xess-xmx-linux (github.com/HHHHanasak1/xess-xmx-linux; our fork
IHulkSmashI/xess-xmx-linux carries the XeLL driver-mode work of PRs #23 and #24), which runs Intel XeSS super
resolution and frame generation on the XMX cores under Proton.

What the package does (its README): install.sh writes ~/.config/environment.d/50-xess-xmx.conf pointing
VK_DRIVER_FILES at <package>/run/intel_icd.json, puts its igdext64.dll shim into every Proton and prefix, and
enables xess-xmx-check.service, which before each session writes ~/.cache/xess-xmx/driver-status ("ok <date> <lib>
<driverInfo>" or "FAILED <date> <lib>: <reason>"). The package carries patched drivers for two Mesa versions in
lib/mesa-<version>/; lib/active names the default.

XeLL (Intel's latency reduction, used with XeSS frame generation) runs in its cross-vendor mode under stock Proton.
The fork's shim offers XeLL its driver mode when IGDEXT_XELL_PROBE is set for the game; with IGDEXT_TRACE=1 and
IGDEXT_XELL_LOG=1 it logs what XeLL did into the game's prefix (drive_c/igdext_trace.log, igdext_xell.log), which
is how ClawDeck tells per game whether XeLL is in driver mode, in cross-vendor mode, or not used at all.

ClawDeck reads that state, downloads a release into ~/.local/share/clawdeck (the package must stay where it was
unpacked) and runs its install.sh / uninstall.sh as the desktop user (never as root). Any XeSS library loaded in a
prefix goes through the shim, so OptiScaler's bundled libxess / libxess_fg / libxell (Bazzite's Decky Framegen
puts them next to a game) run on XMX too; optiscaler_in() tells the Per-game panel when that is the case.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import urllib.error

from . import downloads, sysfs, userexec

log = logging.getLogger("clawdeck.xess")

ENV_CONF = ".config/environment.d/50-xess-xmx.conf"
STATUS = ".cache/xess-xmx/driver-status"
INSTALL_ROOT = ".local/share/clawdeck"
SOURCE_FILE = ".clawdeck-source.json"      # written next to install.sh: which repo and tag this package came from
COMPATDATA = ".local/share/Steam/steamapps/compatdata"
STOCK_ICD_DIRS = ("/usr/share/vulkan/icd.d", "/usr/local/share/vulkan/icd.d", "/etc/vulkan/icd.d")
# the environment the launch wrapper exports when XeLL driver mode is on for a game (fork shim; the stock shim
# ignores the variables, so a game then runs exactly as before)
XELL_ENV = {"IGDEXT_XELL_PROBE": "1", "IGDEXT_XELL_LOG": "1", "IGDEXT_TRACE": "1"}


def package_dir(home: str) -> str | None:
    txt = sysfs.read(os.path.join(home, ENV_CONF)) or ""
    for line in txt.splitlines():
        k, _, v = line.partition("=")
        if k.strip() == "VK_DRIVER_FILES":
            for icd in v.strip().strip('"').split(":"):
                if icd.endswith("/run/intel_icd.json"):
                    return os.path.dirname(os.path.dirname(icd))
    return None


def parse_status(line: str | None) -> dict:
    if not line:
        return {"state": "unknown", "line": None}
    first = line.splitlines()[0]
    if first.startswith("ok "):
        return {"state": "ok", "line": first, "mode": "XMX (patched driver)"}
    if first.startswith("FAILED"):
        return {"state": "failed", "line": first, "mode": "DP4a fallback (stock driver)"}
    return {"state": "unknown", "line": first}


def official_proton(home: str) -> list[str]:
    """Proton builds that ship Wine's built-in libxess.dll (Intel's forwarding path). Detection by file presence;
    verify the path against the first Proton release that carries it."""
    roots = [os.path.join(home, ".local/share/Steam/compatibilitytools.d"), os.path.join(home, ".local/share/Steam/steamapps/common")]
    hits = []
    for r in roots:
        for d in sorted(glob.glob(os.path.join(r, "*"))):
            for sub in ("files/lib/wine/x86_64-windows/libxess.dll", "files/lib64/wine/x86_64-windows/libxess.dll"):
                if os.path.exists(os.path.join(d, sub)):
                    hits.append(os.path.basename(d))
                    break
    return hits


def source_of(pkg: str | None) -> dict | None:
    if not pkg:
        return None
    try:
        with open(os.path.join(pkg, SOURCE_FILE), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def state(home: str) -> dict:
    pkg = package_dir(home)
    st = parse_status(sysfs.read(os.path.join(home, STATUS)))
    drivers, active = [], None
    if pkg:
        drivers = sorted(os.path.basename(d)[5:] for d in glob.glob(os.path.join(pkg, "lib", "mesa-*"))
                         if os.path.isfile(os.path.join(d, "libvulkan_intel.so")))
        active = sysfs.read(os.path.join(pkg, "lib", "active"))
    name = os.path.basename(pkg) if pkg else ""
    src = source_of(pkg)
    return {
        "installed": pkg is not None,
        "package_dir": pkg,
        "version": name[len("xess-xmx-linux-"):] if name.startswith("xess-xmx-linux-") else (name or None),
        "source": src,                                     # {repo, tag} or None (installed by hand)
        "drivers": drivers,
        "default_driver": active,
        "status": st,
        # the fork's shim (XeLL driver mode) ships tools/xell-probe.sh; upstream's releases don't
        "xell_capable": bool(pkg) and os.path.exists(os.path.join(pkg, "tools", "xell-probe.sh")),
        "official_proton": official_proton(home),
    }


def _fetch(entry: dict, root: str, allow_unpinned: bool) -> tuple[dict, str | None]:
    """The fork's release when versions.json says use_fork; when the fork has published none (404), upstream's,
    with a note the UI shows. Any other failure is reported with the step that failed."""
    note = None
    if entry.get("use_fork") and entry.get("fork"):
        try:
            return downloads.fetch({**entry, "repo": entry["fork"], "use_fork": False}, root, allow_unpinned), None
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            note = f"{entry['fork']} has no release yet; installed {entry['repo']}'s instead (no XeLL driver mode)"
            log.warning(note)
    got = downloads.fetch({**entry, "use_fork": False}, root, allow_unpinned)
    return got, note


def install(home: str, entry: dict, allow_unpinned: bool = False, dry_run: bool = False) -> dict:
    root = os.path.join(home, INSTALL_ROOT)
    repo = entry.get("fork") if entry.get("use_fork") else entry["repo"]
    if dry_run:
        return {"ok": True, "dry_run": True, "would": f"download {repo} {entry.get('tag')} into {root}, run install.sh as the user"}
    try:
        got, note = _fetch(entry, root, allow_unpinned)
    except PermissionError as e:
        return {"ok": False, "error": str(e)}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"download: {e.url} answered {e.code} {e.reason}"}
    except Exception as e:   # network, archive, disk: say which and what
        return {"ok": False, "error": f"download: {type(e).__name__}: {e}"}
    pkg = _find_package(got["dir"])
    if not pkg:
        return {"ok": False, "error": f"install.sh not found in {os.path.basename(got['archive'])} (unpacked into {got['dir']})", **got}
    try:
        with open(os.path.join(pkg, SOURCE_FILE), "w", encoding="utf-8") as f:
            json.dump({"repo": got["repo"], "tag": got["tag"]}, f)
        userexec.chown_tree(root, home)
    except OSError as e:
        return {"ok": False, "error": f"preparing {pkg}: {e}"}
    r = userexec.run(["bash", os.path.join(pkg, "install.sh")], home, timeout=900, cwd=pkg)
    out = {"ok": r["rc"] == 0, "tag": got["tag"], "repo": got["repo"], "package_dir": pkg, "log": r["out"], "note": note,
           "next": "log out of Game Mode and back in (or reboot) so the session picks up the driver"}
    if r["rc"] != 0:
        tail = [ln for ln in r["out"].strip().splitlines() if ln.strip()][-3:]
        out["error"] = f"install.sh exited {r['rc']}: " + (" · ".join(tail) or "no output")
    return out


def _find_package(d: str) -> str | None:
    for root, dirs, files in os.walk(d):
        if "install.sh" in files and "uninstall.sh" in files:
            return root
        if root.count(os.sep) - d.count(os.sep) > 2:
            dirs[:] = []
    return None


def _tool(home: str, rel: str, *args: str, timeout: float = 120) -> dict:
    pkg = package_dir(home)
    if not pkg:
        return {"ok": False, "error": "xess-xmx-linux is not installed"}
    script = os.path.join(pkg, rel)
    if not os.path.exists(script):
        return {"ok": False, "error": f"{rel} missing in {pkg}"}
    r = userexec.run(["bash", script, *args], home, timeout=timeout, cwd=pkg)
    return {"ok": r["rc"] == 0, "log": r["out"]}


def uninstall(home: str) -> dict:
    return _tool(home, "uninstall.sh", timeout=300)


# ---- per game ------------------------------------------------------------------------------------------------------
STEAM = ".local/share/Steam"


def steam_install_dir(home: str, appid: str) -> str | None:
    """The game's install folder from Steam's own records (libraryfolders.vdf, appmanifest_<appid>.acf)."""
    if not str(appid).isdigit():
        return None
    libs = [os.path.join(home, STEAM)]
    for m in re.finditer(r'"path"\s+"([^"]+)"', sysfs.read(os.path.join(home, STEAM, "steamapps", "libraryfolders.vdf")) or ""):
        libs.append(m.group(1).replace("\\\\", "/"))
    for lib in libs:
        acf = sysfs.read(os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf"))
        m = re.search(r'"installdir"\s+"([^"]+)"', acf or "")
        if m:
            d = os.path.join(lib, "steamapps", "common", m.group(1))
            if os.path.isdir(d):
                return d
    return None


def optiscaler_in(home: str, appid: str) -> dict:
    """Is OptiScaler (Bazzite's Decky Framegen, or by hand) in this game? Read-only: ClawDeck never puts it there.
    Its bundled XeSS libraries run on XMX through the same shim as a game's own."""
    d = steam_install_dir(home, appid)
    if not d:
        return {"present": False, "dir": None}
    for root, dirs, files in os.walk(d):
        if root.count(os.sep) - d.count(os.sep) > 2:
            dirs[:] = []
            continue
        if "OptiScaler.ini" in files or "OptiScaler.dll" in files:
            m = re.search(r"OptiScaler\s+(v[\d.]+[^\s\]]*)", sysfs.read(os.path.join(root, "OptiScaler.log")) or "")
            return {"present": True, "dir": root, "version": m.group(1) if m else None,
                    "xess_fg": bool(re.search(r"^\s*FGOutput\s*=\s*xefg", sysfs.read(os.path.join(root, "OptiScaler.ini")) or "", re.M | re.I))}
    return {"present": False, "dir": d}

def stock_env() -> dict[str, str]:
    """Environment that puts one game back on the system's Vulkan drivers (XeSS XMX off for that game): the stock
    ICD list instead of the package's, and vkd3d-proton's extensions as shipped."""
    icds = []
    for d in STOCK_ICD_DIRS:
        icds += sorted(glob.glob(os.path.join(d, "*.json")))
    return {"VK_DRIVER_FILES": ":".join(icds), "VKD3D_DISABLE_EXTENSIONS": ""}


def prefix_dir(home: str, appid: str) -> str:
    return os.path.join(home, COMPATDATA, str(appid), "pfx", "drive_c")


def game_xell(home: str, appid: str, xell_on: bool) -> dict:
    """What XeLL did in this game's last launch, from the shim's logs in the prefix (the launch wrapper rotates them
    at each start, so they describe one launch). mode: driver | cross-vendor | none | off."""
    pfx = prefix_dir(home, appid)
    trace = sysfs.read(os.path.join(pfx, "igdext_trace.log")) or ""
    xlog = sysfs.read(os.path.join(pfx, "igdext_xell.log")) or ""
    shim = "XELLPROBE" in trace or "device info" in trace or bool(xlog)
    if "LatencyReductionExt(" in trace or "GetRenderSubmitTimingsBuffers" in trace or "RenderSubmitStart(" in trace:
        return {"mode": "driver", "shim": True, "detail": "XeLL driver mode active: the game paces frames with GPU timings from the shim"}
    if "xellSetSleepMode" in xlog or "LatencyReduction" in trace:
        return {"mode": "cross-vendor", "shim": shim,
                "detail": "XeLL is used, in its built-in cross-vendor mode" + ("" if xell_on else " (turn XeLL driver mode on for this game)")}
    if not xell_on:
        return {"mode": "off", "shim": shim, "detail": "XeLL driver mode is off for this game"}
    return {"mode": "none", "shim": shim,
            "detail": "no XeLL activity logged yet: the game needs XeSS frame generation on, and the shim loaded (restart the game once after installing)"}
