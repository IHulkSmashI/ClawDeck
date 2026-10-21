"""Client for Handheld Daemon's local API (/run/hhd/api, a UNIX socket speaking HTTP; root only, which the Decky
backend is). ClawDeck does not re-implement what HHD already does for the Claw - TDP through MSI's firmware
attributes, fan curves, charge limit, the MSI buttons - it sets HHD's state and reads it back.

State keys are dotted paths into HHD's settings tree, e.g. tdp.qam.tdp (the TDP slider in the Quick Access menu),
tdp.msi.fan.mode, tdp.general.charge_limit. `hhdctl get` on the device lists every key the installed version
offers; ClawDeck reads the tree once at start and only uses keys that exist.
"""
from __future__ import annotations

import json
import logging
import socket
from http.client import HTTPConnection, HTTPResponse

log = logging.getLogger("clawdeck.hhd")

SOCKET = "/run/hhd/api"


class _UnixConnection(HTTPConnection):
    def __init__(self, path: str, timeout: float = 2.0):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def _request(method: str, url: str, body: dict | None = None) -> dict | None:
    con = _UnixConnection(SOCKET)
    try:
        con.request(method, url, body=json.dumps(body) if body is not None else None,
                    headers={"Content-Type": "application/json"} if body is not None else {})
        resp: HTTPResponse = con.getresponse()
        data = resp.read()
        if resp.status != 200:
            log.warning("hhd %s %s -> %d %s", method, url, resp.status, data[:200])
            return None
        return json.loads(data) if data else {}
    except (OSError, ValueError) as e:
        log.debug("hhd unavailable: %s", e)
        return None
    finally:
        con.close()


def available() -> bool:
    return _request("GET", "/api/v1/state") is not None


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def get_state(flat: bool = True) -> dict | None:
    st = _request("GET", "/api/v1/state")
    if st is None:
        return None
    return _flatten(st) if flat else st


def get(key: str, default=None):
    st = get_state()
    if st is None:
        return default
    return st.get(key, default)


def set_values(values: dict) -> dict | None:
    """Set dotted keys. HHD applies them on its next loop and returns the new state; a rejected value comes back
    unchanged, so callers compare. In dry-run nothing is sent; the current state with the values merged in comes back,
    so callers behave as if HHD had accepted them."""
    from . import sysfs
    if sysfs.dry_run():
        log.info("dry-run: would set Handheld Daemon %s", values)
        st = get_state() or {}
        return {**st, **values}
    tree: dict = {}
    for key, val in values.items():
        node = tree
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = val
    st = _request("POST", "/api/v1/state", tree)
    return _flatten(st) if st is not None else None


# HHD's MSI driver keys (adjustor/drivers/msi/settings.yml):
#   tdp.msi.tdp.mode            quiet | balanced | performance | custom
#   tdp.msi.tdp.custom.tdp      int, watts (HHD clamps to the model's limit, derives PL2/PL3 itself)
#   tdp.msi.tdp.custom.boost    bool
#   tdp.msi.fan.mode            disabled | manual (+ tdp.msi.fan.manual.st<N> curve points)
#   gamemode.battery.charge_limit   p70 | p80 | p85 | p90 | p95 | disabled   (battery driver, 70% and up only)
#   hhd.settings.tdp_ready      bool: the user accepted HHD's TDP controls; nothing is applied before that
KEY_TDP_MODE = "tdp.msi.tdp.mode"
KEY_TDP_CUSTOM = "tdp.msi.tdp.custom.tdp"
KEY_TDP_BOOST = "tdp.msi.tdp.custom.boost"
KEY_FAN_MODE = "tdp.msi.fan.mode"
KEY_CHARGE_LIMIT = "gamemode.battery.charge_limit"
KEY_TDP_READY = "hhd.settings.tdp_ready"
CHARGE_OPTIONS = {70: "p70", 80: "p80", 85: "p85", 90: "p90", 95: "p95"}


def tdp_ready() -> bool:
    return bool(get(KEY_TDP_READY, False))


def set_tdp(watts: int, boost: bool = True) -> bool:
    """Custom TDP in watts through HHD (it clamps to the model's limit and derives PL2/PL3 itself)."""
    st = set_values({KEY_TDP_MODE: "custom", KEY_TDP_CUSTOM: int(watts), KEY_TDP_BOOST: bool(boost)})
    ok = st is not None and st.get(KEY_TDP_MODE) == "custom" and int(st.get(KEY_TDP_CUSTOM, -1)) == int(watts)
    if not ok:
        log.warning("hhd did not accept tdp %s (got mode %s, tdp %s)", watts, None if st is None else st.get(KEY_TDP_MODE),
                    None if st is None else st.get(KEY_TDP_CUSTOM))
    return ok


def set_tdp_mode(mode: str) -> bool:
    st = set_values({KEY_TDP_MODE: mode})
    return st is not None and st.get(KEY_TDP_MODE) == mode


def get_tdp() -> dict | None:
    st = get_state()
    if st is None:
        return None
    return {"mode": st.get(KEY_TDP_MODE), "custom_tdp": st.get(KEY_TDP_CUSTOM), "boost": st.get(KEY_TDP_BOOST),
            "ready": bool(st.get(KEY_TDP_READY, False))}


def set_charge_limit(percent: int | None) -> bool:
    """HHD offers 70-95 %; other values are handled by battery.py directly on sysfs with HHD set to 'disabled'."""
    opt = CHARGE_OPTIONS.get(percent, "disabled") if percent else "disabled"
    st = set_values({KEY_CHARGE_LIMIT: opt})
    return st is not None and st.get(KEY_CHARGE_LIMIT) == opt


def has_key(key: str) -> bool:
    st = get_state()
    return st is not None and key in st


# ---- settings schema (/api/v1/settings) --------------------------------------------------------------------------
# HHD describes every setting it offers as a tree of containers: {"type": "container", "children": {...}}, with leaves
# of type bool | int | float | multiple (options) | discrete (options list) | mode (modes: {name: container}) and
# non-settings (display, action, version). A "mode" stores its choice at "<key>.mode" and its children under
# "<key>.<mode name>.<child>". ClawDeck walks this tree instead of hard-coding keys, so a new HHD version that adds or
# renames a gyro/RGB option shows up without a ClawDeck release.
SETTING_TYPES = ("bool", "int", "float", "multiple", "discrete", "mode")


def get_settings() -> dict | None:
    return _request("GET", "/api/v1/settings")


def describe(schema: dict, prefix: str = "") -> list[dict]:
    """Flatten a settings tree into [{key, type, title, hint, options|min/max/step, tags}] for the leaves the UI can
    render. Mode children are included under their mode name and carry "mode_of" so the UI can show them only when
    that mode is selected."""
    out: list[dict] = []

    def leaf(key: str, node: dict, mode_of: tuple[str, str] | None) -> None:
        t = node.get("type")
        d = {"key": key, "type": t, "title": node.get("title") or key.rsplit(".", 1)[-1], "hint": node.get("hint"),
             "tags": list(node.get("tags") or []), "default": node.get("default")}
        if mode_of:
            d["mode_of"] = {"key": mode_of[0], "mode": mode_of[1]}
        if t == "multiple":
            d["options"] = [{"value": k, "label": v} for k, v in (node.get("options") or {}).items()]
        elif t == "discrete":
            d["options"] = [{"value": v, "label": str(v)} for v in (node.get("options") or [])]
        elif t in ("int", "float"):
            for k in ("min", "max", "step", "unit"):
                if k in node:
                    d[k] = node[k]
        out.append(d)

    def walk(node: dict, key: str, mode_of: tuple[str, str] | None) -> None:
        t = node.get("type")
        if t == "container" or t is None:
            # the top level (and HHD's section groups) are plain dicts of sections without a "type"
            kids = node.get("children") if isinstance(node.get("children"), dict) else (node if t is None else {})
            for k, child in kids.items():
                if isinstance(child, dict):
                    walk(child, f"{key}.{k}" if key else k, mode_of)
        elif t == "mode":
            modes = node.get("modes") or {}
            d = {"key": f"{key}.mode", "type": "mode", "title": node.get("title") or key, "hint": node.get("hint"),
                 "tags": list(node.get("tags") or []), "default": node.get("default"),
                 "options": [{"value": m, "label": (c or {}).get("title") or m} for m, c in modes.items()]}
            if mode_of:
                d["mode_of"] = {"key": mode_of[0], "mode": mode_of[1]}
            out.append(d)
            for m, c in modes.items():
                if isinstance(c, dict):
                    walk(c, f"{key}.{m}", (f"{key}.mode", m))
        elif t in SETTING_TYPES:
            leaf(key, node, mode_of)

    walk(schema, prefix, None)
    return out


def validate(desc: dict, value):
    """Coerce a value from the UI to what the setting accepts, or raise ValueError."""
    t = desc["type"]
    if t == "bool":
        return bool(value)
    if t in ("int", "float"):
        v = int(value) if t == "int" else float(value)
        if "min" in desc and v < desc["min"] or "max" in desc and v > desc["max"]:
            raise ValueError(f"{desc['key']}: {v} outside {desc.get('min')}..{desc.get('max')}")
        return v
    if t in ("multiple", "discrete", "mode"):
        allowed = [o["value"] for o in desc.get("options", [])]
        if value not in allowed:
            raise ValueError(f"{desc['key']}: {value!r} not one of {allowed}")
        return value
    raise ValueError(f"{desc['key']}: type {t} is not settable")
