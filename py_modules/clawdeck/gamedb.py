"""ClawDeck's per-game OptiScaler list (assets/games.json, built by tools/gen_games.py): what ClawDeck looks up on
Windows, from an open source anyone can check and extend.

For a game, match() finds its entry by the game's executables (OptiScaler's own exe names, and Unreal projects as
<project>-Win64-Shipping.exe / -WinGDK-Shipping.exe). recommend() turns it into the Per-game setup for the Claw:

  * ClawDeck's own OptiScaler (optiscaler.py) with XeSS on XMX and XeSS frame generation (Intel's own, as on Windows);
  * the game's OptiScaler settings from the list, written into its OptiScaler.ini ([Section] Key = Section_Key);
  * Capcom RE Engine games: the Capcom setup (onehoon's REFramework as dinput8.dll, OptiScaler v10 in the
    OptiScaler/ subfolder, the game's DLSS frame generation turned into XeSS-FG; launchenv.capcom), or Capcom's
    anti-tamper stops the game. games.json "capcom" says which of them were tested on a Claw.
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


def _ours(folder: str) -> set[str]:
    """Files ClawDeck's OptiScaler added below this folder (its manifest), which the game didn't ship, as absolute
    paths: OptiScaler brings libxess.dll and AMD's FSR DLLs (in OptiScaler/ with v10), and counting them turned
    CONTROL into "ships XeSS" once set up (2026-11-04). A file the game had and OptiScaler replaced (backed up) still
    counts as the game's."""
    try:
        with open(os.path.join(folder, ".clawdeck-optiscaler", "manifest.json"), "r", encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return set()
    backed = {str(x).lower() for x in m.get("backed") or []}
    return {os.path.join(folder, *str(x).split("/")).lower() for x in m.get("added") or [] if str(x).lower() not in backed}


def _has(game_dir: str | None, names: tuple[str, ...]) -> bool:
    if not game_dir or not os.path.isdir(game_dir):
        return False
    base = game_dir.rstrip(os.sep).count(os.sep)
    ours: set[str] = set()
    for r, d, f in os.walk(game_dir):
        if r.count(os.sep) - base >= DEPTH:
            d[:] = []
        ours |= _ours(r)                       # os.walk is top-down: a manifest is read before the folders below it
        lo = {x.lower() for x in f if os.path.join(r, x).lower() not in ours}
        if any(n in lo or (n.endswith("*") and any(x.startswith(n[:-1]) for x in lo)) for n in names):
            return True
    return False


def capcom_tested(doc: dict, exes: list[str]) -> str | None:
    """Where the Capcom setup was seen working for this game (games.json "capcom" "tested"), else None."""
    tested = {k.lower(): v for k, v in ((doc.get("capcom") or {}).get("tested") or {}).items()}
    return next((tested[e] for e in exes if e in tested), None)


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
    fsr31 = _has(game_dir, FSR_FILES)                  # any FSR 2 / 3 / 3.1 OptiScaler can take over
    dlss = _has(game_dir, ("nvngx_dlss.dll", "sl.dlss.dll"))
    # when the user turns frame generation on: the game's own (DLSS FG, FSR FG) if it has one, else OptiScaler's
    fg_source = ofg if ofg != "off" else "upscaler"
    ships = shipped(game_dir)
    if xess and not re_engine:
        route, launch, opti = "own_xess", {}, False
        own = ["XeSS"] + (["XeSS frame generation"] if "XeSS-FG" in ships["fg"] else []) + (["XeLL"] if "XeLL" in ships["latency"] else [])
        why = (f"This game ships {', '.join(own[:-1]) + ' and ' + own[-1] if len(own) > 1 else own[0]}: use them in its graphics "
               "menu; they run on the XMX cores by themselves, nothing to add.")
    elif re_engine:
        # the Capcom setup: the game's own DLSS frame generation is what OptiScaler turns into XeSS-FG, so it
        # is the frame generation source whatever files the game ships; the wiki's Spoofing_Dxgi=false would hide
        # DLSS-G (it needs the Nvidia spoofing), so it is left out here
        route, opti = "re_engine", True
        ofg = fg_source = "dlssg"
        ini.pop("Spoofing_Dxgi", None)
        launch = {"opti": "xess", "opti_fg": ofg, "opti_ini": ini, "reframework": True}
        why = ("Capcom RE Engine: OptiScaler (XeSS on the XMX cores) with REFramework, which keeps Capcom's "
               "anti-tamper from stopping the game. In the game's graphics menu turn on DLSS and DLSS Frame "
               "Generation: OptiScaler makes them XeSS and XeSS frame generation.")
    else:
        # DLSS and FSR games alike: OptiScaler takes the game's DLSS or FSR and runs XeSS (or FSR
        # 4.1.1) on the XMX cores. FSR-only games used xess-xmx-linux's own FSR route before; OptiScaler offers the
        # same XeSS plus FSR 4.1.2, with one set of choices for every game (maintainer, 2026-11-04).
        route, opti = "optiscaler", True
        launch = {"opti": "xess", "opti_fg": ofg, "opti_ini": ini}
        menu = "DLSS" if dlss else "FSR" if fsr31 else "DLSS (or FSR)"
        why = f"OptiScaler turns the game's {menu} into XeSS on the XMX cores. Pick {menu} in the game's graphics menu."
    reasons = [why]
    if g and opti and (ini or later):
        reasons.append(f"{len(ini)} fix(es) from OptiScaler's own list for this game ({', '.join(k.split('_', 1)[1] for k in ini)})" if ini
                       else "OptiScaler's list has this game; no extra settings needed")
    if later and opti:
        reasons.append(f"Not yet possible with the OptiScaler ClawDeck pins ({doc.get('ini_version') or '?'}): {', '.join(later)}")
    if (g or {}).get("quirks") and opti and not re_engine:
        reasons.append(f"Newer OptiScaler also fixes this game in code ({', '.join(g['quirks'])})")
    tested = capcom_tested(doc, exes) if re_engine else None
    if re_engine:
        reasons.append(f"The Capcom setup, tested on a Claw: {tested}" if tested else
                       "The Capcom setup; not tested on a Claw in this game yet")
    return {
        "route": route, "known": bool(g), "frame_gen": ofg if opti else None, "fg_source": fg_source, "id": (g or {}).get("id"), "name": (g or {}).get("name"),
        "re_engine": re_engine, "capcom": re_engine, "capcom_tested": tested, "optiscaler": opti, "ini": ini if opti else {}, "later": later if opti else [],
        "launch": launch, "reasons": reasons, "ships": ships, "upscaler": launch.get("opti") if opti else None, "ini_version": doc.get("ini_version"),
        "source_commit": doc.get("optiscaler_commit"), "exes": exes[:8],
        "main_exes": [e.lower() for e in (g or {}).get("exes", [])] + [e for e in exes if e in {x.lower() for x in doc.get("re_engine_exes", [])}],
    }


# the FSR upscaler libraries OptiScaler takes over (the names in its own sources, 2026-11-05): FSR 2 and 3 through
# their API DLLs, FSR 3.1+ through AMD's FidelityFX DLLs, DirectX 12 and Vulkan. A game with FSR 2/3 built into its
# exe (no DLL) can't be taken over by OptiScaler or xess-xmx-linux.
FSR_FILES = ("ffx_fsr2_api_x64.dll", "ffx_fsr2_api_dx12_x64.dll", "ffx_fsr3upscaler_x64.dll", "amd_fidelityfx_dx12.dll",
             "amd_fidelityfx_upscaler_dx12.dll", "amd_fidelityfx_loader_dx12.dll", "amd_fidelityfx_vk.dll",
             "amd_fidelityfx_upscaler_vk.dll", "amd_fidelityfx_loader_vk.dll")

# what a game brings along inspects a game before it installs anything
SHIPPED = {
    "upscaler": {"DLSS": ("nvngx_dlss.dll", "sl.dlss.dll"), "FSR": FSR_FILES, "XeSS": ("libxess.dll", "libxess_dx11.dll")},
    "fg": {"DLSS-G": ("nvngx_dlssg.dll", "sl.dlss_g.dll"), "FSR-FG": ("amd_fidelityfx_framegeneration_dx12.dll",
           "ffx_frameinterpolation_x64.dll"), "XeSS-FG": ("libxess_fg.dll",)},
    "latency": {"Reflex": ("sl.reflex.dll", "nvlowlatencyvk.dll"), "XeLL": ("libxell.dll",), "Anti-Lag 2": ("amd_antilag2*",)},
}


def shipped(game_dir: str | None) -> dict:
    """{"upscaler": [...], "fg": [...], "latency": [...]}: the upscalers, frame generation and low-latency libraries
    in the game's folder (not counting what ClawDeck's OptiScaler added)."""
    return {kind: [name for name, files in d.items() if _has(game_dir, files)] for kind, d in SHIPPED.items()}


UPSCALER_FILES = {
    "DLSS": ("nvngx_dlss.dll", "sl.dlss.dll", "nvngx_dlssd.dll"),
    "FSR": FSR_FILES,
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


def is_dx12(game_dir: str | None, exes: list[str] | None = None) -> bool:
    """FSR 4 through OptiScaler needs a DirectX 12 game. True when the game ships a DX12-only library (FSR 3.1's
    dx12 DLLs, D3D12 Agility SDK, Streamline) or its main exe imports d3d12.dll (looked for in its first 32 MB)."""
    if not game_dir or not os.path.isdir(game_dir):
        return False
    if _has(game_dir, ("amd_fidelityfx_dx12.dll", "amd_fidelityfx_upscaler_dx12.dll", "d3d12core.dll", "sl.interposer.dll",
                       "ffx_fsr2_api_dx12_x64.dll")):
        return True
    base = game_dir.rstrip(os.sep).count(os.sep)
    want = {e.lower() for e in exes or []}
    for r, d, f in os.walk(game_dir):
        if r.count(os.sep) - base >= DEPTH:
            d[:] = []
        for x in f:
            lo = x.lower()
            if not lo.endswith(".exe") or not (lo in want or lo.endswith("-shipping.exe")):
                continue
            try:
                with open(os.path.join(r, x), "rb") as fh:
                    if b"d3d12.dll" in fh.read(32 << 20).lower():
                        return True
            except OSError:
                continue
    return False
