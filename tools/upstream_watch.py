#!/usr/bin/env python3
"""upstream_watch.py - checks every project ClawDeck builds on, so a change upstream is seen before it breaks a Claw.

  python3 tools/upstream_watch.py                 report (markdown) to stdout; exit 1 if a canary failed
  python3 tools/upstream_watch.py --issue         also open / update the "Upstream watch" GitHub issue (CI, daily)
  python3 tools/upstream_watch.py --update-lock   after testing: record today's versions in assets/upstream.lock.json

Two kinds of checks:
  versions  the newest release / commit of each upstream, compared with assets/upstream.lock.json (the versions last
            tested with ClawDeck). New ones are listed so they get tested.
  canaries  the formats ClawDeck reads from upstream, read live: the xess-xmx-linux release asset, Handheld Daemon's
            MSI keys, Bazzite's Claw hardware handling, ClawLab's panel list, InputPlumber's Claw 8 EX map (the
            face-button fix is needed only while it is still `claw2`). A failed canary means a ClawDeck parser
            needs fixing before users notice.
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

LOCK = os.path.join(ROOT, "assets", "upstream.lock.json")
ISSUE_TITLE = "Upstream watch: new versions to test"

# name -> (kind, source, credit/why). kind: release | prerelease | commit | npm
UPSTREAMS = {
    "ClawLab VRR Range Fix": ("release", "Zendo-GIT/Intel-Handheld-and-ARC-GPU-laboratory",
                              "validated Claw panels for the 30-120 Hz VRR range (edid.py)"),
    "xess-xmx-linux": ("release", "HHHHanasak1/xess-xmx-linux", "XeSS on XMX driver (pinned: test, then bump)"),
    "xess-xmx-linux fork": ("commit", "IHulkSmashI/xess-xmx-linux", "our fork"),
    "Handheld Daemon": ("release", "hhd-dev/hhd", "TDP, fans, controller when installed"),
    "InputPlumber": ("release", "ShadowBlip/InputPlumber", "controller on current Bazzite"),
    "PowerStation": ("release", "ShadowBlip/PowerStation", "power on current Bazzite"),
    "Decky Loader": ("release", "SteamDeckHomebrew/decky-loader", "plugin host"),
    "@decky/ui": ("npm", "@decky/ui", "Steam UI components and typings"),
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

    def xess_asset():
        rel = _json("https://api.github.com/repos/HHHHanasak1/xess-xmx-linux/releases/latest")
        names = [a["name"] for a in rel.get("assets", [])]
        ok = any(n.startswith("xess-xmx-linux-") and n.endswith(".zip") for n in names)
        return ok, ", ".join(names) or "no assets"
    check("xess-xmx-linux release still ships xess-xmx-linux-*.zip", xess_asset)

    def claw8ex_map():
        s = raw("ShadowBlip/InputPlumber", "rootfs/usr/share/inputplumber/devices/50-msi_claw8_ex_ai_cg3em.yaml")
        stock = "capability_map_id: claw2" in s
        return True, "still claw2: ClawDeck's face-button override is still needed" if stock else "changed upstream: check whether buttons.py's override can go"
    check("InputPlumber's Claw 8 EX map", claw8ex_map)

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
