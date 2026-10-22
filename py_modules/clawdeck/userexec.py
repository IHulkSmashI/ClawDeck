"""Run a command as the desktop user from the root backend (installers and checks that belong to the user's session:
xess-xmx-linux writes to ~/.config and enables systemd --user units, which must not be done as root)."""
from __future__ import annotations

import os
import pwd

from . import sysfs
import subprocess


def user_of(home: str) -> pwd.struct_passwd | None:
    try:
        return pwd.getpwuid(os.stat(home).st_uid)
    except (OSError, KeyError):
        return None


def run(cmd: list[str], home: str, timeout: float = 600, cwd: str | None = None, env: dict | None = None) -> dict:
    u = user_of(home)
    if u is None:
        return {"rc": -1, "out": f"no user owns {home}"}
    uenv = {"HOME": u.pw_dir, "USER": u.pw_name, "LOGNAME": u.pw_name, "PATH": "/usr/local/bin:/usr/bin:/bin",
            "XDG_RUNTIME_DIR": f"/run/user/{u.pw_uid}",
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{u.pw_uid}/bus"}
    uenv.update(env or {})
    full = cmd if os.geteuid() != 0 else ["runuser", "-u", u.pw_name, "--", "env", *[f"{k}={v}" for k, v in uenv.items()], *cmd]
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=timeout, cwd=cwd, check=False,
                           env=sysfs.clean_env() if os.geteuid() == 0 else {**sysfs.clean_env(), **uenv})
        return {"rc": r.returncode, "out": ((r.stdout or "") + (r.stderr or ""))[-4000:]}
    except (OSError, subprocess.SubprocessError) as e:
        return {"rc": -1, "out": str(e)}


def write_new(path: str, text: str, mode: int = 0o644) -> str:
    """Write a file in a folder the user can write to, as root, without following a link someone planted there:
    whatever is at the path (file or symlink) is removed first, and the new file is created exclusively."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, mode)
    return path


def chown_tree(path: str, home: str) -> None:
    """Give a file, or a folder and everything in it, to the desktop user (the backend runs as root)."""
    u = user_of(home)
    if u is None or os.geteuid() != 0:
        return
    targets = [path]
    if os.path.isdir(path) and not os.path.islink(path):
        for root, dirs, files in os.walk(path):
            targets += [os.path.join(root, x) for x in dirs + files]
    for n in targets:
        try:
            os.lchown(n, u.pw_uid, u.pw_gid)
        except OSError:
            pass
