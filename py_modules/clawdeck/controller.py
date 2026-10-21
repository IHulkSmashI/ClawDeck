"""Controller: gyro, RGB stick lighting, back buttons and controller mode, as Handheld Daemon offers them. Without
Handheld Daemon, the tab shows InputPlumber's controller and profile instead (shadowblip.py), and changes nothing.

ClawDeck does not drive the controller; HHD does (hhd/device/claw). This module reads HHD's settings schema
(/api/v1/settings) and current state, keeps the Claw controller and RGB sections, and writes changes back through
HHD's state API after checking them against the schema. Keys are discovered, not hard-coded, so what the installed
HHD offers is exactly what the tab shows:
  controllers.claw.*   controller mode (and, inside it, gyro / back-button options), swap guide, Nintendo layout ...
  rgb.handheld.*       RGB mode (solid, pulse, rainbow ... whatever the device supports) and its colour/brightness
"""
from __future__ import annotations

import logging

from . import hhd, shadowblip

log = logging.getLogger("clawdeck.controller")

PREFIXES = ("controllers.claw.", "rgb.handheld.")
ADVANCED_TAGS = ("advanced", "expert", "non-essential")
HIDDEN_TAGS = ("hide", "hide-title-only-deprecated")


def group_of(key: str) -> str:
    k = key.lower()
    if k.startswith("rgb."):
        return "rgb"
    if "gyro" in k or "imu" in k or "motion" in k:
        return "gyro"
    if k.endswith("controller_mode.mode"):
        return "mode"
    if "paddle" in k or "extra" in k or "back" in k or "button" in k or "chord" in k or "swap" in k:
        return "buttons"
    return "other"


def _descriptors(schema: dict) -> list[dict]:
    out = []
    for d in hhd.describe(schema):
        if not d["key"].startswith(PREFIXES):
            continue
        if any(t in HIDDEN_TAGS for t in d["tags"]):
            continue
        d["advanced"] = any(t in ADVANCED_TAGS for t in d["tags"])
        d["group"] = group_of(d["key"])
        out.append(d)
    return out


class Controller:
    def __init__(self):
        self._schema_cache: list[dict] | None = None

    def _schema(self, refresh: bool = False) -> list[dict] | None:
        if self._schema_cache is None or refresh:
            raw = hhd.get_settings()
            self._schema_cache = None if raw is None else _descriptors(raw)
        return self._schema_cache

    def settings(self) -> dict:
        """Everything the Controller tab renders: {available, settings: [descriptor + value]}."""
        desc = self._schema(refresh=True)
        state = hhd.get_state()
        if desc is None or state is None:
            # no Handheld Daemon: say what InputPlumber (current Bazzite) has built for this Claw, read-only
            return {"available": False, "settings": [], "inputplumber": shadowblip.composite_devices()}
        items = []
        for d in desc:
            item = dict(d)
            item["value"] = state.get(d["key"], d.get("default"))
            items.append(item)
        return {"available": True, "settings": items, "device": state.get("info.device") or state.get("hhd.settings.device")}

    def set(self, key: str, value) -> dict:
        desc = {d["key"]: d for d in (self._schema() or [])}
        if key not in desc:
            raise ValueError(f"{key} is not a controller setting Handheld Daemon offers on this device")
        v = hhd.validate(desc[key], value)
        st = hhd.set_values({key: v})
        ok = st is not None and st.get(key) == v
        if not ok:
            log.warning("hhd did not accept %s=%r (now %r)", key, v, None if st is None else st.get(key))
        return {"ok": ok, "key": key, "value": None if st is None else st.get(key)}
