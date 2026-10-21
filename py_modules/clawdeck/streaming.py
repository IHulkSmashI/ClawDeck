"""Streaming: play games from your PC on the Claw with Moonlight, through Vibepollo (or Apollo / Sunshine) on the PC.

The client is Moonlight with VRR, Nonary/moonlight-qt: a Moonlight fork by the author of Vibepollo that presents each
streamed frame as soon as it arrives on a variable-refresh display (a VRR-capable Vulkan path on Linux). It ships
daily AppImage builds with a SHA256SUMS file; updates.py downloads and verifies the newest one.

ClawDeck unpacks the AppImage once as the desktop user (`--appimage-extract`, so it needs no FUSE) into
~/.local/share/clawdeck/moonlight/<version>/ and keeps one launcher, ~/.local/share/clawdeck/moonlight/moonlight-vrr,
that always starts the current version. Steam's shortcut points at the launcher, so an update needs no new shortcut.
A saved PC (host) gives a second shortcut that goes straight to `moonlight stream <host> "<app>"`; pairing is
Moonlight's own (it shows a PIN to type into Vibepollo's web page).
"""
from __future__ import annotations

import os
import re
import shutil

from . import userexec

ROOT = ".local/share/clawdeck/moonlight"
LAUNCHER = "moonlight-vrr"
HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")


def root(home: str) -> str:
    return os.path.join(home, ROOT)


def launcher(home: str) -> str:
    return os.path.join(root(home), LAUNCHER)


def current(home: str) -> str | None:
    try:
        with open(os.path.join(root(home), "CURRENT"), "r", encoding="utf-8") as f:
            v = f.read().strip()
        return v if os.path.exists(os.path.join(root(home), v, "squashfs-root", "AppRun")) else None
    except OSError:
        return None


def state(home: str, settings: dict) -> dict:
    flathub = any(os.path.isdir(p) for p in (os.path.join(home, ".local/share/flatpak/app/com.moonlight_stream.Moonlight"),
                                             "/var/lib/flatpak/app/com.moonlight_stream.Moonlight"))
    return {"installed": current(home) is not None, "version": current(home), "launcher": launcher(home),
            "host": settings.get("stream_host"), "app": settings.get("stream_app", "Desktop"),
            "shortcut": settings.get("moonlight_shortcut"), "host_shortcut": settings.get("moonlight_host_shortcut"),
            "flathub_moonlight": flathub}


def install(home: str, staged_dir: str, version: str) -> dict:
    """Unpack the downloaded AppImage as the user and point the launcher at it. Old versions beyond the previous one
    are removed."""
    appimage = os.path.join(staged_dir, "Moonlight.AppImage")
    if not os.path.exists(appimage):
        raise FileNotFoundError("Moonlight was not downloaded")
    if not re.match(r"^[A-Za-z0-9._\-]+$", version):
        raise ValueError("bad version name")
    base = root(home)
    target = os.path.join(base, version)
    os.makedirs(target, exist_ok=True)
    local = os.path.join(target, "Moonlight.AppImage")
    shutil.copy2(appimage, local)
    os.chmod(local, 0o755)
    userexec.chown_tree(base, home)
    r = userexec.run([local, "--appimage-extract"], home, timeout=300, cwd=target)
    if not os.path.exists(os.path.join(target, "squashfs-root", "AppRun")):
        raise RuntimeError("Moonlight could not be unpacked: " + r["out"][-300:])
    os.remove(local)
    previous = current(home)
    with open(os.path.join(base, "CURRENT"), "w", encoding="utf-8") as f:
        f.write(version + "\n")
    with open(launcher(home), "w", encoding="utf-8") as f:
        f.write('#!/bin/bash\n# Starts the Moonlight (VRR) version ClawDeck installed last; written by ClawDeck.\n'
                'D="$(dirname "$(readlink -f "$0")")"\nexec "$D/$(cat "$D/CURRENT")/squashfs-root/AppRun" "$@"\n')
    os.chmod(launcher(home), 0o755)
    for d in os.listdir(base):
        p = os.path.join(base, d)
        if os.path.isdir(p) and d not in (version, previous):
            shutil.rmtree(p, ignore_errors=True)
    userexec.chown_tree(base, home)
    return {"version": version, "previous": previous, "launcher": launcher(home)}


def stream_args(host: str, app: str) -> str:
    """Launch options for a shortcut that goes straight into a stream (moonlight-qt's CLI)."""
    if not HOST_RE.match(host or ""):
        raise ValueError("that does not look like a PC name or IP address")
    app = (app or "Desktop").replace('"', "")
    return f'stream {host} "{app}"'
