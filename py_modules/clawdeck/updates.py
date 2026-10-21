"""Keeps the third-party tools ClawDeck installs at their newest release, the way OptiClick does on Windows.

  optiscaler   optiscaler/OptiScaler, latest stable release, Optiscaler_<version>.7z (bundles fakenvapi, Nukem's
               dlssg-to-fsr3 and Intel's XeSS libraries incl. libxess_fg / libxell for XeSS frame generation)
  optipatcher  optiscaler/OptiPatcher, the rolling OptiPatcher.asi (what OptiScaler's own setup script downloads)
  reframework  praydog/reframework-nightly, latest REFramework.zip (one dinput8.dll for every RE Engine game)
  lsfg         lsfg-vk v2 from builds.lsfg-vk.dev "Latest release" (Bazzite's `ujust get-lsfg install` does the same)
  moonlight    Nonary/moonlight-qt, Moonlight with VRR from the author of Vibepollo; daily prereleases, AppImage

Safety: a download whose release publishes a SHA256SUMS file is checked against it before anything is replaced, and
the version that was there before is kept as <component>.prev so rollback() can put it back in one step.

Each component is unpacked into <assets>/<component>/ with a VERSION file. check() asks upstream at most once a day
(cached in updates.json); ensure() downloads when upstream is newer. Games set up with an older OptiScaler or
REFramework are refreshed by main.py after the game closes, never while it runs.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import urllib.request

from . import downloads

log = logging.getLogger("clawdeck.updates")

DAY = 24 * 3600
COMPONENTS = {
    "optiscaler": {"repo": "optiscaler/OptiScaler", "asset": "optiscaler_*.7z"},
    "optipatcher": {"url": "https://github.com/optiscaler/OptiPatcher/releases/download/rolling/OptiPatcher.asi"},
    "reframework": {"repo": "praydog/reframework-nightly", "asset": "REFramework.zip"},
    "lsfg": {"builds": "https://builds.lsfg-vk.dev/"},
    "moonlight": {"repo": "Nonary/moonlight-qt", "asset": "Moonlight-*-x86_64.AppImage", "prerelease": True},
}


def _get_text(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ClawDeck"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def lsfg_latest(page: str, base: str) -> tuple[str, str] | None:
    """(version, url) of the build builds.lsfg-vk.dev marks "Latest release" (same parsing as Bazzite's ujust)."""
    for line in page.splitlines():
        if "Latest release (" in line:
            m = re.search(r'href="([^"]*\.tar\.xz)"', line)
            if m:
                href = m.group(1)
                url = href if href.startswith("http") else base + href.lstrip("./")
                return os.path.basename(href).replace(".tar.xz", ""), url
    return None


class Updates:
    def __init__(self, assets_dir: str, state_path: str):
        self.assets = assets_dir
        self.state_path = state_path

    # ---- what is here ------------------------------------------------------------------------------------------
    def dir(self, comp: str) -> str:
        return os.path.join(self.assets, comp)

    def installed(self, comp: str) -> str | None:
        try:
            with open(os.path.join(self.dir(comp), "VERSION"), "r", encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None

    # ---- what is upstream -------------------------------------------------------------------------------------
    def _state(self) -> dict:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self, st: dict) -> None:
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)
        os.replace(self.state_path + ".tmp", self.state_path)

    def latest(self, comp: str) -> dict | None:
        c = COMPONENTS[comp]
        if "repo" in c:
            rel = downloads.newest_with_asset(c["repo"], c["asset"]) if c.get("prerelease") else downloads.release(c["repo"], None)
            asset = downloads.pick_asset(rel or {}, c["asset"])
            if not asset:
                return None
            return {"version": rel.get("tag_name"), "url": asset["browser_download_url"], "name": asset["name"],
                    "sha256": downloads.expected_sha256(rel, asset["name"])}
        if "url" in c:       # rolling build: the version is the release's update time
            rel = downloads.release("optiscaler/OptiPatcher", "rolling")
            a = downloads.pick_asset(rel, "OptiPatcher.asi")
            return {"version": (a or {}).get("updated_at") or rel.get("published_at") or "rolling",
                    "url": c["url"], "name": "OptiPatcher.asi"}
        found = lsfg_latest(_get_text(c["builds"]), c["builds"])
        return {"version": found[0], "url": found[1], "name": os.path.basename(found[1])} if found else None

    def check(self, force: bool = False) -> dict:
        """{component: {installed, latest, update}} with upstream asked at most once a day."""
        st = self._state()
        now = time.time()
        for comp in COMPONENTS:
            entry = st.get(comp, {})
            if force or now - entry.get("checked", 0) > DAY:
                try:
                    lat = self.latest(comp)
                    entry = {"checked": now, "latest": lat}
                except Exception as e:    # offline, rate limit: keep the last answer
                    log.warning("update check for %s failed: %s", comp, e)
                    entry = {**entry, "checked": now - DAY + 3600, "error": str(e)}
                st[comp] = entry
        self._save(st)
        out = {}
        for comp in COMPONENTS:
            lat = (st.get(comp) or {}).get("latest") or {}
            inst = self.installed(comp)
            held = (st.get("held") or {}).get(comp)
            rolled_from = self.previous(comp) if held else None
            out[comp] = {"installed": inst, "latest": lat.get("version"), "previous": self.previous(comp),
                         "update": bool(lat.get("version")) and lat.get("version") != inst and lat.get("version") != rolled_from}
        return out

    # ---- getting it ---------------------------------------------------------------------------------------------
    def ensure(self, comp: str, force: bool = False) -> dict:
        """Download the newest release when it is not here yet. Returns {version, dir, updated}. After a rollback the
        version rolled away from is skipped unless force."""
        st = self.check()
        if not force and comp in st and not st[comp]["update"] and self.installed(comp):
            return {"version": self.installed(comp), "dir": self.dir(comp), "updated": False}
        info = (self._state().get(comp) or {}).get("latest")
        have = self.installed(comp)
        if not info:
            if have:
                return {"version": have, "dir": self.dir(comp), "updated": False}
            raise ConnectionError(f"could not reach {comp}'s releases, and it is not downloaded yet")
        if have == info["version"]:
            return {"version": have, "dir": self.dir(comp), "updated": False}
        work = os.path.join(self.assets, ".downloads")
        archive = downloads.download(info["url"], os.path.join(work, info["name"]), timeout=300)
        if info.get("sha256"):
            got = downloads.sha256_of(archive)
            if got != info["sha256"]:
                os.remove(archive)
                raise ValueError(f"{comp} {info['version']}: download does not match its published checksum; not installed")
        fresh = self.dir(comp) + ".new"
        shutil.rmtree(fresh, ignore_errors=True)
        if comp in ("optipatcher", "moonlight"):
            os.makedirs(fresh, exist_ok=True)
            shutil.move(archive, os.path.join(fresh, "OptiPatcher.asi" if comp == "optipatcher" else "Moonlight.AppImage"))
        else:
            unpacked = downloads.extract(archive, os.path.join(work, comp))
            if comp == "optiscaler":
                root = _find_with(unpacked, "OptiScaler.dll")
            elif comp == "reframework":
                root = _find_with(unpacked, "dinput8.dll")
            else:
                root = unpacked
            if not root:
                raise FileNotFoundError(f"the {comp} release does not contain what ClawDeck expects")
            shutil.copytree(root, fresh)
            shutil.rmtree(os.path.join(work, comp), ignore_errors=True)
            try:
                os.remove(archive)
            except OSError:
                pass
        with open(os.path.join(fresh, "VERSION"), "w", encoding="utf-8") as f:
            f.write(info["version"] + "\n")
        prev = self.dir(comp) + ".prev"
        if os.path.isdir(self.dir(comp)):
            shutil.rmtree(prev, ignore_errors=True)
            os.replace(self.dir(comp), prev)          # keep the last working version for rollback()
        os.replace(fresh, self.dir(comp))
        st2 = self._state()
        if (st2.get("held") or {}).pop(comp, None) is not None:
            self._save(st2)
        log.info("%s updated to %s", comp, info["version"])
        return {"version": info["version"], "dir": self.dir(comp), "updated": True, "previous": have, "check": st.get(comp)}


    def previous(self, comp: str) -> str | None:
        try:
            with open(os.path.join(self.dir(comp) + ".prev", "VERSION"), "r", encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None

    def rollback(self, comp: str) -> dict:
        """Put the previous version back (the current one becomes the previous, so it can be undone too)."""
        prev = self.dir(comp) + ".prev"
        if not os.path.isdir(prev):
            raise FileNotFoundError(f"no earlier {comp} version to go back to")
        swap = self.dir(comp) + ".swap"
        shutil.rmtree(swap, ignore_errors=True)
        if os.path.isdir(self.dir(comp)):
            os.replace(self.dir(comp), swap)
        os.replace(prev, self.dir(comp))
        if os.path.isdir(swap):
            os.replace(swap, prev)
        st = self._state()
        st.setdefault("held", {})[comp] = self.installed(comp)   # don't auto-update straight back to the broken one
        self._save(st)
        return {"version": self.installed(comp), "previous": self.previous(comp)}

    def held(self, comp: str) -> str | None:
        return (self._state().get("held") or {}).get(comp)


def _find_with(root: str, filename: str) -> str | None:
    for d, _dirs, files in os.walk(root):
        if filename in files:
            return d
    return None
