"""ClawDeck's per-game OptiScaler list (assets/games.json, built by tools/gen_games.py): what OptiClick looks up on
Windows, from an open source anyone can check and extend.

For a game, match() finds its entry by the game's executables (OptiScaler's own exe names, and Unreal projects as
<project>-Win64-Shipping.exe / -WinGDK-Shipping.exe). recommend() turns it into the Per-game setup for the Claw:

  * OptiScaler through Decky Framegen with XeSS on XMX and XeSS frame generation (Intel's own, as on Windows);
  * the game's OptiScaler settings from the list, as Decky Framegen launch variables ([Section] Key = Section_Key);
  * Capcom RE Engine games: REFramework (dinput8.dll) next to OptiScaler, or Capcom's anti-tamper stops the game.
"""
from __future__ import annotations

import json
import os

DEPTH = 6
KEY_RE = r"^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+$"


def load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {"games": [], "re_engine_exes": []}
    return doc if isinstance(doc.get("games"), list) else {"games": [], "re_engine_exes": []}


def exes_in(game_dir: str | None) -> list[str]:
    """Lower-case .exe names within DEPTH folders of the install folder."""
    if not game_dir or not os.path.isdir(game_dir):
        return []
    base = game_dir.rstrip(os.sep).count(os.sep)
    out = []
    for r, d, f in os.walk(game_dir):
        if r.count(os.sep) - base >= DEPTH:
            d[:] = []
        out += [x.lower() for x in f if x.lower().endswith(".exe")]
    return sorted(set(out))


def match(doc: dict, exes: list[str]) -> dict | None:
    have = set(exes)
    for g in doc.get("games", []):
        if have & {e.lower() for e in g.get("exes", [])}:
            return g
        for ue in g.get("ue_exes", []):
            if {f"{ue.lower()}-win64-shipping.exe", f"{ue.lower()}-wingdk-shipping.exe"} & have:
                return g
    return None


def is_re_engine(doc: dict, exes: list[str]) -> bool:
    return bool(set(exes) & {e.lower() for e in doc.get("re_engine_exes", [])})


def _has(game_dir: str | None, names: tuple[str, ...]) -> bool:
    if not game_dir or not os.path.isdir(game_dir):
        return False
    base = game_dir.rstrip(os.sep).count(os.sep)
    for r, d, f in os.walk(game_dir):
        if r.count(os.sep) - base >= DEPTH:
            d[:] = []
        lo = {x.lower() for x in f}
        if any(n in lo or (n.endswith("*") and any(x.startswith(n[:-1]) for x in lo)) for n in names):
            return True
    return False


def recommend(doc: dict, game_dir: str | None) -> dict:
    """What 'Use the recommended setup' sets for this game on a Claw, and why (shown before anything is written).

    In order: a game with its own XeSS uses it (on XMX through xess-xmx-linux, nothing to add); a Capcom RE Engine
    game gets OptiScaler + REFramework (its anti-tamper stops the game when the FSR DLL is swapped); a game with
    FSR 3.1+ DLLs gets xess-xmx-linux's FSR-on-XeSS (no OptiScaler needed); anything else (DLSS games) gets
    OptiScaler with XeSS and XeSS frame generation. The list's OptiScaler settings come along whenever OptiScaler does.
    """
    exes = exes_in(game_dir)
    g = match(doc, exes)
    re_engine = is_re_engine(doc, exes)
    ini = dict((g or {}).get("ini") or {})
    ini.update(((g or {}).get("when") or {}).get("xefg") or {})          # frame generation goes out through XeSS-FG
    later = sorted(((g or {}).get("later") or {}).keys())
    xess = _has(game_dir, ("libxess*",))
    # where OptiScaler's frame generation comes from: the game's own DLSS FG, its FSR FG, else off (the user can pick
    # "any game" in Per-game; it can glitch the HUD, so it isn't the default)
    ofg = ("dlssg" if _has(game_dir, ("nvngx_dlssg.dll", "sl.dlss_g.dll")) else
           "fsrfg" if _has(game_dir, ("amd_fidelityfx_framegeneration_dx12.dll", "ffx_frameinterpolation_x64.dll")) else "off")
    fsr31 = _has(game_dir, ("amd_fidelityfx_upscaler_dx12.dll", "amd_fidelityfx_dx12.dll", "ffx_fsr2_api_x64.dll"))
    dlss = _has(game_dir, ("nvngx_dlss.dll", "sl.dlss.dll"))
    # when the user turns frame generation on: the game's own (DLSS FG, FSR FG) if it has one, else OptiScaler's
    fg_source = ofg if ofg != "off" else "upscaler"
    if xess and not re_engine:
        route, launch, opti = "own_xess", {}, False
        why = "This game ships XeSS: use XeSS in its graphics menu; it runs on the XMX cores by itself."
    elif re_engine:
        route, opti = "re_engine", True
        launch = {"opti": "xess", "opti_fg": ofg, "opti_ini": ini, "reframework": True}
        why = ("Capcom RE Engine: OptiScaler (XeSS on the XMX cores) with REFramework, which keeps Capcom's "
               "anti-tamper from stopping the game. Pick DLSS in the game's graphics menu.")
    else:
        # DLSS and FSR games alike, as OptiClick does: OptiScaler takes the game's DLSS or FSR and runs XeSS (or FSR
        # 4.1.1) on the XMX cores. FSR-only games used xess-xmx-linux's own FSR route before; OptiScaler offers the
        # same XeSS plus FSR 4.1.1, with one set of choices for every game (maintainer, 2026-11-04).
        route, opti = "optiscaler", True
        launch = {"opti": "xess", "opti_fg": ofg, "opti_ini": ini}
        menu = "DLSS" if dlss else "FSR" if fsr31 else "DLSS (or FSR)"
        why = f"OptiScaler turns the game's {menu} into XeSS on the XMX cores. Pick {menu} in the game's graphics menu."
    reasons = [why]
    if g and opti and (ini or later):
        reasons.append(f"{len(ini)} fix(es) from OptiScaler's own list for this game ({', '.join(k.split('_', 1)[1] for k in ini)})" if ini
                       else "OptiScaler's list has this game; no extra settings needed")
    if later and opti:
        reasons.append(f"Not yet possible with the OptiScaler Decky Framegen ships ({doc.get('ini_version') or '?'}): {', '.join(later)}")
    if (g or {}).get("quirks") and opti:
        reasons.append(f"Newer OptiScaler also fixes this game in code ({', '.join(g['quirks'])})")
    return {
        "route": route, "known": bool(g), "frame_gen": ofg if opti else None, "fg_source": fg_source, "id": (g or {}).get("id"), "name": (g or {}).get("name"),
        "re_engine": re_engine, "optiscaler": opti, "ini": ini if opti else {}, "later": later if opti else [],
        "launch": launch, "reasons": reasons, "ini_version": doc.get("ini_version"),
        "source_commit": doc.get("optiscaler_commit"), "exes": exes[:8],
        "main_exes": [e.lower() for e in (g or {}).get("exes", [])] + [e for e in exes if e in {x.lower() for x in doc.get("re_engine_exes", [])}],
    }


UPSCALER_FILES = {
    "DLSS": ("nvngx_dlss.dll", "sl.dlss.dll", "nvngx_dlssd.dll"),
    "FSR": ("amd_fidelityfx_upscaler_dx12.dll", "amd_fidelityfx_dx12.dll", "ffx_fsr2_api_x64.dll", "ffx_fsr2_api_dx12_x64.dll",
            "ffx_fsr3upscaler_x64.dll", "amd_fidelityfx_vk.dll"),
    "XeSS": ("libxess*",),
}


def support(doc: dict, game_dir: str | None) -> dict:
    """Can ClawDeck set this game up? Yes when it is in the per-game list, or ships an upscaler OptiScaler takes over
    (DLSS, FSR 2/3/3.1) or XeSS of its own. Unreal games often keep DLSS in a plugin folder, which DEPTH covers."""
    if not game_dir or not os.path.isdir(game_dir):
        return {"ok": False, "via": []}
    via = [k for k, names in UPSCALER_FILES.items() if _has(game_dir, names)]
    if match(doc, exes_in(game_dir)):
        via.append("list")
    return {"ok": bool(via), "via": via}
