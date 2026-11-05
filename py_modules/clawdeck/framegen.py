"""Decky Framegen (github.com/xXJSONDeruloXx/Decky-Framegen) compatibility. ClawDeck installs its own pinned
OptiScaler per game (optiscaler.py) and no longer offers Decky Framegen; this module only says whether Decky
Framegen is installed and its ~/fgmod is set up (state), and whether a game's launch options run its fgmod
(uses_fgmod). Such a game is Decky Framegen's: the panel shows a warning and "Stop using Decky Framegen here", and
ClawDeck's wrapper still goes in front of fgmod ("clawdeck-launch.sh ~/fgmod/fgmod %command%") so the presets are in
the environment when fgmod reads it.

install() (its release zip into Decky's plugin folder, never overwriting) is kept for the backend and its test; no
panel calls it.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import shutil
import subprocess
import urllib.error

from . import downloads, sysfs

log = logging.getLogger("clawdeck.framegen")

NAME = "Decky-Framegen"
PLUGINS = "homebrew/plugins"
FGMOD = "fgmod"


def plugin_dir(home: str) -> str | None:
    """Decky Framegen's folder in Decky's plugins, found by the name in its plugin.json (the folder name varies:
    "Decky-Framegen", "Decky Framegen", a store install's)."""
    for pj in sorted(glob.glob(os.path.join(home, PLUGINS, "*", "plugin.json"))):
        try:
            with open(pj, "r", encoding="utf-8") as f:
                if json.load(f).get("name") == NAME:
                    return os.path.dirname(pj)
        except (OSError, ValueError):
            continue
    return None


def _version_of(d: str) -> str | None:
    try:
        with open(os.path.join(d, "package.json"), "r", encoding="utf-8") as f:
            return json.load(f).get("version")
    except (OSError, ValueError):
        return None


def optiscaler_version(home: str) -> str | None:
    fg = os.path.join(home, FGMOD)
    try:
        with open(os.path.join(fg, "install-manifest.json"), "r", encoding="utf-8") as f:
            v = (json.load(f).get("optiscaler") or {}).get("version")
            if v:
                return str(v)
    except (OSError, ValueError, AttributeError):
        pass
    return (sysfs.read(os.path.join(fg, "version.txt")) or "").strip() or None


def state(home: str) -> dict:
    d = plugin_dir(home)
    fgmod = os.path.join(home, FGMOD, "fgmod")
    ready = os.path.isfile(fgmod) and os.access(fgmod, os.X_OK)
    return {
        "installed": d is not None,
        "dir": d,
        "version": _version_of(d) if d else None,
        "fgmod_ready": ready,                          # "Setup OptiScaler Mod" done in Decky Framegen
        "fgmod": fgmod if ready else None,
        "optiscaler": optiscaler_version(home) if ready else None,
    }


def uses_fgmod(launch_options: str | None) -> bool:
    return bool(launch_options) and "fgmod/fgmod" in launch_options


def _restart_decky() -> bool:
    """Restart Decky Loader a few seconds from now (it loads new plugins at start). Scheduled through systemd so
    this call can answer first: the restart ends ClawDeck's own backend too."""
    for cmd in (["systemd-run", "--on-active=3", "--unit=clawdeck-decky-restart", "systemctl", "restart", "plugin_loader"],):
        try:
            if subprocess.run(cmd, capture_output=True, timeout=10, env=sysfs.clean_env()).returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    return False


def install(home: str, entry: dict, allow_unpinned: bool = False) -> dict:
    """Install Decky Framegen into Decky's plugin folder from its release zip, when it isn't there. An installed one
    is left alone (Decky's own store or the Portal updates it)."""
    have = plugin_dir(home)
    if have:
        return {"ok": True, "already": True, "dir": have, "version": _version_of(have),
                "summary": f"Decky Framegen {_version_of(have) or ''} is installed"}
    plugins = os.path.join(home, PLUGINS)
    if not os.path.isdir(plugins):
        return {"ok": False, "error": f"Decky's plugin folder {plugins} is missing"}
    work = os.path.join(home, ".local", "share", "clawdeck", "framegen")
    try:
        got = downloads.fetch(entry, work, allow_unpinned)
    except PermissionError as e:
        return {"ok": False, "error": str(e)}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"download: {e.url} answered {e.code} {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": f"download: {type(e).__name__}: {e}"}
    src = None
    for pj in glob.glob(os.path.join(got["dir"], "**", "plugin.json"), recursive=True):
        try:
            with open(pj, "r", encoding="utf-8") as f:
                if json.load(f).get("name") == NAME:
                    src = os.path.dirname(pj)
                    break
        except (OSError, ValueError):
            continue
    if not src:
        return {"ok": False, "error": f"no Decky Framegen plugin.json in {os.path.basename(got['archive'])}"}
    dest = os.path.join(plugins, NAME)
    if os.path.lexists(dest):
        return {"ok": False, "error": f"{dest} exists but isn't a Decky Framegen plugin; not overwritten"}
    try:
        shutil.copytree(src, dest, symlinks=False)
    except OSError as e:
        return {"ok": False, "error": f"copying into {dest}: {e}"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    restarting = _restart_decky()
    log.info("Decky Framegen %s installed into %s; Decky restart scheduled: %s", got["tag"], dest, restarting)
    return {"ok": True, "dir": dest, "tag": got["tag"], "restarting": restarting,
            "next": ("Decky restarts in a few seconds. Then open Decky Framegen once and tap \"Setup OptiScaler Mod\"."
                     if restarting else "Restart Decky (or the Claw), then open Decky Framegen once and tap \"Setup OptiScaler Mod\".")}
