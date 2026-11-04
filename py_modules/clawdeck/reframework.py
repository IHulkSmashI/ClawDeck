"""REFramework for Capcom RE Engine games (praydog/REFramework, MIT).

Capcom's anti-tamper stops an RE Engine game when OptiScaler swaps its upscaler unless REFramework is loaded too
(Onimusha: Way of the Sword on the Claw 8 EX, 2026-11-04). OptiClick installs REFramework as dinput8.dll next to the
game's exe; ClawDeck does the same from REFramework's nightly release (one REFramework.zip for every game since the
per-game zips were retired). Proton loads it through WINEDLLOVERRIDES=dinput8=n,b (launchenv: reframework).

A dinput8.dll that isn't ClawDeck's (ReShade, an REFramework the user put there) is never overwritten. ClawDeck's
own copy is recorded in .clawdeck-reframework.json with its sha256, so remove() deletes exactly that file.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import shutil
import urllib.error

from . import downloads, userexec

log = logging.getLogger("clawdeck.reframework")

MARK = ".clawdeck-reframework.json"
DLL = "dinput8.dll"


def exe_dir(game_dir: str, exes: list[str]) -> str | None:
    """The folder that holds the game's main exe (one of `exes`, lower case)."""
    want = {e.lower() for e in exes}
    for r, d, f in os.walk(game_dir):
        if r.count(os.sep) - game_dir.rstrip(os.sep).count(os.sep) >= 4:
            d[:] = []
        if any(x.lower() in want for x in f):
            return r
    return None


def _mark(folder: str) -> dict | None:
    try:
        with open(os.path.join(folder, MARK), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def status(folder: str | None) -> dict:
    if not folder:
        return {"present": False, "ours": False, "folder": None}
    dll = os.path.join(folder, DLL)
    m = _mark(folder)
    ours = bool(m and os.path.isfile(dll) and downloads.sha256_of(dll) == m.get("sha256"))
    return {"present": os.path.isfile(dll), "ours": ours, "folder": folder, "tag": (m or {}).get("tag") if ours else None}


def install(home: str, folder: str, entry: dict, allow_unpinned: bool = False) -> dict:
    st = status(folder)
    if st["ours"]:
        return {"ok": True, "already": True, **st}
    if st["present"]:
        return {"ok": False, "error": f"{DLL} in {folder} isn't ClawDeck's (ReShade or REFramework put there by hand?); "
                                      "left as it is", **st}
    work = os.path.join(home, ".local", "share", "clawdeck", "reframework")
    try:
        got = downloads.fetch(entry, work, allow_unpinned)
    except PermissionError as e:
        return {"ok": False, "error": str(e), **st}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"download: {e.url} answered {e.code} {e.reason}", **st}
    except Exception as e:
        return {"ok": False, "error": f"download: {type(e).__name__}: {e}", **st}
    try:
        src = next(iter(glob.glob(os.path.join(got["dir"], "**", DLL), recursive=True)), None)
        if not src:
            return {"ok": False, "error": f"no {DLL} in {os.path.basename(got['archive'])}", **st}
        dest = os.path.join(folder, DLL)
        shutil.copyfile(src, dest)
        with open(os.path.join(folder, MARK), "w", encoding="utf-8") as f:
            json.dump({"tag": got["tag"], "repo": got["repo"], "sha256": downloads.sha256_of(dest)}, f)
        for p in (dest, os.path.join(folder, MARK)):
            userexec.chown_tree(p, home)
    except OSError as e:
        return {"ok": False, "error": f"copying into {folder}: {e}", **st}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log.info("REFramework %s into %s", got["tag"], folder)
    return {"ok": True, "tag": got["tag"], **status(folder)}


def remove(folder: str | None) -> dict:
    st = status(folder)
    if not st["ours"]:
        return {"ok": True, "removed": False, **st}
    try:
        os.remove(os.path.join(folder, DLL))
        os.remove(os.path.join(folder, MARK))
    except OSError as e:
        return {"ok": False, "error": str(e), **st}
    return {"ok": True, "removed": True, **status(folder)}
