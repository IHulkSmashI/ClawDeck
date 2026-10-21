"""Per-game knowledge, looked up fresh: OptiScaler's community compatibility list and OptiPatcher's supported games.

  OptiScaler wiki, Compatibility-List.md: one table row per tested game - works / doesn't, which upscaler inputs it
  offers, whether OptiPatcher supports it, and notes with known fixes ("set Dxgi=false", "-dx12", "Requires
  REFramework"). ClawDeck shows the notes and applies the INI fixes they name.
  OptiPatcher, OptiPatcher/dllmain.cpp: the executables it patches. Read exactly as OptiScaler's own
  setup_windows.bat does: CHECK_UE(name) means name-win64-shipping.exe / name-wingdk-shipping.exe, plus every
  exeName == "..." literal.
Both are cached for a day in ClawDeck's runtime folder; offline, the last copy is used.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import time
import urllib.request

WIKI = "https://raw.githubusercontent.com/wiki/optiscaler/OptiScaler/Compatibility-List.md"
WIKI_PAGE = "https://github.com/optiscaler/OptiScaler/wiki/"
OPTIPATCHER_SRC = "https://raw.githubusercontent.com/optiscaler/OptiPatcher/main/OptiPatcher/dllmain.cpp"
DAY = 24 * 3600
# INI fixes the compatibility notes name, and where they go in OptiScaler.ini
KNOWN_INI_KEYS = {"Dxgi": "Spoofing", "RestoreComputeSignature": "Hotfix", "RestoreGraphicSignature": "Hotfix",
                  "LoadAsiPlugins": "Plugins", "Dx12Upscaler": "Upscalers", "Dx11Upscaler": "Upscalers",
                  "FGInput": "FrameGen", "FGOutput": "FrameGen"}
ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}


def _fetch(url: str, cache: str, max_age: float = DAY, timeout: float = 15.0) -> str | None:
    try:
        if time.time() - os.path.getmtime(cache) < max_age:
            with open(cache, "r", encoding="utf-8") as f:
                return f.read()
    except OSError:
        pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ClawDeck"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", errors="replace")
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache + ".tmp", "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(cache + ".tmp", cache)
        return text
    except OSError:
        try:
            with open(cache, "r", encoding="utf-8") as f:   # offline: the last copy
                return f.read()
        except OSError:
            return None


def normalize(name: str) -> str:
    s = name.lower().replace("’", "'").replace("&", " and ")
    s = re.sub(r"\(\d{4}\)", " ", s)                 # "(2023)" remake years
    s = re.sub(r"[™®©:']", "", s)
    words = [ROMAN.get(w, w) for w in re.split(r"[^a-z0-9]+", s) if w]
    return " ".join(words)


def parse_wiki(md: str) -> list[dict]:
    rows = []
    for line in md.splitlines():
        if not line.startswith("| ") or line.startswith("| Game") or line.startswith("| ----"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        m = re.match(r"\[(.+?)\]\((.+?)\)", cells[0])
        name, page = (m.group(1), m.group(2)) if m else (cells[0], None)
        status = "works" if "✅" in cells[1] else "broken" if "❌" in cells[1] else "partial" if "➖" in cells[1] else "unknown"
        if len(cells) >= 6:
            patcher, notes = "✨" in cells[3], cells[4]
        else:
            patcher, notes = False, cells[3] if len(cells) > 3 else ""
        rows.append({"name": name, "key": normalize(name), "status": status,
                     "inputs": [i.strip() for i in cells[2].split(",") if i.strip()],
                     "optipatcher": patcher, "notes": re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", notes),
                     "page": WIKI_PAGE + page if page and not page.startswith("http") else page})
    return rows


def ini_fixes(notes: str) -> dict:
    """Key=value fixes named in a compatibility note, for keys ClawDeck knows the INI section of."""
    out = {}
    for k, v in re.findall(r"`?\b([A-Z][A-Za-z0-9]+)=(true|false|auto|[a-z0-9_]+)\b`?", notes or ""):
        if k in KNOWN_INI_KEYS:
            out.setdefault(KNOWN_INI_KEYS[k], {})[k] = v
    return out


def lookup(rows: list[dict], game_name: str) -> dict | None:
    key = normalize(game_name)
    by_key = {r["key"]: r for r in rows}
    if key in by_key:
        return by_key[key]
    close = difflib.get_close_matches(key, list(by_key), n=1, cutoff=0.9)
    return by_key[close[0]] if close else None


def parse_optipatcher(src: str) -> set[str]:
    names = set()
    for m in re.finditer(r"CHECK_UE\s*\(\s*([A-Za-z0-9_]+)\s*\)", src):
        if m.group(1) != "name":
            names.add(f"{m.group(1)}-win64-shipping.exe".lower())
            names.add(f"{m.group(1)}-wingdk-shipping.exe".lower())
    for m in re.finditer(r"exeName\s*==\s*[\"']([^\"']+)[\"']", src):
        names.add(m.group(1).lower())
    return names


class Compat:
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self._rows: list[dict] | None = None
        self._patcher: set[str] | None = None

    def rows(self) -> list[dict]:
        if self._rows is None:
            md = _fetch(WIKI, os.path.join(self.cache_dir, "optiscaler-compat.md"))
            self._rows = parse_wiki(md) if md else []
        return self._rows

    def optipatcher_exes(self) -> set[str]:
        if self._patcher is None:
            src = _fetch(OPTIPATCHER_SRC, os.path.join(self.cache_dir, "optipatcher-dllmain.cpp"))
            self._patcher = parse_optipatcher(src) if src else set()
        return self._patcher

    def for_game(self, name: str, exes: list[str]) -> dict:
        row = lookup(self.rows(), name) if name else None
        patched = sorted(e for e in exes if e.lower() in self.optipatcher_exes())
        return {"listed": row is not None, "entry": row, "optipatcher": bool(patched) or bool(row and row["optipatcher"]),
                "optipatcher_exe": patched[0] if patched else None,
                "ini_fixes": ini_fixes(row["notes"]) if row else {},
                "needs_reframework": bool(row and "reframework" in row["notes"].lower()),
                "list_loaded": bool(self._rows)}

    def refresh(self) -> None:
        for f in ("optiscaler-compat.md", "optipatcher-dllmain.cpp"):
            try:
                os.remove(os.path.join(self.cache_dir, f))
            except OSError:
                pass
        self._rows = self._patcher = None


def dump(rows: list[dict]) -> str:
    return json.dumps(rows[:3], indent=2)
