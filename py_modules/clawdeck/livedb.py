"""The per-game lists, kept current without a ClawDeck update (maintainer, 2026-11-05: "always looking at the
database", as OptiClick does on Windows).

Two lists, each fetched at most every REFRESH_S in the background at start (main.py) and kept in CACHE_DIR, with the
copy in the plugin as the fallback offline:

  games.json   ClawDeck's own list (OptiScaler's per-game fixes, the Capcom RE Engine exes that need REFramework,
               what was tested on a Claw): assets/games.json on ClawDeck's main branch. Used only when its schema
               matches and it is newer (generated later) than the plugin's copy.
  OptiClick    OptiClick's public Supported Game List, the wiki page OptiClick itself loads at start
               (supported_games_meta.json in its cache names this URL): which games OptiClick supports, per GPU
               vendor, with their Steam app ids. ClawDeck follows its Intel column: a game OptiClick lists as "Not
               Supported" on Intel Arc isn't recommended. OptiClick's per-game recipes (which DLL to hook, its ini)
               come from its signed-in service and are not public, so they are not used.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request

from . import tls

log = logging.getLogger("clawdeck.livedb")

GAMES_URL = "https://raw.githubusercontent.com/IHulkSmashI/ClawDeck/main/assets/games.json"
OPTICLICK_URL = "https://raw.githubusercontent.com/wiki/onehoon/OptiClick/Supported-Game-List.md"
REFRESH_S = 12 * 3600
GAMES_FILE = "games.json"
OPTICLICK_FILE = "opticlick-supported.md"


def _get(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ClawDeck"})
    with tls.urlopen(req, timeout) as r:
        return r.read()


def _fresh(path: str) -> bool:
    try:
        return time.time() - os.stat(path).st_mtime < REFRESH_S
    except OSError:
        return False


def _write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def valid_games(doc: object, schema: object) -> bool:
    return isinstance(doc, dict) and isinstance(doc.get("games"), list) and doc.get("schema") == schema


def refresh(cache_dir: str, bundled_games: str, force: bool = False, get=_get) -> dict:
    """Fetch both lists when the cached copies are older than REFRESH_S (blocking: run it off the event loop).
    A list that fails to download or doesn't parse leaves the cached copy as it was."""
    out = {}
    try:
        with open(bundled_games, "r", encoding="utf-8") as f:
            schema = json.load(f).get("schema")
    except (OSError, ValueError):
        schema = None
    gp = os.path.join(cache_dir, GAMES_FILE)
    if force or not _fresh(gp):
        try:
            data = get(GAMES_URL)
            if valid_games(json.loads(data), schema):
                _write(gp, data)
                out["games"] = "updated"
            else:
                out["games"] = "other schema, kept"
        except (OSError, ValueError) as e:
            out["games"] = f"error: {e}"
    op = os.path.join(cache_dir, OPTICLICK_FILE)
    if force or not _fresh(op):
        try:
            text = get(OPTICLICK_URL).decode("utf-8", "replace")
            if parse_opticlick(text):
                _write(op, text.encode("utf-8"))
                out["opticlick"] = "updated"
            else:
                out["opticlick"] = "no games in the page, kept"
        except (OSError, ValueError) as e:
            out["opticlick"] = f"error: {e}"
    if out:
        log.info("per-game lists: %s", out)
    return out


def games_path(cache_dir: str, bundled: str) -> str:
    """The newer usable games.json: the one fetched from main, else the plugin's."""
    try:
        with open(bundled, "r", encoding="utf-8") as f:
            b = json.load(f)
        with open(os.path.join(cache_dir, GAMES_FILE), "r", encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError):
        return bundled
    if valid_games(c, b.get("schema")) and str(c.get("generated") or "") > str(b.get("generated") or ""):
        return os.path.join(cache_dir, GAMES_FILE)
    return bundled


# ---- OptiClick's Supported Game List ----------------------------------------------------------------------------

def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def parse_opticlick(text: str) -> dict[str, dict]:
    """{steam app id: {"name", "intel", "amd", "nvidia", "game_id"}} from the wiki page: the table's GPU columns,
    joined by English title to the page's metadata block (game ids, Steam app ids)."""
    support: dict[str, tuple[str, str, str]] = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 5 and cells[1] and cells[1] != "English Title" and not set(cells[1]) <= set("-: "):
            support[_norm(cells[1])] = (cells[2], cells[3], cells[4])
    m = re.search(r"<!--\s*opticlick-supported-games-meta\s*(\{.*?\})\s*-->", text, re.S)
    try:
        meta = json.loads(m.group(1)).get("games", []) if m else []
    except ValueError:
        meta = []
    out: dict[str, dict] = {}
    for g in meta:
        name = str(g.get("game_name_en") or "")
        cols = support.get(_norm(name))
        if not cols:
            continue
        ids = g.get("cover_steam_app_ids") or ([g["cover_steam_app_id"]] if g.get("cover_steam_app_id") else [])
        for appid in ids:
            out[str(appid)] = {"name": name, "intel": cols[0], "amd": cols[1], "nvidia": cols[2],
                               "game_id": g.get("game_id")}
    return out


def opticlick(cache_dir: str) -> dict[str, dict]:
    try:
        with open(os.path.join(cache_dir, OPTICLICK_FILE), "r", encoding="utf-8") as f:
            return parse_opticlick(f.read())
    except OSError:
        return {}


def opticlick_says(entries: dict[str, dict], appid: str) -> dict:
    """What OptiClick's list says about this game on Intel Arc: listed or not, supported or not."""
    e = entries.get(str(appid))
    if not entries:
        return {"known": False, "listed": False, "intel_ok": None}
    if not e:
        return {"known": True, "listed": False, "intel_ok": None}
    return {"known": True, "listed": True, "name": e["name"], "intel": e["intel"],
            "intel_ok": not e["intel"].lower().startswith("not supported")}
