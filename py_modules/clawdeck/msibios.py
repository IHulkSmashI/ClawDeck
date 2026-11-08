"""Is there a newer MSI BIOS for this Claw? (read-only: nothing is downloaded or flashed)

MSI ships performance, power and fan fixes for the Claw in BIOS updates, and Linux has no way to learn about them
(MSI isn't on the LVFS, so fwupd stays silent). ClawDeck compares the running BIOS (DMI bios_version, e.g.
"E1T91IMS.10D") with the list on MSI's own support page for that model, the JSON the page itself loads:
  https://www.msi.com/api/v1/product/support/panel?product=<page>&type=bios
-> result.downloads.BIOS[]: download_version ("E1T91IMS.10E"), download_release ("2026-10-30").
The board part before the dot must match (E1T91IMS = Claw 8 EX); the part after it is MSI's revision, which counts up
in hex (10D < 10E < 114). Asked at most every CHECK_EVERY_S, off the event loop; offline is just "not checked".
Flashing stays MSI's way (Windows, or their USB instructions): Setup links the support page.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request

from . import sysfs, tls

log = logging.getLogger("clawdeck.msibios")

# board id (DMI board_name) -> MSI support page name. Read from msi.com on 2026-11-07.
PAGES = {
    "MS-1T91": "Claw-8-EX-AI-Plus-CG3EMX",
    "MS-1T52": "Claw-8-AI-plus-A2VM",
    "MS-1T42": "Claw-7-AI-plus-A2VM",
    "MS-1T41": "Claw-A1M",
    "MS-1T8K": "Claw-A8-BZ2EM",
}
API = "https://www.msi.com/api/v1/product/support/panel?product={page}&type=bios"
SUPPORT = "https://www.msi.com/Handheld/{page}/support#bios"
CHECK_EVERY_S = 12 * 3600
_cache: dict = {"at": 0.0, "latest": None, "date": None, "error": None, "page": None}


def running() -> str:
    return (sysfs.read("/sys/class/dmi/id/bios_version") or "").strip()


def _split(v: str) -> tuple[str, str]:
    board, _, rev = (v or "").strip().partition(".")
    return board.upper(), rev.upper()


def _rev(rev: str) -> int:
    try:
        return int(rev, 16)
    except ValueError:
        return -1


def newest(entries: list[dict], board: str) -> tuple[str, str] | None:
    """(version, date) of the newest BIOS for this board in MSI's list; ME firmware and other boards are skipped."""
    best = None
    for e in entries or []:
        v = str(e.get("download_version") or "")
        b, r = _split(v)
        if b != board or _rev(r) < 0:
            continue
        if best is None or _rev(r) > _rev(_split(best[0])[1]):
            best = (v, str(e.get("download_release") or ""))
    return best


def _fetch(page: str) -> list[dict]:
    req = urllib.request.Request(API.format(page=page), headers={"Accept": "application/json", "User-Agent": "ClawDeck"})
    with tls.urlopen(req, 20) as r:
        data = json.loads(r.read())
    return (((data or {}).get("result") or {}).get("downloads") or {}).get("BIOS") or []


def refresh(model_id: str, force: bool = False, fetch=_fetch) -> dict:
    page = PAGES.get(model_id)
    if not page:
        return dict(_cache)
    if not force and _cache["at"] and _cache["page"] == page and time.monotonic() - _cache["at"] < CHECK_EVERY_S:
        return dict(_cache)
    board, _ = _split(running())
    try:
        best = newest(fetch(page), board)
        _cache.update(latest=best[0] if best else None, date=best[1] if best else None, error=None)
    except Exception as e:  # noqa: BLE001 - offline or MSI changed the page: "not checked", nothing else
        log.info("MSI BIOS check: %s", e)
        _cache.update(error=str(e)[:200])
    _cache.update(at=time.monotonic(), page=page)
    return dict(_cache)


def status(model_id: str) -> dict:
    page = PAGES.get(model_id)
    cur = running()
    if not page or not cur:
        return {"applies": False}
    latest = _cache.get("latest") if _cache.get("page") == page else None
    newer = bool(latest) and _rev(_split(latest)[1]) > _rev(_split(cur)[1]) and _split(latest)[0] == _split(cur)[0]
    if newer:
        line = (f"MSI has BIOS {latest} ({_cache.get('date') or 'new'}); this Claw runs {cur}. Install it MSI's way "
                "(Windows or MSI's USB steps): MSI's BIOS updates bring power, fan and performance fixes")
    elif latest:
        line = f"BIOS {cur} is MSI's newest"
    else:
        line = f"BIOS {cur} (MSI's list not checked yet)"
    return {"applies": True, "running": cur, "latest": latest, "date": _cache.get("date"), "newer": newer,
            "line": line, "url": SUPPORT.format(page=page), "error": _cache.get("error")}
