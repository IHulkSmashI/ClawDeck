"""ClawDeck's own updates (Setup -> Updates): which release is installed, which is the newest, and what Decky needs to
install it.

The install itself is Decky's: the frontend hands the release's ClawDeck.zip to Decky's own plugin installer
(utilities/install_plugin, the same one "Install plugin from URL" uses), which asks the user, checks the sha256
GitHub records for the asset, replaces the plugin folder and reloads it. Nothing here writes outside ClawDeck's
folder, and nothing runs in the background: GitHub is asked only when Setup opens or the user taps Check.

Which build is installed comes from build.json, written into the zip by CI (the tag for a release build, the commit
for every build). Builds from before build.json (0.2.0 beta 1 and 2) show as "unknown", and the newest release is
offered to them.
"""
from __future__ import annotations

import json
import os
import re
import time

from . import downloads

REPO = "IHulkSmashI/ClawDeck"
RELEASES = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
ASSET = "ClawDeck.zip"


def installed(plugin_dir: str) -> dict:
    """build.json of the running ClawDeck: {"tag": "v0.2.0-beta.3" or "", "commit": "abc1234", "built": "..."}."""
    try:
        with open(os.path.join(plugin_dir, "build.json"), "r", encoding="utf-8") as f:
            b = json.load(f)
        return {"tag": str(b.get("tag") or ""), "commit": str(b.get("commit") or ""), "built": str(b.get("built") or "")}
    except (OSError, ValueError):
        return {"tag": "", "commit": "", "built": ""}


def _key(tag: str) -> tuple:
    """v0.2.0-beta.2 -> (0, 2, 0, 0, 2); a final release (no "-beta.N") sorts after its betas."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:-[a-z]+\.?(\d+))?", tag or "")
    if not m:
        return (-1,)
    a, b, c, pre = m.groups()
    return (int(a), int(b), int(c), 0 if pre is not None else 1, int(pre or 0))


def newest(releases: list[dict]) -> dict | None:
    """The newest published release that has ClawDeck.zip (betas count: ClawDeck is in beta)."""
    ok = [r for r in releases if not r.get("draft") and downloads.pick_asset(r, ASSET) and _key(r.get("tag_name", ""))[0] >= 0]
    return max(ok, key=lambda r: _key(r["tag_name"])) if ok else None


def check(plugin_dir: str, releases: list[dict] | None = None) -> dict:
    mine = installed(plugin_dir)
    try:
        rels = releases if releases is not None else json.loads(downloads._get(RELEASES))
    except Exception as e:  # noqa: BLE001 - offline, rate limit: said in the panel, nothing else changes
        return {"ok": False, "installed": mine, "error": f"couldn't reach GitHub: {e}"[:200]}
    top = newest(rels)
    if not top:
        return {"ok": True, "installed": mine, "latest": None, "update": False}
    asset = downloads.pick_asset(top, ASSET)
    latest = {"tag": top["tag_name"], "name": top.get("name") or top["tag_name"], "url": asset["browser_download_url"],
              "sha256": downloads.asset_digest(asset) or "", "notes": top.get("html_url", ""),
              "prerelease": bool(top.get("prerelease"))}
    update = not mine["tag"] or _key(latest["tag"]) > _key(mine["tag"])
    return {"ok": True, "installed": mine, "latest": latest, "update": update}


# Decky's installer uninstalls the old version before it unpacks the new one (stop(uninstall=True), which runs
# ClawDeck's _uninstall). The Update button writes this marker first, so that uninstall keeps everything. It is
# good for a few minutes only: a real uninstall later (or after the user cancelled Decky's prompt) reverts as always.
MARKER = "update-pending"
MARKER_MAX_AGE = 600


def mark_update(runtime_dir: str) -> dict:
    try:
        os.makedirs(runtime_dir, exist_ok=True)
        with open(os.path.join(runtime_dir, MARKER), "w", encoding="utf-8") as f:
            f.write(str(int(time.time())))
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def update_pending(runtime_dir: str, clear: bool = False, now: float | None = None) -> bool:
    p = os.path.join(runtime_dir, MARKER)
    try:
        fresh = (now or time.time()) - os.path.getmtime(p) < MARKER_MAX_AGE
    except OSError:
        return False
    if clear:
        try:
            os.remove(p)
        except OSError:
            pass
    return fresh
