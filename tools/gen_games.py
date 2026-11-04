#!/usr/bin/env python3
"""gen_games.py - build assets/games.json, ClawDeck's per-game OptiScaler list, from OptiScaler's own sources.

OptiClick's per-game database is private (an authenticated web service), so ClawDeck builds an open one:

  1. OptiScaler's GameConfigs.cpp (main branch): the per-game settings its developers ship in code, newer than the
     OptiScaler that Decky Framegen bundles (0.9.4). ClawDeck passes them as Decky Framegen launch variables
     (Section_Key=value), so a game gets them today. Settings wrapped in OnNvidia(...) are dropped (the Claw is
     Intel); OnNonNvidia(...) ones are kept; WithXeFG(...) / WithUpscalerFGInput(...) keep their condition.
  2. Config.cpp: which [Section] Key each setting is in OptiScaler.ini.
  3. assets/games.claw.json: what ClawDeck adds by hand, by game id (Steam app id, REFramework, notes tested on a
     Claw). It wins over 1.

  python3 tools/gen_games.py GameConfigs.cpp Config.cpp --ini OptiScaler.ini [--commit SHA]   writes assets/games.json

--ini is the OptiScaler.ini of the version Decky Framegen ships: Decky Framegen only rewrites keys that are already in
it, so a setting newer than that version is listed under "later" (it applies once Decky Framegen ships it).
OptiScaler is GPL-3.0; the list is data derived from it and credited in CREDITS.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def ini_keys(config_cpp: str) -> dict[str, tuple[str, str]]:
    """Config member -> (section, key), from Config.cpp's reads: X.set_from_config(readBool("Section", "Key"))."""
    out = {}
    for m in re.finditer(r'(\w+)\.set_from_config\(\s*read\w*\(\s*"(\w+)"\s*,\s*"(\w+)"', config_cpp):
        out.setdefault(m.group(1), (m.group(2), m.group(3)))
    return out


def _split_top(s: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in s:
        if ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _value(v: str):
    v = v.strip()
    if v in ("true", "false"):
        return v
    if re.fullmatch(r"-?\d+(\.\d+)?f?", v):
        return v.rstrip("f")
    return None                                    # an expression we don't translate (enum etc.): skipped


def _defaults(body: str, keys: dict) -> tuple[dict, list[str]]:
    ini, skipped = {}, []
    for item in _split_top(body):
        cond = None
        m = re.fullmatch(r"(\w+)\((.*)\)", item, re.S)
        if m:
            wrap, inner = m.group(1), m.group(2)
            if wrap == "OnNvidia":
                continue
            cond = None if wrap == "OnNonNvidia" else {"WithXeFG": "xefg", "WithUpscalerFGInput": "upscaler_fg_input"}.get(wrap, wrap)
            items = _split_top(inner)
        else:
            items = [item]
        for it in items:
            mm = re.fullmatch(r"(\w+)\s*=\s*(.+)", it, re.S)
            if not mm:
                continue
            member, val = mm.group(1), _value(mm.group(2))
            if member not in keys or val is None:
                skipped.append(it)
                continue
            sec, key = keys[member]
            name = f"{sec}_{key}"
            if cond:
                ini.setdefault("when", {}).setdefault(cond, {})[name] = val
            else:
                ini[name] = val
    return ini, skipped


def games(gameconfigs_cpp: str, keys: dict) -> tuple[list[dict], list[str]]:
    start = gameconfigs_cpp.index("gameConfigs = {")
    text = gameconfigs_cpp[start:]
    out, skipped = [], []
    # each entry: comment lines (name, then why), then { .exes / .ueExes / .defaults / .quirks }
    for m in re.finditer(r"((?:[ \t]*//[^\n]*\n)+)[ \t]*\{(.*?)\n[ \t]*\},", text, re.S):
        comments = [c.strip()[2:].strip() for c in m.group(1).strip().splitlines()]
        while len(comments) > 1 and comments[0].endswith(","):      # a name list wrapped over two comment lines
            comments[0:2] = [comments[0] + " " + comments[1]]
        body = m.group(2)
        def field(name):
            f = re.search(r"\." + name + r"\s*=\s*\{(.*?)\}\s*,", body, re.S)
            return f.group(1) if f else None
        exes = re.findall(r'"([^"]+)"', field("exes") or "")
        ue = re.findall(r'"([^"]+)"', field("ueExes") or "")
        if not exes and not ue:
            continue
        ini, sk = _defaults(field("defaults") or "", keys)
        skipped += [f"{comments[0]}: {s}" for s in sk]
        quirks = [q.strip() for q in (field("quirks") or "").split(",") if q.strip()]
        out.append({"name": comments[0], "why": " ".join(comments[1:]) or None, "exes": exes, "ue_exes": ue,
                    "ini": {k: v for k, v in ini.items() if k != "when"}, "when": ini.get("when", {}),
                    "quirks": quirks})
    return out, skipped


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("gameconfigs")
    ap.add_argument("config")
    ap.add_argument("--commit", default=None)
    ap.add_argument("--ini", required=True)
    ap.add_argument("--ini-version", default=None)
    a = ap.parse_args()
    keys = ini_keys(open(a.config, encoding="utf-8").read())
    gs, skipped = games(open(a.gameconfigs, encoding="utf-8").read(), keys)
    have, sec = set(), None
    for line in open(a.ini, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            sec = line[1:-1].replace("-", "").replace(".", "")
        elif sec and "=" in line and not line.startswith(";"):
            have.add(f"{sec}_{line.split('=', 1)[0].strip()}")
    for g in gs:
        later = sorted(k for k in g["ini"] if k not in have)
        g["later"] = {k: g["ini"].pop(k) for k in later}
        for cond, vals in g["when"].items():
            for k in [k for k in vals if k not in have]:
                g["later"][k] = vals.pop(k)
    claw_path = os.path.join(ROOT, "assets", "games.claw.json")
    claw = json.load(open(claw_path, encoding="utf-8")) if os.path.exists(claw_path) else {"games": {}}
    by_id = {}
    for g in gs:
        gid = slug(g["name"])
        by_id[gid] = {"id": gid, **g, "source": "optiscaler"}
    for gid, extra in claw.get("games", {}).items():
        base = by_id.get(gid, {"id": gid, "name": extra.get("name", gid), "exes": [], "ue_exes": [], "ini": {}, "when": {}, "quirks": [], "source": "clawdeck"})
        base = {**base, **{k: v for k, v in extra.items() if k != "ini"}, "ini": {**base.get("ini", {}), **extra.get("ini", {})}}
        by_id[gid] = base
    doc = {"_comment": "Generated by tools/gen_games.py from OptiScaler's GameConfigs.cpp and Config.cpp (GPL-3.0) "
                       "plus assets/games.claw.json. Do not edit by hand. Derived from OptiScaler, so this file (unlike the rest of "
                       "ClawDeck, MIT) is under GPL-3.0: https://github.com/optiscaler/OptiScaler/blob/master/LICENSE",
           "schema": 1, "optiscaler_commit": a.commit, "ini_version": a.ini_version,
           "re_engine_exes": sorted(claw.get("re_engine_exes", [])), "games": sorted(by_id.values(), key=lambda g: g["name"].lower())}
    with open(os.path.join(ROOT, "assets", "games.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"{len(doc['games'])} games; {len(skipped)} settings not translated", file=sys.stderr)
    for s in skipped:
        print("  skipped:", s, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
