"""ClawDeck's own OptiScaler: download once, put into a game with its settings, take out again exactly.

What OptiClick does on Windows, and Decky Framegen on SteamOS, done by ClawDeck so one tap covers everything the
recommended setup needs (FSR 4 on XMX, REFramework, the game's fixes from assets/games.json) and the OptiScaler
version is ClawDeck's to choose:

  install()  OptiScaler's release .7z (pinned in assets/versions.json with its sha256) unpacked into
             ~/.local/share/clawdeck/optiscaler/<version>. Optional extras with their own sha256 (OptiPatcher).
  patch()    into the folder of the game's main exe: OptiScaler.dll under a proxy name (dxgi.dll, or the next free
             one: winmm, version, dbghelp, winhttp; OptiClick's order), OptiScaler.ini with the game's settings, the
             libraries it ships, OptiPatcher in plugins/, and for "FSR 4 on XMX" nitec's files from ~/fsr4-xmx. Any
             file it replaces is moved to .clawdeck-optiscaler/backup first; everything is listed in
             .clawdeck-optiscaler/manifest.json. Proton loads the proxy through WINEDLLOVERRIDES=<name>=n,b (the
             launch wrapper sets it from the game's launch file).
  unpatch()  removes what the manifest lists and puts the backups back; a file someone changed since is kept.

OptiScaler from another tool (Decky Framegen's fgmod, OptiClick's files from a Windows install, by hand) is never
touched: owner() says whose it is and the panel shows a warning instead.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import subprocess
import urllib.error

from . import downloads, userexec

log = logging.getLogger("clawdeck.optiscaler")

ROOT = ".local/share/clawdeck/optiscaler"
STATE = ".clawdeck-optiscaler"
PROXIES = ("dxgi.dll", "winmm.dll", "version.dll", "dbghelp.dll", "winhttp.dll")
FSR4_XMX_DIR = "fsr4-xmx"
# OptiScaler's package, minus what only Windows installs need
SKIP = {"setup_linux.sh", "setup_windows.bat", "OptiScaler.dll", "OptiScaler.ini"}
SKIP_PREFIX = ("!!", "Licenses/")
# always: OptiPatcher (plugins/) loads, and the HQ font stays off (its auto mode asserts on a missing font under
# Proton; Decky Framegen sets the same two)
BASE_INI = {"Plugins_LoadAsiPlugins": "true", "Menu_UseHQFont": "false"}
MAIN_EXE_SKIP = re.compile(r"(unins|crash|setup|redist|launcher|vc_?redist|dxsetup|easyanticheat|eac|ue4prereq|cefprocess|helper|report)", re.I)


# ---- the package ------------------------------------------------------------------------------------------------
def package_dir(home: str, version: str) -> str:
    return os.path.join(home, ROOT, version)


def installed(home: str, version: str) -> bool:
    d = package_dir(home, version)
    return os.path.isfile(os.path.join(d, "OptiScaler.dll")) and os.path.isfile(os.path.join(d, "OptiScaler.ini"))


def _get(url: str, sha256: str, dest: str) -> str:
    downloads.download(url, dest)
    if downloads.sha256_of(dest) != sha256.lower():
        os.remove(dest)
        raise ValueError(f"{os.path.basename(dest)} does not match its pinned sha256; not installed")
    return dest


def install(home: str, entry: dict) -> dict:
    """entry (versions.json "optiscaler"): {version, url, sha256, extras: [{name, url, sha256, dest}]}."""
    ver = entry["version"]
    d = package_dir(home, ver)
    if installed(home, ver) and all(os.path.isfile(os.path.join(d, x["dest"])) for x in entry.get("extras", [])):
        return {"ok": True, "already": True, "version": ver, "dir": d}
    work = os.path.join(home, ROOT, ".download")
    try:
        os.makedirs(work, exist_ok=True)
        arc = _get(entry["url"], entry["sha256"], os.path.join(work, os.path.basename(entry["url"])))
        tmp = d + ".new"
        shutil.rmtree(tmp, ignore_errors=True)
        r = subprocess.run(["7z", "x", "-y", "-o" + tmp, arc], capture_output=True, text=True, timeout=300, check=False,
                           env={**os.environ, "LD_LIBRARY_PATH": ""})
        if r.returncode != 0:
            return {"ok": False, "error": f"unpacking OptiScaler: {(r.stderr or r.stdout).strip()[-300:]}"}
        if not os.path.isfile(os.path.join(tmp, "OptiScaler.dll")):
            return {"ok": False, "error": "the OptiScaler archive has no OptiScaler.dll"}
        for x in entry.get("extras", []):
            dest = os.path.join(tmp, x["dest"])
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            _get(x["url"], x["sha256"], dest)
        shutil.rmtree(d, ignore_errors=True)
        os.replace(tmp, d)
        userexec.chown_tree(os.path.join(home, ROOT), home)
    except (OSError, ValueError, subprocess.SubprocessError, urllib.error.URLError) as e:
        return {"ok": False, "error": f"OptiScaler {ver}: {e}"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log.info("OptiScaler %s into %s", ver, d)
    return {"ok": True, "version": ver, "dir": d}


# ---- the game ---------------------------------------------------------------------------------------------------
def main_exe_dir(game_dir: str, known_exes: list[str] | None = None) -> str | None:
    """Where OptiScaler goes: the folder of the game's real exe. Unreal: <Project>/Binaries/Win64 (the -Shipping exe);
    a known exe name from the game list; else the largest exe that isn't a launcher, installer or crash reporter."""
    best, size = None, -1
    want = {e.lower() for e in known_exes or []}
    base = game_dir.rstrip(os.sep).count(os.sep)
    for r, d, f in os.walk(game_dir):
        if r.count(os.sep) - base >= 5:
            d[:] = []
        for x in f:
            lo = x.lower()
            if not lo.endswith(".exe"):
                continue
            if lo.endswith(("-win64-shipping.exe", "-wingdk-shipping.exe")) or lo in want:
                return r
            if MAIN_EXE_SKIP.search(lo):
                continue
            try:
                s = os.path.getsize(os.path.join(r, x))
            except OSError:
                continue
            if s > size:
                best, size = r, s
    return best


def _manifest(folder: str) -> dict | None:
    try:
        with open(os.path.join(folder, STATE, "manifest.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def owner(folder: str | None) -> dict:
    """Whose OptiScaler is in this folder: "clawdeck", "decky-framegen", "other" (by hand, OptiClick's files from a
    Windows install, another tool) or None."""
    if not folder or not os.path.isdir(folder):
        return {"owner": None}
    m = _manifest(folder)
    if m:
        return {"owner": "clawdeck", "version": m.get("version"), "dll": m.get("dll"), "preset": m.get("preset")}
    names = {x.lower() for x in os.listdir(folder)}
    if "framegen_patch" in names or any(n.endswith(".b") for n in names if n.startswith(PROXIES)):
        return {"owner": "decky-framegen"}
    if "optiscaler.ini" in names or "optiscaler.log" in names or "optiscaler.dll" in names:
        return {"owner": "other"}
    return {"owner": None}


def set_ini(text: str, values: dict[str, str]) -> str:
    """OptiScaler.ini with [Section] Key=value set for each "Section_Key" (sections are matched without - and .,
    as Decky Framegen does: V-Sync = VSync). A key missing from its section is added at the section's end; a missing
    section at the end of the file."""
    lines = text.splitlines()
    for sk, v in values.items():
        sec, key = sk.split("_", 1)
        cur, start, end, done = None, None, None, False
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                if cur == sec:
                    end = i
                    break
                cur = s[1:-1].replace("-", "").replace(".", "")
                if cur == sec:
                    start = i
                continue
            if cur == sec and re.match(rf"^\s*{re.escape(key)}\s*=", line):
                lines[i] = f"{key}={v}"
                done = True
                break
        if done:
            continue
        if start is None:
            lines += ["", f"[{sec}]", f"{key}={v}"]
        else:
            lines.insert(end if end is not None else len(lines), f"{key}={v}")
    return "\n".join(lines) + "\n"


def _put(src: str, dest: str, folder: str, added: list, backed: list) -> None:
    rel = os.path.relpath(dest, folder)
    if os.path.lexists(dest) and rel not in added:
        bak = os.path.join(folder, STATE, "backup", rel)
        os.makedirs(os.path.dirname(bak), exist_ok=True)
        if not os.path.exists(bak):
            os.replace(dest, bak)
            backed.append(rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(src, dest)
    if rel not in added:
        added.append(rel)


def _proton_cabinet(home: str) -> str | None:
    for p in sorted(glob.glob(os.path.join(home, ".local/share/Steam/steamapps/common/Proton*/files/lib/wine/x86_64-windows/cabinet.dll")), reverse=True):
        return p
    return None


def patch(home: str, folder: str, version: str, ini: dict[str, str], preset: str = "xess",
          fsr4_xmx: bool = False) -> dict:
    """Put ClawDeck's OptiScaler into `folder` (the game's exe folder). Patching again (another preset) rewrites
    the ini and the FSR 4 files; the proxy name and backups stay."""
    pkg = package_dir(home, version)
    if not installed(home, version):
        return {"ok": False, "error": f"OptiScaler {version} isn't downloaded yet"}
    who = owner(folder)
    if who["owner"] not in (None, "clawdeck"):
        return {"ok": False, "owner": who["owner"],
                "error": "this game already has OptiScaler from " + ("Decky Framegen" if who["owner"] == "decky-framegen" else "another tool or by hand")
                         + "; remove that first (ClawDeck never overwrites it)"}
    m = _manifest(folder) or {"version": version, "dll": None, "added": [], "backed": []}
    added, backed = m["added"], m["backed"]
    try:
        dll = m.get("dll")
        if not dll:
            for name in PROXIES:
                p = os.path.join(folder, name)
                if not os.path.exists(p):
                    dll = name
                    break
            if not dll:
                return {"ok": False, "error": f"every proxy name ({', '.join(PROXIES)}) is taken in {folder}"}
        os.makedirs(os.path.join(folder, STATE), exist_ok=True)
        _put(os.path.join(pkg, "OptiScaler.dll"), os.path.join(folder, dll), folder, added, backed)
        for r, d, f in os.walk(pkg):
            for x in f:
                rel = os.path.relpath(os.path.join(r, x), pkg).replace(os.sep, "/")
                if rel in SKIP or rel.startswith(SKIP_PREFIX):
                    continue
                if fsr4_xmx and rel == "amd_fidelityfx_upscaler_dx12.dll":
                    continue
                _put(os.path.join(r, x), os.path.join(folder, rel), folder, added, backed)
        # FSR 4 on XMX: nitec's upscaler instead of AMD's, with what it needs; or AMD's back when switched away
        xdir = os.path.join(home, FSR4_XMX_DIR)
        extra = ("fsr-original.dll", "cabinet.dll", "cabinet_wine.dll")
        if fsr4_xmx:
            need = [f for f in ("amd_fidelityfx_upscaler_dx12.dll", "fsr-original.dll", "cabinet.dll") if not os.path.isfile(os.path.join(xdir, f))]
            if need:
                return {"ok": False, "error": f"FSR 4 on XMX needs {', '.join(need)} in {xdir}"}
            for f in ("amd_fidelityfx_upscaler_dx12.dll", "fsr-original.dll", "cabinet.dll"):
                _put(os.path.join(xdir, f), os.path.join(folder, f), folder, added, backed)
            wcab = os.path.join(xdir, "cabinet_wine.dll")
            wcab = wcab if os.path.isfile(wcab) else _proton_cabinet(home)
            if wcab:
                _put(wcab, os.path.join(folder, "cabinet_wine.dll"), folder, added, backed)
        else:
            for f in extra:
                if f in added and os.path.exists(os.path.join(folder, f)):
                    os.remove(os.path.join(folder, f))
                    added.remove(f)
        ini_text = open(os.path.join(pkg, "OptiScaler.ini"), "r", encoding="utf-8", errors="replace").read()
        ini_path = os.path.join(folder, "OptiScaler.ini")
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(set_ini(ini_text, {**BASE_INI, **ini}))
        if "OptiScaler.ini" not in added:
            added.append("OptiScaler.ini")
        # low latency always on: the game's Reflex becomes XeLL through fakenvapi, forced on even where the game's
        # menu leaves Reflex off (xess-xmx-linux's shim then runs XeLL on the frames)
        fk = os.path.join(folder, "fakenvapi.ini")
        if os.path.isfile(fk) and "fakenvapi.ini" in added:
            t = open(fk, "r", encoding="utf-8", errors="replace").read()
            want = "2"                                 # always: XeLL in every game (maintainer, 2026-11-04)
            t = re.sub(r"(?m)^force_reflex=.*$", f"force_reflex={want}", t) if re.search(r"(?m)^force_reflex=", t) else t + f"\nforce_reflex={want}\n"
            with open(fk, "w", encoding="utf-8") as f:
                f.write(t)
        m.update({"version": version, "dll": dll, "preset": preset, "ini": ini, "added": added, "backed": backed})
        with open(os.path.join(folder, STATE, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(m, f, indent=1)
        for rel in added + [STATE]:
            p = os.path.join(folder, rel)
            if os.path.exists(p):
                userexec.chown_tree(p, home)
    except OSError as e:
        return {"ok": False, "error": f"putting OptiScaler into {folder}: {e}"}
    log.info("OptiScaler %s into %s as %s (%s)", version, folder, dll, preset)
    return {"ok": True, "dll": dll, "dll_override": dll[:-4], "folder": folder, "version": version}


def unpatch(folder: str | None) -> dict:
    m = _manifest(folder) if folder else None
    if not m:
        return {"ok": True, "removed": False}
    errors = []
    for rel in m.get("added", []):
        try:
            p = os.path.join(folder, rel)
            if os.path.isfile(p):
                os.remove(p)
        except OSError as e:
            errors.append(f"{rel}: {e}")
    for rel in m.get("backed", []):
        try:
            os.replace(os.path.join(folder, STATE, "backup", rel), os.path.join(folder, rel))
        except OSError as e:
            errors.append(f"restoring {rel}: {e}")
    for d in ("D3D12_Optiscaler", "plugins"):
        try:
            os.rmdir(os.path.join(folder, d))                 # only when empty: the game may have its own plugins/
        except OSError:
            pass
    if not errors:
        shutil.rmtree(os.path.join(folder, STATE), ignore_errors=True)
    return {"ok": not errors, "removed": True, "error": "; ".join(errors) or None}
