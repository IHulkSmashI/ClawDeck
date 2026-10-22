"""Per-game launch environment: the one file tools/clawdeck-launch.sh reads (<runtime>/launch/<appid>.env).

Every per-game choice that has to be in the game's environment lands here, composed from the game's launch settings
(profiles.py, key "launch"):
  inject   none | optiscaler | lsfg      -> injector.Injector.launch_env (WINEDLLOVERRIDES / LSFGVK_*)
  proxy, reframework                     -> the DLL name OptiScaler got and REFramework's dinput8, loaded native-first
  lsfg     {multiplier, flow_scale, performance_mode}
  xmx      bool (default on)             -> off: the stock Vulkan drivers for this game (xess.stock_env)
  xell     bool                          -> on: XeLL driver mode through the fork's shim (xess.XELL_ENV)
  mesa     "" | "26.1" | "26.3"          -> MESA=<v>, xess-xmx-linux's per-game driver choice
The plugin never edits Steam's launch options; the user adds the wrapper once per game and the file does the rest.
An empty composition removes the file, so a game with nothing set runs exactly as without ClawDeck.
"""
from __future__ import annotations

import os
import re

from . import xess
from .injector import Injector

KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
MESA_CHOICES = ("", "26.1", "26.3")


def compose(launch: dict) -> dict[str, str]:
    env: dict[str, str] = {}
    env.update(Injector.launch_env(launch.get("inject", "none"), launch.get("lsfg"), launch.get("proxy"),
                                   bool(launch.get("reframework"))))
    if launch.get("xmx") is False:
        env.update(xess.stock_env())
    if launch.get("xell"):
        env.update(xess.XELL_ENV)
    mesa = launch.get("mesa") or ""
    if mesa:
        if mesa not in MESA_CHOICES:
            raise ValueError(f"mesa must be one of {MESA_CHOICES}")
        env["MESA"] = mesa
    return env


def render(env: dict[str, str]) -> str:
    lines = ["# Written by ClawDeck for tools/clawdeck-launch.sh. KEY=VALUE, one per line."]
    for k, v in sorted(env.items()):
        if not KEY_RE.match(k) or "\n" in v:
            raise ValueError(f"bad launch variable {k!r}")
        lines.append(f"{k}={v}")
    return "\n".join(lines) + "\n"


def path_for(runtime_dir: str, appid: str) -> str:
    if not str(appid).isdigit():
        raise ValueError("appid must be numeric")
    return os.path.join(runtime_dir, "launch", f"{appid}.env")


def write(runtime_dir: str, appid: str, launch: dict) -> dict:
    env = compose(launch)
    p = path_for(runtime_dir, appid)
    if not env:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
        return {"path": None, "env": {}}
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(render(env))
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)
    return {"path": p, "env": env}
