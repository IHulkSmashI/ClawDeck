"""XeSS on XMX per game, never for the whole session.

xess-xmx-linux's install.sh points the whole desktop session at its patched Mesa driver
(~/.config/environment.d/50-xess-xmx.conf: VK_DRIVER_FILES, VKD3D_DISABLE_EXTENSIONS, ...). That reaches Steam's
own screen (gamescope) and every game, also those without XeSS. On the Claw 8 EX (2026-11-04) it cost Onimusha (no
XeSS) much of its frame rate, made every game recompile its shaders when it changed, and the one boot that went
black ran with it.

ClawDeck keeps the package, but scopes the driver:
  1. isolate_session(): the session file moves aside (…/50-xess-xmx.conf.clawdeck-per-game, put back by restore())
     and its variables are kept in ~/.local/share/clawdeck/xmx.env;
  2. install_scope(): every Proton that has xess-xmx-linux's hook gets a user_settings.user.py (the hook already runs
     that file at every game start, after tools/proton-hook.py). It adds xmx.env's variables to the game's
     environment only for a game that ships XeSS (libxess*.dll) or OptiScaler, or that Per-game set to on
     (CLAWDECK_XMX=1 in its launch file; CLAWDECK_XMX=0 keeps a XeSS game on the stock driver).
A user_settings.user.py that isn't ClawDeck's is kept as user_settings.clawdeck-prev.py and still runs first.
"""
from __future__ import annotations

import glob
import logging
import os
import re

from . import sysfs, userexec

log = logging.getLogger("clawdeck.xmxscope")

SESSION_CONF = ".config/environment.d/50-xess-xmx.conf"
PARKED_SUFFIX = ".clawdeck-per-game"
XMX_ENV = ".local/share/clawdeck/xmx.env"
MARK = "# clawdeck xmx scope"
USER_PY = "user_settings.user.py"
PREV_PY = "user_settings.clawdeck-prev.py"
HOOK_MARK = "xess-xmx proton hook"

SCOPE_PY = MARK + ''' - written by ClawDeck, removed by ClawDeck (Setup / uninstall).
# The XeSS XMX driver for this game only: a game that ships XeSS or OptiScaler, or one Per-game set to on.
import os as _o, glob as _g
user_settings = {}
_here = _o.path.dirname(_o.path.abspath(__file__))
_prev = _o.path.join(_here, "%(prev)s")
if _o.path.isfile(_prev):
    _p = {"__file__": _prev}
    exec(compile(open(_prev).read(), _prev, "exec"), _p)
    user_settings.update(_p.get("user_settings", {}))
try:
    _home = _o.environ.get("HOME", "")
    _rt = _o.environ.get("CLAWDECK_RUNTIME") or _o.path.join(_home, "homebrew/data/ClawDeck")
    _id = _o.environ.get("SteamAppId") or _o.environ.get("SteamGameId") or ""
    _want = _o.environ.get("CLAWDECK_XMX", "")
    _lf = _o.path.join(_rt, "launch", _id + ".env")
    if not _want and _id.isdigit() and _o.path.isfile(_lf):
        for _l in open(_lf):
            if _l.startswith("CLAWDECK_XMX="):
                _want = _l.split("=", 1)[1].strip()
    if not _want:
        _game = _o.environ.get("STEAM_COMPAT_INSTALL_PATH", "")
        _found = False
        if _game and _o.path.isdir(_game):
            for _r, _d, _f in _o.walk(_game):
                if _r.count(_o.sep) - _game.count(_o.sep) >= 5:
                    _d[:] = []
                _lo = [x.lower() for x in _f]
                if any(x.startswith("libxess") and x.endswith(".dll") for x in _lo) or "optiscaler.ini" in _lo:
                    _found = True
                    break
        _want = "1" if _found else "0"
    if _want == "1":
        _env = _o.path.join(_home, "%(xmx_env)s")
        for _l in open(_env):
            _l = _l.strip()
            if _l and not _l.startswith("#") and "=" in _l:
                _k, _v = _l.split("=", 1)
                user_settings[_k] = _v
        import sys as _s
        _s.stderr.write("clawdeck: XeSS XMX driver for this game (%%s)\\n" %% ("Per-game" if _o.environ.get("CLAWDECK_XMX") or _o.path.isfile(_lf) else "ships XeSS"))
except Exception as _e:
    import sys as _s
    _s.stderr.write("clawdeck: xmx scope: %%s\\n" %% _e)
'''


def _parse_env(text: str) -> dict[str, str]:
    env = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k.strip()):
            env[k.strip()] = v
    return env


def xmx_env(home: str) -> dict[str, str]:
    return _parse_env(sysfs.read(os.path.join(home, XMX_ENV)) or "")


def session_active(home: str) -> bool:
    return os.path.isfile(os.path.join(home, SESSION_CONF))


def isolate_session(home: str) -> dict:
    """Move the package's session-wide driver file aside and keep its variables for the per-game scope."""
    conf = os.path.join(home, SESSION_CONF)
    if not os.path.isfile(conf):
        return {"ok": True, "moved": False, "env": xmx_env(home)}
    env = _parse_env(sysfs.read(conf) or "")
    if not env.get("VK_DRIVER_FILES"):
        return {"ok": False, "moved": False, "error": f"{conf} has no VK_DRIVER_FILES; left as it is"}
    out = os.path.join(home, XMX_ENV)
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        userexec.write_new(out, "# the XeSS XMX driver's variables, from " + SESSION_CONF + " (ClawDeck applies them per game)\n"
                           + "".join(f"{k}={v}\n" for k, v in env.items()), 0o644)
        os.replace(conf, conf + PARKED_SUFFIX)
        userexec.chown_tree(out, home)
    except OSError as e:
        return {"ok": False, "moved": False, "error": f"moving {conf}: {e}"}
    log.info("XeSS XMX driver: session-wide file moved to %s%s, %d variables per game", conf, PARKED_SUFFIX, len(env))
    return {"ok": True, "moved": True, "env": env}


def protons(home: str) -> list[str]:
    """Proton folders with xess-xmx-linux's hook (user_settings.py written by its install.sh)."""
    libs = [os.path.join(home, ".local/share/Steam")]
    vdf = sysfs.read(os.path.join(libs[0], "steamapps", "libraryfolders.vdf")) or ""
    libs += [m.group(1) for m in re.finditer(r'"path"\s+"([^"]+)"', vdf)]
    found = []
    for lib in dict.fromkeys(libs):
        for p in glob.glob(os.path.join(lib, "steamapps/common/Proton*")) + glob.glob(os.path.join(lib, "compatibilitytools.d/*")):
            if HOOK_MARK in (sysfs.read(os.path.join(p, "user_settings.py")) or ""):
                found.append(os.path.realpath(p))     # /home is a link to /var/home on Bazzite: one entry per Proton
    return sorted(set(found))


def install_scope(home: str) -> dict:
    text = SCOPE_PY % {"prev": PREV_PY, "xmx_env": XMX_ENV}
    done, errors = [], []
    for p in protons(home):
        u, prev = os.path.join(p, USER_PY), os.path.join(p, PREV_PY)
        try:
            cur = sysfs.read(u)
            if cur is not None and MARK not in cur:
                os.replace(u, prev)           # the user's own Proton settings keep running, first
            if (sysfs.read(u) or "") != text:
                userexec.write_new(u, text, 0o644)
                userexec.chown_tree(u, home)
            done.append(p)
        except OSError as e:
            errors.append(f"{p}: {e}")
    return {"ok": not errors, "protons": done, "error": "; ".join(errors) or None}


def remove_scope(home: str) -> dict:
    removed = []
    for p in protons(home):
        u, prev = os.path.join(p, USER_PY), os.path.join(p, PREV_PY)
        if MARK in (sysfs.read(u) or ""):
            os.remove(u)
            if os.path.isfile(prev):
                os.replace(prev, u)
            removed.append(p)
    return {"ok": True, "protons": removed}


def restore_session(home: str) -> dict:
    """Undo isolate_session(): the package's driver for the whole session again (as its install.sh set it)."""
    conf = os.path.join(home, SESSION_CONF)
    parked = conf + PARKED_SUFFIX
    if os.path.isfile(parked) and not os.path.exists(conf):
        os.replace(parked, conf)
        return {"ok": True, "restored": True}
    return {"ok": True, "restored": False}


def state(home: str) -> dict:
    ps = protons(home)
    scoped = [p for p in ps if MARK in (sysfs.read(os.path.join(p, USER_PY)) or "")]
    return {"session_wide": session_active(home), "per_game": bool(xmx_env(home)) and bool(scoped),
            "protons": ps, "scoped": scoped, "unscoped": [p for p in ps if p not in scoped]}
