#!/usr/bin/env python3
"""upstream_watch.py - checks every project ClawDeck builds on, so a change upstream is seen before it breaks a Claw.

  python3 tools/upstream_watch.py                 report (markdown) to stdout; exit 1 if a canary failed
  python3 tools/upstream_watch.py --issue         also open / update the "Upstream watch" GitHub issue (CI, daily)
  python3 tools/upstream_watch.py --update-lock   after testing: record today's versions in assets/upstream.lock.json

Two kinds of checks:
  versions  the newest release / commit of each upstream, compared with assets/upstream.lock.json (the versions last
            tested with ClawDeck). New ones are listed so they get tested; ClawDeck's runtime updater already
            fetches OptiScaler, OptiPatcher, REFramework, lsfg-vk and Moonlight VRR for users.
  canaries  the formats ClawDeck reads from upstream, read live with ClawDeck's own parsers: OptiScaler's
            compatibility list, OptiPatcher's executable list, the lsfg-vk builds page, Handheld Daemon's MSI keys,
            Bazzite's Claw hardware handling, Moonlight VRR's checksums. A failed canary means a ClawDeck parser needs
            fixing before users notice.
Only the standard library; GITHUB_TOKEN (set in CI) raises the API limit.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
from clawdeck import compat, updates  # noqa: E402

LOCK = os.path.join(ROOT, "assets", "upstream.lock.json")
ISSUE_TITLE = "Upstream watch: new versions to test"

# name -> (kind, source, credit/why). kind: release | prerelease | commit | npm | page
UPSTREAMS = {
    "OptiScaler": ("release", "optiscaler/OptiScaler", "XeSS for DLSS/FSR games (auto-updated for users)"),
    "OptiPatcher": ("release:rolling", "optiscaler/OptiPatcher", "DLSS inputs without spoofing (auto-updated)"),
    "REFramework": ("release", "praydog/reframework-nightly", "RE Engine games (auto-updated)"),
    "lsfg-vk": ("page", "https://builds.lsfg-vk.dev/", "Lossless Scaling frame generation (auto-updated)"),
    "Moonlight VRR": ("prerelease", "Nonary/moonlight-qt", "streaming client (auto-updated)"),
    "Vibepollo": ("prerelease", "Nonary/Vibepollo", "streaming host on the PC"),
    "ClawLab VRR Range Fix": ("release", "Zendo-GIT/Intel-Handheld-and-ARC-GPU-laboratory",
                              "validated Claw panels for the 30-120 Hz VRR range (edid.py)"),
    "xess-xmx-linux": ("release", "HHHHanasak1/xess-xmx-linux", "XeSS on XMX driver (pinned: test, then bump)"),
    "xess-xmx-linux fork": ("commit", "IHulkSmashI/xess-xmx-linux", "our fork"),
    "Handheld Daemon": ("release", "hhd-dev/hhd", "TDP, fans, controller when installed"),
    "InputPlumber": ("release", "ShadowBlip/InputPlumber", "controller on current Bazzite"),
    "PowerStation": ("release", "ShadowBlip/PowerStation", "power on current Bazzite"),
    "Decky Loader": ("release", "SteamDeckHomebrew/decky-loader", "plugin host"),
    "@decky/ui": ("npm", "@decky/ui", "Steam UI components and typings"),
    "gamescope-session": ("commit", "ChimeraOS/gamescope-session", "Game Mode display variables"),
    "Bazzite": ("commit", "ublue-os/bazzite", "the OS"),
}


def _get(url: str, accept: str = "application/vnd.github+json") -> bytes:
    headers = {"User-Agent": "ClawDeck-upstream-watch", "Accept": accept}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _json(url: str):
    return json.loads(_get(url, "application/json" if "registry.npmjs.org" in url else "application/vnd.github+json"))


def version_of(kind: str, src: str) -> str:
    if kind == "release":
        return _json(f"https://api.github.com/repos/{src}/releases/latest")["tag_name"]
    if kind.startswith("release:"):
        rel = _json(f"https://api.github.com/repos/{src}/releases/tags/{kind.split(':', 1)[1]}")
        return max((a.get("updated_at") or "" for a in rel.get("assets", [])), default=rel.get("published_at") or "?")
    if kind == "prerelease":
        rels = _json(f"https://api.github.com/repos/{src}/releases?per_page=5")
        return next(r["tag_name"] for r in rels if not r.get("draft"))
    if kind == "commit":
        return _json(f"https://api.github.com/repos/{src}/commits?per_page=1")[0]["sha"][:12]
    if kind == "npm":
        return _json(f"https://registry.npmjs.org/{src}/latest")["version"]
    if kind == "page":
        found = updates.lsfg_latest(_get(src, "text/html").decode("utf-8", "replace"), src)
        return found[0] if found else "?"
    raise ValueError(kind)


def raw(repo: str, path: str, branch: str = "HEAD") -> str:
    return _get(f"https://raw.githubusercontent.com/{repo}/{branch}/{path}", "text/plain").decode("utf-8", "replace")


def canaries() -> list[tuple[str, bool, str]]:
    out = []

    def check(name, fn):
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"error: {e}"
        out.append((name, ok, detail))

    def wiki():
        rows = compat.parse_wiki(_get(compat.WIKI, "text/plain").decode("utf-8", "replace"))
        cp = compat.lookup(rows, "Cyberpunk 2077")
        return len(rows) > 400 and cp is not None and cp["status"] == "works", f"{len(rows)} games parsed"
    check("OptiScaler compatibility list parses", wiki)

    def patcher():
        n = compat.parse_optipatcher(_get(compat.OPTIPATCHER_SRC, "text/plain").decode("utf-8", "replace"))
        return len(n) > 100, f"{len(n)} executables"
    check("OptiPatcher executable list parses", patcher)

    def lsfg():
        found = updates.lsfg_latest(_get("https://builds.lsfg-vk.dev/", "text/html").decode("utf-8", "replace"),
                                    "https://builds.lsfg-vk.dev/")
        return found is not None and found[1].endswith(".tar.xz"), found[0] if found else "no 'Latest release' link"
    check("lsfg-vk builds page has a latest release", lsfg)

    def optiscaler_asset():
        rel = _json("https://api.github.com/repos/optiscaler/OptiScaler/releases/latest")
        names = [a["name"] for a in rel.get("assets", [])]
        ok = any(n.lower().startswith("optiscaler_") and n.endswith(".7z") for n in names)
        return ok, ", ".join(names[:3])
    check("OptiScaler release still ships Optiscaler_*.7z", optiscaler_asset)

    def moonlight():
        rels = _json("https://api.github.com/repos/Nonary/moonlight-qt/releases?per_page=5")
        rel = next(r for r in rels if not r.get("draft"))
        names = [a["name"] for a in rel.get("assets", [])]
        ok = any(n.endswith("-x86_64.AppImage") for n in names) and "SHA256SUMS" in names
        return ok, f"{rel['tag_name']}: AppImage + SHA256SUMS" if ok else f"{rel['tag_name']}: {names}"
    check("Moonlight VRR ships an AppImage and SHA256SUMS", moonlight)

    def clawlab_panels():
        from clawdeck import edid
        src = raw("Zendo-GIT/Intel-Handheld-and-ARC-GPU-laboratory",
                  "utilities/msi-claw-8-intel-vrr-range-fix/MSI-Claw-VRR-Fix.ps1").lower()
        missing = [k for k, p in edid.PANELS.items() if p["physical_sha256"] not in src]
        offsets = "minimum = 95; maximum = 96" in src and "minimum = 142; maximum = 143" in src
        return not missing and offsets, ("same panels and range offsets" if not missing and offsets else
                                         f"changed: panels {missing or 'ok'}, offsets {'ok' if offsets else 'moved'}")
    check("ClawLab still validates the panels and range bytes edid.py uses", clawlab_panels)

    def hhd_keys():
        msi = raw("hhd-dev/hhd", "src/adjustor/drivers/msi/settings.yml")
        bat = raw("hhd-dev/hhd", "src/adjustor/drivers/battery/battery.yml")
        ok = all(k in msi for k in ("custom:", "st0:", "st88:", "fan:")) and all(k in bat for k in ("charge_limit:", "p80:"))
        return ok, "tdp.msi.*, fan points, charge_limit present" if ok else "a key ClawDeck uses is gone"
    check("Handheld Daemon keys ClawDeck uses", hhd_keys)

    def bazzite():
        s = raw("ublue-os/bazzite", "system_files/desktop/shared/usr/libexec/hwsupport/steamos-manager-hardware")
        return "Claw" in s, "Claws still use steamos-manager TDP" if "Claw" in s else "Claw handling changed"
    check("Bazzite's Claw hardware handling", bazzite)

    def gamescope():
        s = raw("ChimeraOS/gamescope-session", "usr/share/gamescope-session-plus/gamescope-session-plus")
        keys = ("ADAPTIVE_SYNC", "CUSTOM_REFRESH_RATES", "ENABLE_GAMESCOPE_HDR", "GAMESCOPE_HDR_NITS")
        missing = [k for k in keys if k not in s]
        return not missing, "all Display tab variables present" if not missing else f"missing: {missing}"
    check("gamescope-session reads the Display tab's variables", gamescope)
    return out


def report(lock: dict) -> tuple[str, dict, bool, bool]:
    now, rows, changed = {}, [], False
    for name, (kind, src, why) in UPSTREAMS.items():
        try:
            v = version_of(kind, src)
        except Exception as e:
            v = f"error: {e}"
        now[name] = v
        old = lock.get(name)
        new = old is not None and old != v and not v.startswith("error")
        changed |= new
        mark = "🆕" if new else ("⚠️" if v.startswith("error") else "✓")
        rows.append(f"| {mark} | {name} | `{old or '—'}` | `{v}` | {why} |")
    can = canaries()
    failed = [c for c in can if not c[1]]
    md = ["## Versions", "", "| | Project | Last tested | Now | Used for |", "|---|---|---|---|---|", *rows, "",
          "## Canaries (ClawDeck's parsers against live upstream data)", ""]
    md += [f"- {'✅' if ok else '❌'} **{n}**: {d}" for n, ok, d in can]
    md += ["", "To accept the new versions after testing on a Claw: `python3 tools/upstream_watch.py --update-lock`."]
    return "\n".join(md), now, bool(failed) or changed, bool(failed)


def issue(body: str) -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    found = _json(f"https://api.github.com/repos/{repo}/issues?state=open&per_page=50")
    existing = next((i for i in found if i.get("title") == ISSUE_TITLE and "pull_request" not in i), None)
    data = json.dumps({"title": ISSUE_TITLE, "body": body}).encode()
    url = existing["url"] if existing else f"https://api.github.com/repos/{repo}/issues"
    req = urllib.request.Request(url, data=data, method="PATCH" if existing else "POST",
                                 headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                                          "Accept": "application/vnd.github+json", "User-Agent": "ClawDeck-upstream-watch"})
    urllib.request.urlopen(req, timeout=30).read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", action="store_true")
    ap.add_argument("--update-lock", action="store_true")
    a = ap.parse_args()
    try:
        with open(LOCK, "r", encoding="utf-8") as f:
            lock = json.load(f)
    except (OSError, ValueError):
        lock = {}
    md, now, attention, failed = report(lock)
    print(md)
    if a.update_lock:
        with open(LOCK, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in now.items() if not v.startswith("error")}, f, indent=2, sort_keys=True)
            f.write("\n")
    if a.issue and attention:
        issue(md)
    return 1 if failed and not a.update_lock else 0   # a red run is the alarm: a parser needs fixing


if __name__ == "__main__":
    sys.exit(main())
