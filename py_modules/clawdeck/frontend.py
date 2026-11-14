"""Emulator frontends that start many games under one Steam shortcut (ES-DE): which game runs, so that each one gets
its own power and Autotune instead of the frontend's (maintainer, 2026-11-14: "es-de could have nes roms that run at
500fps at 8W alongside PS3 roms that are locked at 30fps but need more than 8W less than 35W alongside current-gen
consoles that can't even hit 60fps with wattage maxed").

Steam only sees the frontend: every ROM started from ES-DE runs as ES-DE's shortcut, so ClawDeck saw one game, and
without ClawDeck's launch wrapper on that shortcut no MangoHud log, so Autotune said "no frame rate" and every ROM ran
at the pick. Two things fix that:
  - ES-DE's custom event scripts ("Enable custom event scripts", ~/ES-DE/scripts/<event>/, run blocking, in name order)
    start ClawDeck's script on game-start and game-end with the ROM path, game name, system name and system full name.
    It writes them to EVENT_FILE; while a game runs the plugin watches that file with inotify (Watch: no timer, it
    wakes only when the script writes, CONTRIBUTING's battery-first rule), and each ROM is a game of its own under the
    key "<frontend appid>#<system>/<ROM file name>";
  - ClawDeck's launch wrapper on the frontend's shortcut: its MangoHud settings are inherited by every emulator the
    frontend starts, so each one writes a frame-time log (Vulkan emulators through the host layer, Flatpak RetroArch
    through org.freedesktop.Platform.VulkanLayer.MangoHud). ES-DE draws with OpenGL, so its menus write none.
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import glob
import os
import re
import subprocess
from typing import Callable

from . import shortcuts

EVENT_FILE = ".local/share/clawdeck/frontend-game"
ESDE_DIR = "ES-DE"
ESDE_EVENTS = {"game-start": "start", "game-end": "end"}
ESDE_SCRIPT_NAME = "clawdeck.sh"
ESDE_SETTING = "CustomEventScripts"

SCRIPT = """#!/bin/sh
# ClawDeck: tells ClawDeck which game ES-DE {verb}, so each game gets its own power and Autotune.
# Installed by ClawDeck (Setup); ES-DE runs it with the ROM path, game name, system name and system full name.
d="$HOME/.local/share/clawdeck"
mkdir -p "$d" && printf '%s\\n' {event} "$3" "$1" "$2" > "$d/frontend-game.tmp" && mv -f "$d/frontend-game.tmp" "$d/frontend-game"
exit 0
"""


def event_path(home: str) -> str:
    return os.path.join(home, EVENT_FILE)


class Watch:
    """inotify on EVENT_FILE's folder: `on_event` runs in the event loop whenever the script has written the file
    (it is renamed into place, IN_MOVED_TO). close() ends it. Raises OSError where inotify isn't there."""
    IN_CLOSE_WRITE, IN_MOVED_TO = 0x00000008, 0x00000080

    def __init__(self, home: str, on_event: Callable[[], None]):
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        if not hasattr(libc, "inotify_init1"):
            raise OSError("no inotify here")
        self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        folder = os.path.dirname(event_path(home))
        if libc.inotify_add_watch(self.fd, folder.encode(), self.IN_CLOSE_WRITE | self.IN_MOVED_TO) < 0:
            err = ctypes.get_errno()
            os.close(self.fd)
            raise OSError(err, f"inotify on {folder}")
        self.loop, self.on_event = asyncio.get_event_loop(), on_event
        self.loop.add_reader(self.fd, self._ready)

    def _ready(self) -> None:
        try:
            data = os.read(self.fd, 4096)                # every event in the folder: only ours counts
        except OSError:
            return
        if os.path.basename(EVENT_FILE).encode() + b"\x00" in data:
            self.on_event()

    def close(self) -> None:
        try:
            self.loop.remove_reader(self.fd)
        finally:
            os.close(self.fd)


def stamp(home: str) -> int | None:
    """When the frontend last wrote an event (ns), or None."""
    try:
        return os.stat(event_path(home)).st_mtime_ns
    except OSError:
        return None


def read(home: str) -> dict | None:
    """The last event: {"event": "start" | "end", "system", "rom", "name"}, or None if there is none or it is torn."""
    try:
        with open(event_path(home), encoding="utf-8", errors="replace") as f:
            lines = f.read().split("\n")
    except OSError:
        return None
    if len(lines) < 4 or lines[0] not in ("start", "end"):
        return None
    return {"event": lines[0], "system": lines[1], "rom": lines[2], "name": lines[3]}


def key(appid: str, ev: dict) -> str:
    """A ROM's own game key under the frontend's Steam appid: "<appid>#<system>/<ROM file name without extension>"."""
    rom = os.path.splitext(os.path.basename(ev["rom"].rstrip("/")))[0] or ev["name"]
    return f"{appid}#{ev['system']}/{rom}"


def steam_appid(game_key: str) -> str:
    return game_key.split("#", 1)[0]


def _esde_running() -> bool:
    try:
        r = subprocess.run(["pgrep", "-f", r"ES-DE.*\.AppImage|/es-de( |$)"], capture_output=True, text=True,
                           timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def esde_shortcut(home: str) -> tuple[str, str] | None:
    """The Steam shortcut that starts ES-DE (EmuDeck's es-de.sh or the AppImage): (appid, launch options), or None."""
    for path in sorted(glob.glob(os.path.join(home, shortcuts.USERDATA, "*", "config", "shortcuts.vdf"))):
        try:
            with open(path, "rb") as f:
                data = shortcuts.parse_binary_vdf(f.read())
        except (OSError, ValueError, IndexError):
            continue
        for entry in (shortcuts._get(data, "shortcuts") or {}).values():
            if not isinstance(entry, dict):
                continue
            exe = str(shortcuts._get(entry, "Exe") or "").strip().strip('"')
            if re.search(r"(^|/)es-de(\.sh)?$|/ES-DE[^/]*\.AppImage$", exe, re.I):
                appid = shortcuts._get(entry, "appid")
                if isinstance(appid, int):
                    return str(appid & 0xFFFFFFFF), str(shortcuts._get(entry, "LaunchOptions") or "")
    return None


def esde_status(home: str) -> dict:
    """Is per-game power for ES-DE set up: the scripts in place and executable, the setting on, and ClawDeck's launch
    wrapper in ES-DE's shortcut (its MangoHud log settings reach every emulator ES-DE starts)."""
    root = os.path.join(home, ESDE_DIR)
    present = os.path.isdir(root)
    scripts = present and all(os.access(os.path.join(root, "scripts", ev, ESDE_SCRIPT_NAME), os.X_OK)
                              for ev in ESDE_EVENTS)
    setting = None
    try:
        with open(os.path.join(root, "settings", "es_settings.xml"), encoding="utf-8") as f:
            m = re.search(rf'<bool name="{ESDE_SETTING}" value="(true|false)"', f.read())
        setting = bool(m and m.group(1) == "true")
    except OSError:
        pass
    sc = esde_shortcut(home) if present else None
    wrapper = bool(sc and "clawdeck-launch.sh" in sc[1])
    return {"present": present, "scripts": scripts, "setting": setting, "wrapper": wrapper,
            "appid": sc[0] if sc else None, "ok": bool(scripts and setting and wrapper)}


def esde_install(home: str, uid: int | None = None, gid: int | None = None) -> dict:
    """Put ClawDeck's game-start/game-end scripts in ES-DE and turn its custom event scripts on. ES-DE writes its
    settings when it quits, so the setting is only changed while ES-DE is closed."""
    root = os.path.join(home, ESDE_DIR)
    if not os.path.isdir(root):
        return {"ok": False, "error": "ES-DE has no data folder (~/ES-DE) yet: start it once"}
    settings = os.path.join(root, "settings", "es_settings.xml")
    st = esde_status(home)
    if not st["setting"] and _esde_running():
        return {"ok": False, "error": "close ES-DE first: it writes its settings when it quits"}
    for ev, word in ESDE_EVENTS.items():
        d = os.path.join(root, "scripts", ev)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, ESDE_SCRIPT_NAME)
        with open(path, "w", encoding="utf-8") as f:
            f.write(SCRIPT.format(event=word, verb="starts" if word == "start" else "ends"))
        os.chmod(path, 0o755)
        for p in (os.path.join(root, "scripts"), d, path):
            if uid is not None:
                os.chown(p, uid, gid if gid is not None else -1)
    if not st["setting"]:
        try:
            with open(settings, encoding="utf-8") as f:
                xml = f.read()
        except OSError:
            xml = '<?xml version="1.0"?>\n'
        line = f'<bool name="{ESDE_SETTING}" value="true" />'
        new, n = re.subn(rf'<bool name="{ESDE_SETTING}" value="false" />', line, xml)
        if not n:
            new = xml.rstrip("\n") + "\n" + line + "\n"
        os.makedirs(os.path.dirname(settings), exist_ok=True)
        with open(settings, "w", encoding="utf-8") as f:
            f.write(new)
        if uid is not None:
            os.chown(settings, uid, gid if gid is not None else -1)
    return {**esde_status(home), "ok": True}


def esde_remove(home: str) -> dict:
    """Take ClawDeck's scripts out of ES-DE. Its custom-event-scripts setting stays on: other scripts may use it, and
    with none in place it does nothing. The launch wrapper stays on ES-DE's shortcut: Autotune still reads the
    frame rate through it, for ES-DE as one game."""
    root = os.path.join(home, ESDE_DIR)
    for ev in ESDE_EVENTS:
        try:
            os.remove(os.path.join(root, "scripts", ev, ESDE_SCRIPT_NAME))
        except FileNotFoundError:
            pass
        except OSError as e:
            return {"ok": False, "error": f"{ev}: {e.strerror}"}
    return {**esde_status(home), "ok": True}
