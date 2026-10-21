"""XeSS driver: install, update and check xess-xmx-linux (github.com/HHHHanasak1/xess-xmx-linux; our fork
IHulkSmashI/xess-xmx-linux), which runs Intel XeSS super resolution and frame generation on the XMX cores under
Proton.

What the package does (its README): install.sh writes ~/.config/environment.d/50-xess-xmx.conf pointing
VK_DRIVER_FILES at <package>/run/intel_icd.json, puts its igdext64.dll shim into every Proton and prefix, and
enables xess-xmx-check.service, which before each session writes ~/.cache/xess-xmx/driver-status ("ok <date> <lib>
<driverInfo>" or "FAILED <date> <lib>: <reason>"). The package carries patched drivers for two Mesa versions in
lib/mesa-<version>/; lib/active names the default; tools/switch-driver.sh changes it; MESA=26.1 per game overrides.

ClawDeck reads that state, downloads a release into ~/.local/share/clawdeck (the package must stay where it was
unpacked), runs its install.sh / tools as the desktop user (never as root), and probes with its own session check.
Intel's official path (a built-in libxess.dll in Proton's Wine, Proton PR 10289) is detected so the UI can say when
a Proton build carries it; until then the shim is the working route.
"""
from __future__ import annotations

import glob
import logging
import os

from . import downloads, sysfs, userexec

log = logging.getLogger("clawdeck.xess")

ENV_CONF = ".config/environment.d/50-xess-xmx.conf"
STATUS = ".cache/xess-xmx/driver-status"
INSTALL_ROOT = ".local/share/clawdeck"


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


def state(home: str) -> dict:
    pkg = package_dir(home)
    st = parse_status(sysfs.read(os.path.join(home, STATUS)))
    drivers, active = [], None
    if pkg:
        drivers = sorted(os.path.basename(d)[5:] for d in glob.glob(os.path.join(pkg, "lib", "mesa-*"))
                         if os.path.isfile(os.path.join(d, "libvulkan_intel.so")))
        active = sysfs.read(os.path.join(pkg, "lib", "active"))
    name = os.path.basename(pkg) if pkg else ""
    return {
        "installed": pkg is not None,
        "package_dir": pkg,
        "version": name[len("xess-xmx-linux-"):] if name.startswith("xess-xmx-linux-") else (name or None),
        "drivers": drivers,
        "default_driver": active,
        "status": st,
        "official_proton": official_proton(home),
    }


def install(home: str, entry: dict, allow_unpinned: bool = False, dry_run: bool = False) -> dict:
    root = os.path.join(home, INSTALL_ROOT)
    if dry_run:
        return {"ok": True, "dry_run": True, "would": f"download {entry.get('fork') or entry['repo']} {entry.get('tag')} into {root}, run install.sh as the user"}
    got = downloads.fetch(entry, root, allow_unpinned)
    pkg = _find_package(got["dir"])
    if not pkg:
        return {"ok": False, "error": "install.sh not found in the release archive", **got}
    userexec.chown_tree(root, home)
    r = userexec.run(["bash", os.path.join(pkg, "install.sh")], home, timeout=900, cwd=pkg)
    return {"ok": r["rc"] == 0, "tag": got["tag"], "package_dir": pkg, "log": r["out"],
            "next": "log out of Game Mode and back in (or reboot) so the session picks up the driver"}


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


def probe(home: str) -> dict:
    """The package's own pre-session check, in dry-run mode: does the patched driver load and find the GPU?"""
    r = _tool(home, "tools/session-check.sh", "--dry-run")
    r["state"] = state(home)
    return r


def switch_driver(home: str, version: str) -> dict:
    if not version.replace(".", "").replace("-", "").replace("rc", "").isalnum():
        raise ValueError("bad driver version")
    return _tool(home, "tools/switch-driver.sh", version)


def uninstall(home: str) -> dict:
    return _tool(home, "uninstall.sh", timeout=300)
