"""ClawDeck's own OptiScaler: download once, put into a game with its settings, take out again exactly.

What OptiClick does on Windows, and Decky Framegen on SteamOS, done by ClawDeck so one tap covers everything the
recommended setup needs (FSR 4 on XMX, REFramework, the game's fixes from assets/games.json) and the OptiScaler
version is ClawDeck's to choose:

  install()  OptiScaler's release .7z (pinned in assets/versions.json with its sha256) unpacked into
             ~/.local/share/clawdeck/optiscaler/<version>. Optional extras with their own sha256 (OptiPatcher).
  patch()    into the folder of the game's main exe: OptiScaler.dll under a proxy name (dxgi.dll, or the next free
             one: winmm, version, dbghelp, winhttp; OptiClick's order), OptiScaler.ini with the game's settings, the
             libraries it ships, OptiPatcher in plugins/, and for "FSR 4 on XMX" nitec's files from ~/fsr4-xmx with
             ClawDeck's own cabinet.dll (tools/cabinet-lzms, in the plugin's bin/: no Windows file needed). Any
             file it replaces is moved to .clawdeck-optiscaler/backup first; everything is listed in
             .clawdeck-optiscaler/manifest.json. Proton loads the proxy through WINEDLLOVERRIDES=<name>=n,b (the
             launch wrapper sets it from the game's launch file).
  unpatch()  removes what the manifest lists and puts the backups back; a file someone changed since is kept.

Two package layouts (versions.json "layout"):
  flat       OptiScaler 0.9.4 (every game but Capcom's): its libraries, D3D12_Optiscaler/ and plugins/ next to the exe.
  subfolder  OptiScaler v10 (Capcom RE Engine games, the way OptiClick installs them on Windows): only the proxy DLL
             and OptiScaler.ini next to the exe; the libraries, D3D12_OptiScaler/ and plugins/ in OptiScaler/
             (OptiDllPath=.\\OptiScaler). nitec's FSR 4.1.2 files go into OptiScaler/ too; ClawDeck's cabinet.dll
             (LZMS) and Wine's as cabinet_wine.dll stay next to the exe, where Wine loads them. The folders ClawDeck creates are listed in
             the manifest ("dirs"), so unpatch() takes them away with what OptiScaler wrote into them.

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
# logs and caches OptiScaler, fakenvapi and OptiPatcher create in the game folder at run time (not in the manifest)
RUNTIME_FILES = ("OptiScaler.log", "OptiScaler.log.*", "OptiScaler*.log", "fakenvapi.log", "OptiPatcher.log",
                 "dlssg_to_fsr3.log", "nvngx.log")
PROXIES = ("dxgi.dll", "winmm.dll", "version.dll", "dbghelp.dll", "winhttp.dll")
FSR4_XMX_DIR = "fsr4-xmx"
FSR4_XMX_FILES = ("amd_fidelityfx_upscaler_dx12.dll", "fsr-original.dll")   # what the user puts in ~/fsr4-xmx
# ClawDeck's own cabinet.dll (tools/cabinet-lzms, built by CI into the plugin's bin/): the Compression API LZMS
# decompressor nitec's build imports (Cabinet.dll ordinals 40, 43, 45), which Wine's cabinet.dll lacks. It replaces
# the copy of Windows' cabinet.dll the user had to provide; its FDI/FCI exports forward to cabinet_wine.dll.
OWN_CABINET = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))), "bin", "cabinet.dll")
# OptiScaler's package, minus what only Windows installs need
SKIP = {"setup_linux.sh", "setup_windows.bat", "OptiScaler.dll", "OptiScaler.ini"}
SKIP_PREFIX = ("!!", "Licenses/")
SKIP_SUFFIX = (".bat",)                  # v10: OptiScaler/Streamlined_fetcher_windows.bat (a Windows download helper)
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


def _unpack(arc: str, dest: str) -> str | None:
    """OptiScaler ships as .7z (with BCJ2): 7-Zip if the system has it (7z, 7za, 7zz), else libarchive's bsdtar.
    Returns an error text, or None when unpacked."""
    os.makedirs(dest, exist_ok=True)
    tried = []
    for cmd in (["7z", "x", "-y", "-o" + dest, arc], ["7za", "x", "-y", "-o" + dest, arc], ["7zz", "x", "-y", "-o" + dest, arc],
                ["bsdtar", "-xf", arc, "-C", dest]):
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False, env={**os.environ, "LD_LIBRARY_PATH": ""})
        except (OSError, subprocess.SubprocessError) as e:
            tried.append(f"{cmd[0]}: {e}")
            continue
        if r.returncode == 0:
            return None
        tried.append(f"{cmd[0]}: {(r.stderr or r.stdout).strip()[-200:]}")
    return "; ".join(tried) or "no unpacker for .7z on this system (7z or bsdtar)"


# fakenvapi.ini with OptiClick's values (its Windows Onimusha install, OptiScaler\fakenvapi.ini, 2026-11-04): the
# game's own Reflex setting decides (force_reflex=0), no LatencyFlex
FAKENVAPI_INI = ("[fakenvapi]\nenable_logs=1\nenable_trace_logs=0\nforce_latencyflex=0\nforce_reflex=0\n")
EXTRA_INIS = {"fakenvapi": FAKENVAPI_INI}


def extra_dests(x: dict) -> list[str]:
    """Where an extra lands in the package: one file (dest), or files taken out of its archive (files) plus the
    ini ClawDeck writes for it (ini)."""
    if "dest" in x:
        return [x["dest"]]
    return list((x.get("files") or {}).values()) + ([x["ini"]] if x.get("ini") else [])


def extras_ready(home: str, entry: dict) -> bool:
    d = package_dir(home, entry["version"])
    return all(os.path.isfile(os.path.join(d, p)) for x in entry.get("extras", []) for p in extra_dests(x))


def _extra(x: dict, pkg: str, work: str) -> None:
    """One extra into the unpacked package: a single file (OptiPatcher), or chosen files out of a release archive
    (fakenvapi's .7z), each with its own pinned sha256."""
    if "dest" in x:
        dest = os.path.join(pkg, x["dest"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        _get(x["url"], x["sha256"], dest)
        return
    arc = _get(x["url"], x["sha256"], os.path.join(work, os.path.basename(x["url"])))
    out = os.path.join(work, "extra-" + x["name"])
    shutil.rmtree(out, ignore_errors=True)
    err = _unpack(arc, out)
    if err:
        raise ValueError(f"unpacking {x['name']}: {err}")
    for src, to in (x.get("files") or {}).items():
        p = os.path.join(out, src)
        want = (x.get("file_sha256") or {}).get(src)
        if not os.path.isfile(p) or (want and downloads.sha256_of(p) != want.lower()):
            raise ValueError(f"{src} from {os.path.basename(x['url'])} is missing or doesn't match its pinned sha256")
        dest = os.path.join(pkg, to)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(p, dest)
    if x.get("ini"):
        dest = os.path.join(pkg, x["ini"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(EXTRA_INIS[x["name"]])


def install(home: str, entry: dict) -> dict:
    """entry (versions.json "optiscaler"): {version, url, sha256, extras: [{name, url, sha256, dest} or
    {name, url, sha256, files: {in archive: in package}, file_sha256, ini}]}."""
    ver = entry["version"]
    d = package_dir(home, ver)
    if installed(home, ver) and extras_ready(home, entry):
        return {"ok": True, "already": True, "version": ver, "dir": d}
    work = os.path.join(home, ROOT, ".download")
    try:
        os.makedirs(work, exist_ok=True)
        arc = _get(entry["url"], entry["sha256"], os.path.join(work, os.path.basename(entry["url"])))
        tmp = d + ".new"
        shutil.rmtree(tmp, ignore_errors=True)
        r = _unpack(arc, tmp)
        if r:
            return {"ok": False, "error": f"unpacking OptiScaler: {r}"}
        if not os.path.isfile(os.path.join(tmp, "OptiScaler.dll")):
            return {"ok": False, "error": "the OptiScaler archive has no OptiScaler.dll"}
        for x in entry.get("extras", []):
            _extra(x, tmp, work)
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


def _mkdirs(folder: str, rel_dir: str, dirs: list | None) -> None:
    """makedirs for folder/rel_dir, recording each folder that didn't exist yet (so unpatch takes it away again)."""
    if not rel_dir or rel_dir == ".":
        return
    parts = rel_dir.replace(os.sep, "/").split("/")
    for i in range(1, len(parts) + 1):
        rel = "/".join(parts[:i])
        p = os.path.join(folder, rel)
        if not os.path.isdir(p):
            os.makedirs(p, exist_ok=True)
            if dirs is not None and rel not in dirs:
                dirs.append(rel)


def _put(src: str, dest: str, folder: str, added: list, backed: list, dirs: list | None = None) -> None:
    rel = os.path.relpath(dest, folder).replace(os.sep, "/")
    _mkdirs(folder, os.path.dirname(rel), dirs)
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


def fsr4_xmx_missing(home: str) -> list[str]:
    """What FSR 4 on XMX still needs: nitec's two files in ~/fsr4-xmx, and ClawDeck's cabinet.dll in the plugin
    (missing only in a source checkout where tools/cabinet-lzms/build.sh hasn't run)."""
    xdir = os.path.join(home, FSR4_XMX_DIR)
    need = [f for f in FSR4_XMX_FILES if not os.path.isfile(os.path.join(xdir, f))]
    if not os.path.isfile(OWN_CABINET):
        need.append("ClawDeck's cabinet.dll (bin/cabinet.dll: build tools/cabinet-lzms)")
    return need


def _proton_cabinet(home: str) -> str | None:
    for p in sorted(glob.glob(os.path.join(home, ".local/share/Steam/steamapps/common/Proton*/files/lib/wine/x86_64-windows/cabinet.dll")), reverse=True):
        return p
    return None


def patch(home: str, folder: str, version: str, ini: dict[str, str], preset: str = "xess",
          fsr4_xmx: bool = False, layout: str = "flat") -> dict:
    """Put ClawDeck's OptiScaler into `folder` (the game's exe folder). Patching again (another preset) rewrites
    the ini and the FSR 4 files; the proxy name and backups stay. Another version or layout than the one in the
    folder is taken out first, then put in fresh."""
    if layout not in ("flat", "subfolder"):
        return {"ok": False, "error": f"unknown OptiScaler layout {layout!r}"}
    pkg = package_dir(home, version)
    if not installed(home, version):
        return {"ok": False, "error": f"OptiScaler {version} isn't downloaded yet"}
    need = fsr4_xmx_missing(home) if fsr4_xmx else []
    if need:                                  # checked before anything is written: no half-patched folder
        return {"ok": False, "error": f"FSR 4 on XMX needs {', '.join(need)} (nitec's files go in {os.path.join(home, FSR4_XMX_DIR)})"}
    who = owner(folder)
    if who["owner"] not in (None, "clawdeck"):
        return {"ok": False, "owner": who["owner"],
                "error": "this game already has OptiScaler from " + ("Decky Framegen" if who["owner"] == "decky-framegen" else "another tool or by hand")
                         + "; remove that first (ClawDeck never overwrites it)"}
    m = _manifest(folder)
    if m and (m.get("version") != version or (m.get("layout") or "flat") != layout):
        r = unpatch(folder)                   # 0.9.4 (flat) -> v10 (subfolder) and back: nothing of the old one stays
        if not r.get("ok"):
            return {"ok": False, "error": f"taking out OptiScaler {m.get('version')} first: {r.get('error')}"}
        m = None
    m = m or {"version": version, "layout": layout, "dll": None, "added": [], "backed": [], "dirs": []}
    added, backed = m["added"], m["backed"]
    dirs = m.setdefault("dirs", [])
    sub = "OptiScaler/" if layout == "subfolder" else ""        # where OptiScaler's libraries (and FSR 4's) go
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
        _put(os.path.join(pkg, "OptiScaler.dll"), os.path.join(folder, dll), folder, added, backed, dirs)
        for r, d, f in os.walk(pkg):
            for x in f:
                rel = os.path.relpath(os.path.join(r, x), pkg).replace(os.sep, "/")
                if rel in SKIP or rel.startswith(SKIP_PREFIX) or rel.endswith(SKIP_SUFFIX):
                    continue
                if fsr4_xmx and rel == sub + "amd_fidelityfx_upscaler_dx12.dll":
                    continue
                _put(os.path.join(r, x), os.path.join(folder, rel), folder, added, backed, dirs)
        # FSR 4 on XMX: nitec's upscaler instead of AMD's, with what it needs; or AMD's back when switched away.
        # The upscaler and fsr-original.dll sit where OptiScaler loads its libraries from (OptiScaler/ in the
        # subfolder layout); ClawDeck's cabinet.dll and Wine's own as cabinet_wine.dll next to the exe, where Wine
        # loads them (cabinet=n,b).
        xdir = os.path.join(home, FSR4_XMX_DIR)
        extra = (sub + "fsr-original.dll", "cabinet.dll", "cabinet_wine.dll")
        if fsr4_xmx:
            for f in FSR4_XMX_FILES:
                _put(os.path.join(xdir, f), os.path.join(folder, sub + f), folder, added, backed, dirs)
            _put(OWN_CABINET, os.path.join(folder, "cabinet.dll"), folder, added, backed, dirs)
            wcab = os.path.join(xdir, "cabinet_wine.dll")
            wcab = wcab if os.path.isfile(wcab) else _proton_cabinet(home)
            if wcab:
                _put(wcab, os.path.join(folder, "cabinet_wine.dll"), folder, added, backed, dirs)
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
        m.update({"version": version, "layout": layout, "dll": dll, "preset": preset, "ini": ini, "added": added,
                  "backed": backed, "dirs": dirs})
        with open(os.path.join(folder, STATE, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(m, f, indent=1)
        for rel in added + [STATE] + [x for x in dirs if "/" not in x]:
            p = os.path.join(folder, rel)
            if os.path.exists(p):
                userexec.chown_tree(p, home)
    except OSError as e:
        return {"ok": False, "error": f"putting OptiScaler into {folder}: {e}"}
    log.info("OptiScaler %s (%s) into %s as %s (%s)", version, layout, folder, dll, preset)
    return {"ok": True, "dll": dll, "dll_override": dll[:-4], "folder": folder, "version": version, "layout": layout}


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
    # what OptiScaler and its helpers write while the game runs: gone too, so the folder is as the game shipped it
    # (maintainer, 2026-11-04: "uninstall removes everything we installed and reverts everything back")
    for pat in RUNTIME_FILES:
        for p in glob.glob(os.path.join(glob.escape(folder), pat)):
            try:
                if os.path.isfile(p) and os.path.relpath(p, folder) not in m.get("backed", []):
                    os.remove(p)
            except OSError:
                pass
    shutil.rmtree(os.path.join(folder, "D3D12_Optiscaler"), ignore_errors=True)
    # the folders ClawDeck created (manifest "dirs", deepest first): OptiScaler/ of the subfolder layout goes with
    # whatever OptiScaler wrote into it (its caches, logs); any other only when empty
    for rel in sorted(m.get("dirs") or [], key=lambda x: x.count("/"), reverse=True):
        p = os.path.join(folder, rel)
        if rel == "OptiScaler":
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                os.rmdir(p)
            except OSError:
                pass
    for d in ("D3D12_Optiscaler", "plugins"):
        try:
            os.rmdir(os.path.join(folder, d))                 # only when empty: the game may have its own plugins/
        except OSError:
            pass
    if not errors:
        shutil.rmtree(os.path.join(folder, STATE), ignore_errors=True)
    return {"ok": not errors, "removed": True, "error": "; ".join(errors) or None}
