"""Per-game profiles, stored as one JSON file in Decky's settings directory and shareable as JSON.

A profile is a dict of overrides on top of a base profile name:
  {"base": "battery", "tdp": 22, "cpu_max_pct": 80, "gpu_min_mhz": 1200, "gpu_max_mhz": null,
   "target_fps": 60, "autotune": true, "learned": {...}}
Keys are Steam app ids as strings; "default" is the fallback. Two slots per game: "battery" and "plugged", so the
right one follows the power source automatically. A third, game-wide key "launch" holds what goes into the game's
environment (launchenv.py): XeSS XMX on/off, XeLL driver mode.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time

log = logging.getLogger("clawdeck.profiles")

FORMAT_VERSION = 1


class Profiles:
    def __init__(self, path: str):
        self.path = path
        self.data: dict = {"version": FORMAT_VERSION, "games": {}, "default": {"battery": {"base": "battery"},
                                                                                 "plugged": {"base": "performance"}}}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("version") == FORMAT_VERSION:
                self.data = d
        except (OSError, ValueError):
            pass

    def save(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix=".profiles.")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)   # atomic: a crash never leaves a half-written file
            return True
        except OSError as e:
            log.error("saving profiles failed: %s", e)
            return False

    def get(self, appid: str | int, plugged: bool) -> dict:
        slot = "plugged" if plugged else "battery"
        game = self.data["games"].get(str(appid), {})
        base = dict(self.data["default"].get(slot, {}))
        base.update(game.get(slot, {}))
        return base

    def set(self, appid: str | int, plugged: bool, values: dict) -> dict:
        slot = "plugged" if plugged else "battery"
        g = self.data["games"].setdefault(str(appid), {})
        s = g.setdefault(slot, {})
        for k, v in values.items():
            if v is None:
                s.pop(k, None)
            else:
                s[k] = v
        g["updated"] = int(time.time())
        self.save()
        return self.get(appid, plugged)

    def get_launch(self, appid: str | int) -> dict:
        """Game-wide launch settings (XeSS XMX, XeLL): the same plugged in or on battery."""
        return dict(self.data["games"].get(str(appid), {}).get("launch", {}))

    def set_launch(self, appid: str | int, values: dict) -> dict:
        g = self.data["games"].setdefault(str(appid), {})
        s = g.setdefault("launch", {})
        for k, v in values.items():
            if v is None:
                s.pop(k, None)
            else:
                s[k] = v
        g["updated"] = int(time.time())
        self.save()
        return dict(s)

    def games(self) -> list[str]:
        return sorted(self.data["games"].keys())

    def remove(self, appid: str | int) -> None:
        self.data["games"].pop(str(appid), None)
        self.save()

