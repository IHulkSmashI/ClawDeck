"""Display and dock: VRR range, internal panel refresh rates, HDR, and what the dock/TV reports.

Game Mode on Bazzite / SteamOS-like images is started by gamescope-session(-plus), which sources every
~/.config/environment.d/*.conf before launching gamescope and turns these variables into flags
(ChimeraOS gamescope-session, usr/share/gamescope-session-plus/gamescope-session-plus):
  ADAPTIVE_SYNC=1                  -> --adaptive-sync (VRR)
  CUSTOM_REFRESH_RATES=30,...,120  -> --custom-refresh-rates (the values Steam's refresh slider offers)
  ENABLE_GAMESCOPE_HDR=1           -> --hdr-enabled --hdr-itm-enable (needs ENABLE_GAMESCOPE_WSI=1, the default)
  GAMESCOPE_HDR_NITS=<n>           -> --hdr-itm-sdr-nits / --hdr-sdr-content-nits
ClawDeck writes only its own file, 60-clawdeck-display.conf, and only when the user taps Save (nothing persists
otherwise). Removing the file restores the image's defaults. Changes take effect at the next Game Mode start.
The dock needs no flags: gamescope picks the TV's preferred mode (4K120 on an HDMI 2.1 / DP 1.4 dock); this module
reports which connector is active and the modes it offers so a wrong cable/dock shows up in the readout.
Per-game HDR for Proton games (DXVK_HDR=1) goes into that game's launch environment (launchenv.py).
"""
from __future__ import annotations

import glob
import logging
import os

from . import hw, sysfs

log = logging.getLogger("clawdeck.display")

CONF_NAME = "60-clawdeck-display.conf"
KEYS = ("ADAPTIVE_SYNC", "CUSTOM_REFRESH_RATES", "ENABLE_GAMESCOPE_HDR", "GAMESCOPE_HDR_NITS")


def connectors() -> list[dict]:
    """DRM connectors with their status and modes, internal panel first."""
    out = []
    for c in sorted(glob.glob("/sys/class/drm/card[0-9]-*")):
        name = os.path.basename(c).split("-", 1)[1]
        status = sysfs.read(f"{c}/status")
        modes = []
        try:
            with open(f"{c}/modes", "r", encoding="utf-8") as f:
                modes = [m.strip() for m in f if m.strip()]
        except OSError:
            pass
        out.append({"name": name, "status": status, "internal": name.startswith(("eDP", "DSI", "LVDS")),
                    "modes": list(dict.fromkeys(modes))[:8], "enabled": sysfs.read(f"{c}/enabled")})
    out.sort(key=lambda d: (not d["internal"], d["name"]))
    return out


def refresh_list(lo: int, hi: int) -> str:
    return ",".join(str(r) for r in range(int(lo), int(hi) + 1))


def parse_conf(text: str) -> dict:
    vals = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip()] = v.strip().strip('"')
    return vals


class Display:
    def __init__(self, hardware: hw.Hardware, home: str):
        self.hw = hardware
        self.home = home
        self.conf = os.path.join(home, ".config", "environment.d", CONF_NAME)

    def defaults(self) -> dict:
        lo, hi = self.hw.model.get("vrr", [self.hw.model["panel_hz"][0], self.hw.model["panel_hz"][-1]])
        return {"vrr": False, "vrr_min": lo, "vrr_max": hi, "hdr": False, "hdr_nits": 400}

    def read(self) -> dict:
        """Current ClawDeck display settings (from our file), plus what the session sees right now."""
        s = self.defaults()
        txt = sysfs.read(self.conf)
        saved = txt is not None
        if txt:
            v = parse_conf(txt)
            s["vrr"] = v.get("ADAPTIVE_SYNC") == "1"
            rates = [int(x) for x in v.get("CUSTOM_REFRESH_RATES", "").split(",") if x.strip().isdigit()]
            if rates:
                s["vrr_min"], s["vrr_max"] = min(rates), max(rates)
            s["hdr"] = v.get("ENABLE_GAMESCOPE_HDR") == "1"
            if v.get("GAMESCOPE_HDR_NITS", "").isdigit():
                s["hdr_nits"] = int(v["GAMESCOPE_HDR_NITS"])
        return {"settings": s, "saved": saved, "conf": self.conf, "connectors": connectors(),
                "panel_hz": self.hw.model["panel_hz"], "vrr_range": self.hw.model.get("vrr"),
                "other_overrides": self._other_overrides()}

    def _other_overrides(self) -> list[str]:
        """Other environment.d files that set the same variables (they may win, files are read in name order)."""
        hits = []
        for f in sorted(glob.glob(os.path.join(self.home, ".config", "environment.d", "*.conf"))):
            if os.path.basename(f) == CONF_NAME:
                continue
            v = parse_conf(sysfs.read(f) or "")
            if any(k in v for k in KEYS):
                hits.append(f)
        return hits

    def render(self, s: dict) -> str:
        lo_hw, hi_hw = self.hw.model.get("vrr", [self.hw.model["panel_hz"][0], self.hw.model["panel_hz"][-1]])
        lo = max(lo_hw, min(int(s.get("vrr_min", lo_hw)), hi_hw))
        hi = max(lo, min(int(s.get("vrr_max", hi_hw)), hi_hw))
        nits = max(100, min(int(s.get("hdr_nits", 400)), 1000))
        lines = ["# Written by ClawDeck (Display tab). Delete this file to go back to the image's defaults.",
                 "# Read by gamescope-session at the next Game Mode start."]
        if s.get("vrr"):
            lines.append("ADAPTIVE_SYNC=1")
        lines.append(f"CUSTOM_REFRESH_RATES={refresh_list(lo, hi)}")
        if s.get("hdr"):
            lines += ["ENABLE_GAMESCOPE_HDR=1", f"GAMESCOPE_HDR_NITS={nits}"]
        return "\n".join(lines) + "\n"

    def save(self, s: dict, dry_run: bool = False) -> dict:
        text = self.render(s)
        if dry_run:
            log.info("dry-run: would write %s:\n%s", self.conf, text)
            return {"ok": True, "dry_run": True, "text": text}
        try:
            os.makedirs(os.path.dirname(self.conf), exist_ok=True)
            tmp = self.conf + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            _chown_like_home(tmp, self.home)
            os.replace(tmp, self.conf)
            return {"ok": True, "text": text, "restart_needed": True}
        except OSError as e:
            log.error("writing %s failed: %s", self.conf, e)
            return {"ok": False, "error": str(e)}

    def reset(self) -> dict:
        try:
            os.remove(self.conf)
        except FileNotFoundError:
            pass
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "restart_needed": True}


def _chown_like_home(path: str, home: str) -> None:
    """The backend runs as root; files in the user's home must belong to the user."""
    try:
        st = os.stat(home)
        os.chown(path, st.st_uid, st.st_gid)
    except OSError:
        pass
