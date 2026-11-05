"""REFramework for Capcom RE Engine games (praydog/REFramework, MIT; onehoon's fork for OptiClick's Capcom setup).

Capcom's anti-tamper stops an RE Engine game when OptiScaler swaps its upscaler unless REFramework is loaded too
(Onimusha: Way of the Sword on the Claw 8 EX, 2026-11-04). OptiClick installs REFramework as dinput8.dll next to the
game's exe; ClawDeck does the same with the build OptiClick uses, onehoon's fork (assets/versions.json
"reframework", sha256 checked), whose notes say to use it with OptiScaler in Capcom games. Proton loads it through
WINEDLLOVERRIDES=dinput8=n,b (launchenv: reframework).

A dinput8.dll that isn't ClawDeck's (ReShade, an REFramework the user put there) is never overwritten. ClawDeck's
own copy is recorded in .clawdeck-reframework.json with its sha256, so remove() deletes exactly that file; an older
copy of ClawDeck's own (another tag) is replaced.

REFramework and OptiScaler both open their menu with Insert, so ClawDeck moves REFramework's to Delete: its config,
re2_fw_config.txt next to the exe, gets REFrameworkConfig_MenuKey_V2=46 (VK_DELETE). A new file holds only that line;
in an existing one only that line changes, and remove() puts it back as it was (or deletes the file ClawDeck made,
with REFramework's log and its empty reframework/ folders), so the folder is left as the game had it.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import urllib.error

from . import downloads, userexec

log = logging.getLogger("clawdeck.reframework")

MARK = ".clawdeck-reframework.json"
DLL = "dinput8.dll"
CONFIG = "re2_fw_config.txt"
MENU_KEY = "REFrameworkConfig_MenuKey_V2"
VK_DELETE = 46                                  # Insert (45) is OptiScaler's menu key too
RUNTIME = ("reframework_log.txt",)              # written by REFramework next to the exe while the game runs
RUNTIME_DIR = "reframework"                     # its autorun/, plugins/, data/ (taken away only when it holds no file)


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


def _set_menu_key(folder: str, key: int) -> dict:
    """REFramework's menu key in re2_fw_config.txt; returns what remove() needs to undo it."""
    p = os.path.join(folder, CONFIG)
    line = f"{MENU_KEY}={key}"
    if not os.path.isfile(p):
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
        return {"config": "created"}
    with open(p, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        text = f.read()
    m = re.search(rf"(?m)^{MENU_KEY}=([^\r\n]*)", text)
    if m:
        was = m.group(1)
        text = text[:m.start()] + line + text[m.end():]
    else:
        was = None
        nl = "\r\n" if "\r\n" in text else "\n"
        text += ("" if not text or text.endswith(("\n", "\r")) else nl) + line + nl
    with open(p, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(text)
    return {"config": "edited", "menu_key_was": was}


def _undo_menu_key(folder: str, m: dict) -> None:
    p = os.path.join(folder, CONFIG)
    how = m.get("config")
    if how == "created":
        if os.path.isfile(p):
            os.remove(p)
    elif how == "edited" and os.path.isfile(p):
        with open(p, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
            text = f.read()
        was = m.get("menu_key_was")
        if was is None:
            text = re.sub(rf"(?m)^{MENU_KEY}=[^\r\n]*(\r?\n)?", "", text, count=1)
        else:
            text = re.sub(rf"(?m)^{MENU_KEY}=[^\r\n]*", lambda _: f"{MENU_KEY}={was}", text, count=1)
        with open(p, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(text)


def install(home: str, folder: str, entry: dict, allow_unpinned: bool = False, menu_key: int | None = VK_DELETE) -> dict:
    st = status(folder)
    if st["ours"]:
        if not entry.get("tag") or st.get("tag") == entry.get("tag"):
            return {"ok": True, "already": True, **st}
        old = remove(folder)                    # ClawDeck's own older REFramework (praydog's nightly): replaced
        if not old.get("ok"):
            return {"ok": False, "error": f"replacing REFramework {st.get('tag')}: {old.get('error')}", **st}
        st = status(folder)
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
        if entry.get("dll_sha256") and got["tag"] == entry.get("tag") and downloads.sha256_of(src) != entry["dll_sha256"].lower():
            return {"ok": False, "error": f"{DLL} in {os.path.basename(got['archive'])} does not match its pinned sha256; not installed", **st}
        dest = os.path.join(folder, DLL)
        had = [x for x in (*RUNTIME, RUNTIME_DIR) if os.path.lexists(os.path.join(folder, x))]
        shutil.copyfile(src, dest)
        mark = {"tag": got["tag"], "repo": got["repo"], "sha256": downloads.sha256_of(dest), "had": had}
        if menu_key is not None:
            mark.update(_set_menu_key(folder, int(menu_key)))
        with open(os.path.join(folder, MARK), "w", encoding="utf-8") as f:
            json.dump(mark, f)
        for p in (dest, os.path.join(folder, MARK), os.path.join(folder, CONFIG)):
            if os.path.exists(p):
                userexec.chown_tree(p, home)
    except OSError as e:
        return {"ok": False, "error": f"copying into {folder}: {e}", **st}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log.info("REFramework %s into %s", got["tag"], folder)
    return {"ok": True, "tag": got["tag"], **status(folder)}


def _only_empty_dirs(p: str) -> bool:
    return all(not f for _, _, f in os.walk(p))


def remove(folder: str | None) -> dict:
    st = status(folder)
    if not st["ours"]:
        return {"ok": True, "removed": False, **st}
    m = _mark(folder) or {}
    try:
        os.remove(os.path.join(folder, DLL))
        _undo_menu_key(folder, m)
        had = set(m.get("had") or [])
        for x in RUNTIME:
            if x not in had and os.path.isfile(os.path.join(folder, x)):
                os.remove(os.path.join(folder, x))
        rd = os.path.join(folder, RUNTIME_DIR)
        if RUNTIME_DIR not in had and os.path.isdir(rd) and not os.path.islink(rd) and _only_empty_dirs(rd):
            shutil.rmtree(rd)
        os.remove(os.path.join(folder, MARK))
    except OSError as e:
        return {"ok": False, "error": str(e), **st}
    return {"ok": True, "removed": True, **status(folder)}
