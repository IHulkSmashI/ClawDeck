"""XeSS driver: install, update and check xess-xmx-linux (github.com/HHHHanasak1/xess-xmx-linux), which runs Intel
XeSS super resolution and frame generation on the XMX cores under Proton.

What the package does (its README): install.sh writes ~/.config/environment.d/50-xess-xmx.conf pointing
VK_DRIVER_FILES at <package>/run/intel_icd.json, puts its igdext64.dll shim into every Proton and prefix, and
enables xess-xmx-check.service, which before each session writes ~/.cache/xess-xmx/driver-status ("ok <date> <lib>
<driverInfo>" or "FAILED <date> <lib>: <reason>"). The package carries patched drivers for two Mesa versions in
lib/mesa-<version>/; lib/active names the default.

Low latency with frame generation: from release 20261104 the package's shim times each frame so it reaches the GPU
when the GPU gets free (on by default while XeSS frame generation runs; IGDEXT_XELL_CAP=off turns it off). With
IGDEXT_XELL_LOG=1 (the launch wrapper sets it) the shim logs what it did into the game's prefix
(drive_c/igdext_xell.log): "low latency: on", "auto: frame generation on", "auto: waited in N of 100 frames ...".
game_latency() reads that per launch. The package's per-game switches (XESS_FG, FSR=xess, FSR_FG, XESS=latest) are
environment variables its Proton hook reads, so they reach a game through ClawDeck's launch wrapper (launchenv.py).

ClawDeck reads that state, downloads a release into ~/.local/share/clawdeck (the package must stay where it was
unpacked) and runs its install.sh / uninstall.sh as the desktop user (never as root). Any XeSS library loaded in a
prefix goes through the shim, so OptiScaler's bundled libxess / libxess_fg / libxell (ClawDeck's own OptiScaler,
or Decky Framegen's, puts them next to a game) run on XMX too; optiscaler_in() tells the Per-game panel when that is the case.
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
# the first release whose shim has the low latency with frame generation and the FSR-on-XeSS route
LOW_LATENCY_SINCE = "20261104"


def package_dir(home: str) -> str | None:
    """The installed package, from its driver variables: the session file install.sh writes, or, once ClawDeck has
    made XMX per game (xmxscope.py), the copy ClawDeck keeps."""
    from . import xmxscope
    txt = "\n".join(sysfs.read(os.path.join(home, f)) or "" for f in
                    (ENV_CONF, xmxscope.XMX_ENV, xmxscope.SESSION_CONF + xmxscope.PARKED_SUFFIX))
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
        # releases from 20261104 on: low latency with frame generation, FSR=xess, XESS_FG up to 8x
        "low_latency": bool(pkg) and has_low_latency(pkg, name),
        "official_proton": official_proton(home),
        "scope": _scope(home) if pkg else None,          # per game or session-wide, and which Protons are covered
    }


def _scope(home: str) -> dict:
    from . import xmxscope
    try:
        return xmxscope.state(home)
    except OSError as e:
        return {"error": str(e)}


def has_low_latency(pkg: str, name: str) -> bool:
    """Does this package have the low latency with frame generation? Its launch-options doc names IGDEXT_XELL_CAP
    from 20261104 on; a package without docs is judged by the date in its folder name."""
    doc = sysfs.read(os.path.join(pkg, "docs", "launch-options.md"))
    if doc is not None:
        return "IGDEXT_XELL_CAP" in doc
    m = re.search(r"(\d{8})", name)
    return bool(m) and m.group(1) >= LOW_LATENCY_SINCE


def _latest(entry: dict) -> tuple[str, dict, str | None]:
    """(repo, release, note) for the pinned tag (or the latest, when the user allowed an untested one)."""
    return entry["repo"], downloads.release(entry["repo"], entry.get("tag")), None


def session_check(home: str, pkg: str | None = None) -> dict:
    """Run the package's own driver check now (tools/session-check.sh, what xess-xmx-check.service runs before each
    session), so Setup's line is right straight after an install or update instead of after the next log-in
    (findings §20). --dry-run: it loads the driver, enumerates the GPU and writes driver-status, but leaves the
    running session's environment alone; switching drivers on a failure stays the log-in unit's job."""
    pkg = pkg or package_dir(home)
    if not pkg:
        return {"ok": False, "ran": False, "error": "xess-xmx-linux is not installed", "status": parse_status(None)}
    script = os.path.join(pkg, "tools", "session-check.sh")
    if not os.path.exists(script):
        return {"ok": False, "ran": False, "error": f"no tools/session-check.sh in {pkg}",
                "status": parse_status(sysfs.read(os.path.join(home, STATUS)))}
    r = userexec.run(["bash", script, "--dry-run"], home, timeout=120, cwd=pkg)
    st = parse_status(sysfs.read(os.path.join(home, STATUS)))
    log.info("XeSS XMX driver check (exit %s): %s", r["rc"], st.get("line"))
    return {"ok": st["state"] == "ok", "ran": True, "status": st, "rc": r["rc"], "log": r["out"]}


def install(home: str, entry: dict, allow_unpinned: bool = False) -> dict:
    """Install, or update to the newest release. When the installed package already is that release, nothing is
    downloaded or re-run ("up to date"; re-unpacking would also overwrite the driver the session has loaded). Either
    way the driver check runs at the end, so the status line is current."""
    root = os.path.join(home, INSTALL_ROOT)
    try:
        repo, rel, note = _latest(entry)
        tag = rel.get("tag_name", "unknown")
        pkg_now = package_dir(home)
        have = source_of(pkg_now)
        if pkg_now and os.path.isdir(pkg_now) and have == {"repo": repo, "tag": tag}:
            chk = session_check(home, pkg_now)
            return {"ok": True, "up_to_date": True, "tag": tag, "repo": repo, "package_dir": pkg_now, "note": note,
                    "check": chk, "next": None, "summary": f"XeSS XMX {tag} is up to date"}
        got = downloads.fetch({**entry, "repo": repo}, root, allow_unpinned, rel=rel)
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
           "next": "XeSS on XMX is ready: it loads in games that ship XeSS (or OptiScaler), never for Steam's own screen"}
    if r["rc"] != 0:
        tail = [ln for ln in r["out"].strip().splitlines() if ln.strip()][-3:]
        out["error"] = f"install.sh exited {r['rc']}: " + (" · ".join(tail) or "no output")
        return out
    out["check"] = chk = session_check(home, pkg)
    from . import xmxscope       # per game, never the whole session (xmxscope.py says why)
    iso = xmxscope.isolate_session(home)
    sc = xmxscope.install_scope(home) if iso.get("ok") else {"ok": False, "error": iso.get("error")}
    out["scope"] = {"isolated": iso, "scope": sc}
    if not (iso.get("ok") and sc.get("ok")):
        out["next"] = f"installed, but per-game XMX could not be set up: {iso.get('error') or sc.get('error')}"
    if chk["ran"] and not chk["ok"]:
        out["next"] = f"installed, but the driver check failed: {chk['status'].get('line') or chk.get('log', '')[-200:]}"
    return out


def _find_package(d: str) -> str | None:
    for root, dirs, files in os.walk(d):
        if "install.sh" in files and "uninstall.sh" in files:
            return root
        if root.count(os.sep) - d.count(os.sep) > 2:
            dirs[:] = []
    return None


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


def steam_app_name(home: str, appid: str) -> str | None:
    """The game's name from its appmanifest, as Steam shows it."""
    if not str(appid).isdigit():
        return None
    libs = [os.path.join(home, STEAM)]
    for m in re.finditer(r'"path"\s+"([^"]+)"', sysfs.read(os.path.join(home, STEAM, "steamapps", "libraryfolders.vdf")) or ""):
        libs.append(m.group(1).replace("\\\\", "/"))
    for lib in libs:
        m = re.search(r'"name"\s+"([^"]+)"', sysfs.read(os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf")) or "")
        if m:
            return m.group(1)
    return None


TOOLS = re.compile(r"^(Proton|Steam Linux Runtime|Steamworks Common Redistributables|SteamVR)", re.I)


def installed_games(home: str) -> list[dict]:
    """Every installed Steam game: appid, name, install folder (Proton, runtimes and redistributables left out).
    Read from Steam's appmanifest files, the same records Steam uses."""
    libs = [os.path.join(home, STEAM)]
    for m in re.finditer(r'"path"\s+"([^"]+)"', sysfs.read(os.path.join(home, STEAM, "steamapps", "libraryfolders.vdf")) or ""):
        libs.append(m.group(1).replace("\\\\", "/"))
    out, seen = [], set()
    for lib in dict.fromkeys(os.path.realpath(x) for x in libs):
        for acf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
            t = sysfs.read(acf) or ""
            a, n, d = (re.search(rf'"{k}"\s+"([^"]*)"', t) for k in ("appid", "name", "installdir"))
            if not (a and n and d) or a.group(1) in seen or TOOLS.match(n.group(1)):
                continue
            folder = os.path.join(lib, "steamapps", "common", d.group(1))
            if os.path.isdir(folder):
                seen.add(a.group(1))
                out.append({"appid": a.group(1), "name": n.group(1), "dir": folder})
    return sorted(out, key=lambda g: g["name"].lower())


def optiscaler_in(home: str, appid: str) -> dict:
    """Is OptiScaler (ClawDeck's own, Decky Framegen's, or by hand) in this game? Read-only: this only looks;
    optiscaler.py puts ClawDeck's in and owner() says whose it is.
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


def xmx_proof(home: str, appid: str, runtime_dir: str | None = None) -> dict:
    """Whether the game's last launch really ran on the XMX cores, from what the patched driver and the shim leave in
    the prefix: anv_canary (written by the patched driver when it recognises the shim's self-test, so only when it
    was loaded in the game), the kernels the game asked for (map.txt) and the failed-compile marker. 2026-11-04: per
    game XMX had silently left every game on the stock driver; this is the line that shows it."""
    k = os.path.join(prefix_dir(home, appid), "igdext_kernels")
    canary = os.path.join(k, "anv_canary")
    failed = os.path.isfile(os.path.join(k, "compile_failed.txt"))
    try:
        when = os.path.getmtime(canary)
    except OSError:
        when = None
    try:
        with open(os.path.join(k, "map.txt"), "r", encoding="utf-8", errors="replace") as f:
            kernels = sum(1 for line in f if line.strip())
    except OSError:
        kernels = 0
    # the game's newest start Steam knows of: the prefix's registry is written at every launch
    # the game's last start: the launch wrapper touches <runtime>/launch/<appid>.started (no wrapper: unknown)
    try:
        last = os.path.getmtime(os.path.join(runtime_dir, "launch", f"{appid}.started")) if runtime_dir else None
    except OSError:
        last = None
    ran = when is not None and (last is None or when >= last - 5)
    state = "failed" if failed else "ok" if ran else "stale" if when else "never"
    return {"state": state, "at": when, "last_launch": last, "kernels": kernels, "failed": failed}


def game_latency(home: str, appid: str, low_latency_on: bool) -> dict:
    """What the shim's low latency did in this game's last launch, from drive_c/igdext_xell.log (the launch wrapper
    rotates it at each start, so it describes one launch). mode: pacing | idle | no-fg | off | none."""
    xlog = sysfs.read(os.path.join(prefix_dir(home, appid), "igdext_xell.log")) or ""
    lat0 = [float(x) for x in re.findall(r"xell latency: simulation start -> present end avg ([\d.]+) ms", xlog)]
    if not low_latency_on:
        return {"mode": "off", "latency_ms": round(sum(lat0) / len(lat0), 1) if lat0 else None,
                "detail": "Turned off for this game: frames may queue in front of the GPU (more input lag)"
                          + (f". Input-to-screen latency {sum(lat0) / len(lat0):.0f} ms on average in the last launch." if lat0 else "")}
    if not xlog:
        return {"mode": "none", "detail": "Nothing logged yet: start the game through ClawDeck (launch options) and turn on XeSS frame generation in it"}
    state = re.findall(r"low latency: ([^\r\n]+)", xlog)
    if state and not state[-1].startswith("on"):
        return {"mode": "off", "detail": f"The shim reports low latency {state[-1]}"}
    fg = re.findall(r"auto: frame generation (on|off)", xlog)
    if not fg or fg[-1] == "off":
        return {"mode": "no-fg", "detail": "Frame generation is off in this game, so there is nothing to pace (low latency works only with XeSS frame generation)"}
    lat = [float(x) for x in re.findall(r"xell latency: simulation start -> present end avg ([\d.]+) ms", xlog)]
    # the latency XeLL itself measures (simulation start -> present end), averaged over the launch: what "no added
    # latency" is checked against (xess-xmx-linux's docs/performance.md measures it the same way)
    avg = f" Input-to-screen latency {sum(lat) / len(lat):.0f} ms on average ({len(lat) * 100} frames)." if lat else ""
    waits = re.findall(r"auto: waited in (\d+) of 100 frames, ([\d.]+) ms each", xlog)
    if waits and int(waits[-1][0]) > 0:
        n, ms = waits[-1]
        return {"mode": "pacing", "latency_ms": round(sum(lat) / len(lat), 1) if lat else None,
                "detail": f"Holding frames back so they don't queue: waited in {n} of the last 100 frames, {ms} ms each.{avg}"}
    return {"mode": "idle", "latency_ms": round(sum(lat) / len(lat), 1) if lat else None,
            "detail": f"On, and nothing to do: this game doesn't queue frames, so there is no lag to remove.{avg}"}
