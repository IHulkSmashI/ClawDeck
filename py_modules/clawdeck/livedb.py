"""The per-game lists, kept current without a ClawDeck update (maintainer, 2026-11-05: "always looking at the
database").

Two lists, each fetched at most every REFRESH_S in the background at start (main.py) and kept in CACHE_DIR, with the
copy in the plugin as the fallback offline:

  games.json   ClawDeck's own list (OptiScaler's per-game fixes, the Capcom RE Engine exes that need REFramework,
               what was tested on a Claw): assets/games.json on ClawDeck's main branch. Used only when its schema
               matches and it is newer (generated later) than the plugin's copy.
  supported    the public supported-games list (a wiki page)
               (supported_games_meta.json in its cache names this URL): which games are supported, per GPU
               vendor, with their Steam app ids. ClawDeck follows its Intel column: a game listed as "Not
               Supported" on Intel Arc isn't recommended. Per-game recipes (which DLL to hook, its ini)
               come from its signed-in service and are not public, so they are not used.
  OptiScaler   OptiScaler's own Compatibility List (its wiki, GPL-3.0 project, community entries): per game whether
               it works, the inputs, and notes ("Requires REFramework"). A game's detailed entry (one wiki page) is
               fetched when the game is opened in Per-game: which file names load OptiScaler there ("Filename":
               dxgi.dll, winmm.dll, ...), in the order the entry gives them, plus its known issues. This is the
               public record other installers (Decky Framegen, private per-game recipes) follow too.
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
SUPPORTED_LIST_URL = "https://raw.githubusercontent.com/wiki/onehoon/OptiClick/Supported-Game-List.md"
OPTISCALER_COMPAT_URL = "https://raw.githubusercontent.com/wiki/optiscaler/OptiScaler/Compatibility-List.md"
OPTISCALER_PAGE_URL = "https://raw.githubusercontent.com/wiki/optiscaler/OptiScaler/{slug}.asciidoc"
OPTISCALER_FILE = "optiscaler-compat.md"
REFRESH_S = 12 * 3600
GAMES_FILE = "games.json"
SUPPORTED_LIST_FILE = "supported-games.md"


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
    op = os.path.join(cache_dir, SUPPORTED_LIST_FILE)
    if force or not _fresh(op):
        try:
            text = get(SUPPORTED_LIST_URL).decode("utf-8", "replace")
            if parse_supported_list(text):
                _write(op, text.encode("utf-8"))
                out["supported_list"] = "updated"
            else:
                out["supported_list"] = "no games in the page, kept"
        except (OSError, ValueError) as e:
            out["supported_list"] = f"error: {e}"
    cp = os.path.join(cache_dir, OPTISCALER_FILE)
    if force or not _fresh(cp):
        try:
            text = get(OPTISCALER_COMPAT_URL).decode("utf-8", "replace")
            if parse_compat(text):
                _write(cp, text.encode("utf-8"))
                out["optiscaler"] = "updated"
            else:
                out["optiscaler"] = "no games in the page, kept"
        except (OSError, ValueError) as e:
            out["optiscaler"] = f"error: {e}"
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


# ---- the public supported-games list ----------------------------------------------------------------------------

def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def parse_supported_list(text: str) -> dict[str, dict]:
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


def supported_list(cache_dir: str) -> dict[str, dict]:
    try:
        with open(os.path.join(cache_dir, SUPPORTED_LIST_FILE), "r", encoding="utf-8") as f:
            return parse_supported_list(f.read())
    except OSError:
        return {}


def supported_list_says(entries: dict[str, dict], appid: str) -> dict:
    """What the list says about this game on Intel Arc: listed or not, supported or not."""
    e = entries.get(str(appid))
    if not entries:
        return {"known": False, "listed": False, "intel_ok": None}
    if not e:
        return {"known": True, "listed": False, "intel_ok": None}
    return {"known": True, "listed": True, "name": e["name"], "intel": e["intel"],
            "intel_ok": not e["intel"].lower().startswith("not supported")}


# ---- OptiScaler's Compatibility List ----------------------------------------------------------------------------

STATUS = {"\u2705": "works", "\u274c": "broken", "\u2796": "mixed"}
# the proxy names ClawDeck can install OptiScaler under (optiscaler.PROXIES order is its own fallback)
PROXY_NAMES = ("dxgi.dll", "winmm.dll", "version.dll", "dbghelp.dll", "winhttp.dll", "wininet.dll")


def parse_compat(text: str) -> dict[str, dict]:
    """{normalized game name: {"name", "slug", "status", "inputs", "notes", "reframework"}} from the table rows
    "| [Game](Slug) | ✅ | DLSS, FSR3.1 | ✨ | notes | images |"."""
    out: dict[str, dict] = {}
    for line in text.splitlines():
        if not line.startswith("| "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        status = next((v for k, v in STATUS.items() if k in cells[1]), None)
        if not status:                       # header, separator, the template row's "✅/❌/➖" and other tables
            continue
        if "/" in cells[1]:
            continue
        m = re.match(r"\[([^\]]+)\]\(([^)\s]+)\)", cells[0])
        name, slug = (m.group(1), m.group(2)) if m else (re.sub(r"[*_`]", "", cells[0]).strip(), None)
        if not name:
            continue
        notes = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cells[4]).strip()
        out[_norm(name)] = {"name": name, "slug": slug if slug and not slug.startswith("http") else None, "status": status,
                            "inputs": cells[2], "notes": notes, "reframework": "reframework" in cells[4].lower()}
    return out


def compat(cache_dir: str) -> dict[str, dict]:
    try:
        with open(os.path.join(cache_dir, OPTISCALER_FILE), "r", encoding="utf-8") as f:
            return parse_compat(f.read())
    except OSError:
        return {}


def find_compat(entries: dict[str, dict], name: str | None) -> dict | None:
    """The Compatibility List row for a Steam name: exact (letters and digits only), else the one row whose name
    the Steam name starts with or the other way round (editions: "CONTROL Ultimate Edition" / "Control")."""
    if not name or not entries:
        return None
    n = _norm(name)
    if n in entries:
        return entries[n]
    near = [e for k, e in entries.items() if len(k) >= 5 and (n.startswith(k) or k.startswith(n))]
    return near[0] if len(near) == 1 else None


def parse_page(text: str) -> dict:
    """A game's detailed entry (AsciiDoc table): the file names OptiScaler loads as, in order, and the known
    issues / notes as short lines."""
    def cell(label: str) -> str:
        m = re.search(r"\|\*\*" + label + r"\*\*\s*\n(.*?)(?=\n\s*\|\*\*|\n\|===)", text, re.S | re.I)
        return m.group(1) if m else ""
    files = re.findall(r"[A-Za-z0-9_]+\.(?:dll|asi)", cell("Filename"))
    proxies = [f.lower() for f in dict.fromkeys(f.lower() for f in files) if f.lower() in PROXY_NAMES]
    def bullets(label: str) -> list[str]:
        out = []
        for b in re.findall(r"^\*\s+(.+)$", cell(label), re.M):
            if "+++<s>" in b or b.strip() in ("-", ""):          # struck out: no longer true
                continue
            b = re.sub(r"https?://\S+?\[([^\]]*)\]", r"\1", b)  # AsciiDoc links: their text
            b = re.sub(r"[*_`]+", "", b)
            out.append(re.sub(r"\s+", " ", b).strip()[:200])
        return out
    linux = "linux" in cell("OS").lower()
    return {"proxies": proxies, "files": [f.lower() for f in dict.fromkeys(files)], "issues": bullets("Known Issues"),
            "notes": bullets("Notes"), "tested_on_linux": linux}


def game_page(cache_dir: str, slug: str | None, get=_get) -> dict | None:
    """The detailed entry, fetched when older than REFRESH_S, else (or offline) the cached copy."""
    if not slug or not re.fullmatch(r"[A-Za-z0-9._()&,'%-]+", slug):
        return None
    path = os.path.join(cache_dir, "optiscaler-pages", slug + ".asciidoc")
    if not _fresh(path):
        try:
            data = get(OPTISCALER_PAGE_URL.format(slug=urllib.request.quote(slug, safe="()&,'-._")))
            _write(path, data)
        except OSError as e:
            log.info("OptiScaler entry %s: %s", slug, e)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return parse_page(f.read())
    except OSError:
        return None
