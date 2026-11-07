"""Wider VRR range for the Claw's own screen: 30-120 Hz instead of the 48-120 Hz the panel's EDID announces.

The panel itself refreshes down to 30 Hz, but its EDID (the description the screen hands the GPU driver) says 48.
Below 48 fps the driver then holds each frame for a whole 48 Hz refresh, which judders. The Intel VRR range fix
(github.com/Zendo-GIT/Intel-Handheld-and-ARC-GPU-laboratory, MIT) fixes this on Windows with an EDID override; this
module is the same fix for Linux, with the same safety rule: it only touches the exact panels and the exact factory
validated EDIDs (matched by SHA-256), and only the range bytes the fix changes.

What changes, and nothing else:
  - base block bytes 95/96 (the monitor range descriptor's min/max vertical rate) and, on the Claw 8 AI+ / 8 EX
    panel, bytes 142/143 in the DisplayID extension (its Adaptive-Sync range), set to 30 and 120;
  - the checksums: each 128-byte block's last byte, plus the DisplayID section checksum, which Linux validates
    (an invalid DisplayID section is dropped whole, and with it the 120 Hz timing; Windows does not check it).

Two ways to apply it. Live (below, preferred): the kernel's DRM debugfs override, any distribution, no restart.
At boot, the way Bazzite already overrides the AOKZOE A1's EDID (usr/libexec/bazzite-hardware-setup):
  /etc/firmware/edid/<file>.bin        the patched EDID
  rpm-ostree initramfs-etc --track     so the GPU driver finds it at its first probe, inside the initramfs
  rpm-ostree kargs                     firmware_class.path=/etc/firmware drm.edid_firmware=eDP-1:edid/<file>.bin
It takes effect at the next restart. Undo removes all three, also at the next restart; and the boot menu's previous
entry always starts without it.
"""
from __future__ import annotations

import glob
import hashlib
import logging
import os
import re
import shutil
import subprocess

from . import distro, sysfs

log = logging.getLogger("clawdeck.edid")

FW_DIR = "/etc/firmware"
EDID_DIR = f"{FW_DIR}/edid"
CONNECTOR = "eDP-1"

# From the validated panel catalog. Physical = the factory EDID, as the kernel reads it.
PANELS: dict[str, dict] = {
    "CSW0801": {"models": "Claw 8 AI+ / 8 EX AI+", "panel": "PN8007QB1-2", "length": 256,
                "range_offsets": [(95, 96), (142, 143)],
                "physical_sha256": "e49bc570225510b7c889ed292570f1345caa07f5840db57ea6998a403db5cef0"},
    "TMA2027": {"models": "Claw A1M / 7 AI+", "panel": "TL070FVXS02-0", "length": 128,
                "range_offsets": [(95, 96)],
                "physical_sha256": "3518ab4456669d12a7b8d254f63005eae143c784dce02ec56c3753c41a664ca1"},
}
WIDE = (30, 120)


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def panel_id(edid: bytes) -> str:
    """PNP id + product code, as Windows names the monitor: 'CSW0801'."""
    if len(edid) < 12:
        return ""
    v = (edid[8] << 8) | edid[9]
    pnp = "".join(chr(((v >> s) & 0x1F) + 64) for s in (10, 5, 0))
    return f"{pnp}{edid[11]:02X}{edid[10]:02X}"


def canonical(edid: bytes, length: int) -> bytes | None:
    """The normalisation: exact length, or a 128-byte EDID padded with one all-zero block."""
    if len(edid) == length:
        return edid
    if length == 128 and len(edid) == 256 and edid[126] == 0 and not any(edid[128:]):
        return edid[:128]
    return None


def vrr_range(edid: bytes) -> tuple[int, int] | None:
    """Min/max vertical rate from the base block's range descriptor (tag 0xFD), as the kernel reads it."""
    for off in (54, 72, 90, 108):
        d = edid[off:off + 18]
        if len(d) == 18 and d[0] == 0 and d[1] == 0 and d[3] == 0xFD:
            return d[5], d[6]
    return None


def checksums_ok(edid: bytes) -> bool:
    for start in range(0, len(edid), 128):
        if sum(edid[start:start + 128]) % 256:
            return False
        if edid[start] == 0x70 and start > 0 and not _displayid_ok(edid, start):
            return False
    return True


def _displayid_section(edid: bytes, start: int) -> tuple[int, int] | None:
    """(first, last) byte offsets of the DisplayID section in an extension block, checksum byte included."""
    first = start + 1
    length = edid[first + 1]
    last = first + 4 + length          # version, length, product type, extension count, payload, checksum
    return (first, last) if last < start + 127 else None


def _displayid_ok(edid: bytes, start: int) -> bool:
    sec = _displayid_section(edid, start)
    return sec is not None and sum(edid[sec[0]:sec[1] + 1]) % 256 == 0


def _fix_checksums(e: bytearray) -> None:
    for start in range(0, len(e), 128):
        if start > 0 and e[start] == 0x70:
            sec = _displayid_section(bytes(e), start)
            if sec:
                e[sec[1]] = (256 - sum(e[sec[0]:sec[1]]) % 256) % 256
        e[start + 127] = (256 - sum(e[start:start + 127]) % 256) % 256


def patch(edid: bytes, key: str, lo: int = WIDE[0], hi: int = WIDE[1]) -> bytes:
    """The factory EDID with the wider range. Raises ValueError for anything but a validated factory EDID."""
    p = PANELS.get(key)
    if p is None:
        raise ValueError(f"panel {key or '?'} is not a validated panel")
    base = canonical(edid, p["length"])
    if base is None or sha256(base) != p["physical_sha256"]:
        raise ValueError(f"panel {key} has an EDID that isn't validated ({sha256(edid)[:12]}...)")
    e = bytearray(base)
    for mn, mx in p["range_offsets"]:
        e[mn], e[mx] = lo, hi
    _fix_checksums(e)
    out = bytes(e)
    changed = [i for i in range(len(out)) if out[i] != base[i]]
    allowed = {o for pair in p["range_offsets"] for o in pair} | {127, 255}
    if 128 < len(out):
        sec = _displayid_section(out, 128)
        if sec:
            allowed.add(sec[1])
    if not set(changed) <= allowed or not checksums_ok(out):
        raise ValueError("patched EDID failed its own check; nothing written")
    return out


def file_name(key: str, lo: int = WIDE[0], hi: int = WIDE[1]) -> str:
    return f"clawdeck-{key.lower()}-{lo}-{hi}.bin"


# ---- the running system -------------------------------------------------------------------------------------------
def panel_edid_path() -> str | None:
    hits = sorted(glob.glob(f"/sys/class/drm/card[0-9]-{CONNECTOR}/edid"))
    return hits[0] if hits else None


def _read_bytes(path: str | None) -> bytes:
    if not path:
        return b""
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


def _cmdline() -> str:
    return sysfs.read("/proc/cmdline") or ""


def _kargs_pending() -> str:
    """Kernel arguments of the deployment the next boot uses."""
    try:
        r = subprocess.run(["rpm-ostree", "kargs"], capture_output=True, text=True, timeout=30, check=False, env=sysfs.clean_env())
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _arg(cmdline: str, name: str) -> str | None:
    m = re.search(rf"(?:^|\s){re.escape(name)}=(\S+)", cmdline)
    return m.group(1) if m else None


def status() -> dict:
    """What the Display page shows: panel, the range the driver sees now, whether the wide range is on / pending."""
    raw = _read_bytes(panel_edid_path())
    key = panel_id(raw)
    p = PANELS.get(key)
    rng = vrr_range(raw) if raw else None
    booted = _arg(_cmdline(), "drm.edid_firmware") or ""
    ours = "clawdeck-" in booted
    pending_args = _kargs_pending()
    pending = _arg(pending_args, "drm.edid_firmware") or ""
    b = boot_backend()
    cp = conf_pending(b)
    supported = False
    reason = None
    if not raw:
        reason = "no internal screen EDID found"
    elif p is None:
        reason = f"screen {key or '?'} is not one of the validated panels"
    elif ours:
        supported = True
    else:
        try:
            patch(raw, key)
            supported = True
        except ValueError as e:
            reason = str(e)
    if booted and not ours:
        supported, reason = False, f"another EDID override is active ({booted}); ClawDeck leaves it alone"
    live = not ours and live_active()
    return {"panel": key or None, "panel_name": p["panel"] if p else None, "range": list(rng) if rng else None,
            "wide": ours or live, "supported": supported or live, "reason": None if live else reason,
            "pending": cp if cp else None if not pending_args else ("on" if "clawdeck-" in pending else "off"),
            "rpm_ostree": shutil.which("rpm-ostree") is not None,
            "boot_backend": b["kind"] if b else None,
            "method": "boot" if ours or cp == "on" else "live" if live else None, "live_possible": live_dir() is not None}


def _run(cmd: list[str]) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=False, env=sysfs.clean_env())
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if r.returncode != 0:
        log.warning("%s failed: %s", " ".join(cmd[:3]), (r.stderr or r.stdout).strip()[-400:])
    return r.returncode == 0, (r.stderr or r.stdout).strip()[-400:]


def enable() -> dict:
    """Write the patched EDID and point the kernel at it. Slow (rebuilds the initramfs); needs a restart."""
    raw = _read_bytes(panel_edid_path())
    key = panel_id(raw)
    if not shutil.which("rpm-ostree"):
        return {"ok": False, "error": "this system has no rpm-ostree (Bazzite / Fedora Atomic only for now)"}
    booted = _arg(_cmdline(), "drm.edid_firmware")
    fw_path = _arg(_kargs_pending() or _cmdline(), "firmware_class.path")
    if booted and "clawdeck-" not in booted:
        return {"ok": False, "error": f"another EDID override is active ({booted}); remove it first"}
    if fw_path and fw_path != FW_DIR:
        return {"ok": False, "error": f"firmware_class.path is already set to {fw_path}; not changing it"}
    name = file_name(key)
    dest = f"{EDID_DIR}/{name}"
    if booted:
        # running with ClawDeck's override already (turned off, then on again before restarting): the kernel now
        # shows the patched EDID, so reuse the file written last time instead of patching again
        data = _read_bytes(dest)
        if not data or vrr_range(data) != WIDE or not checksums_ok(data):
            return {"ok": False, "error": "restart once first, then turn it on again"}
    else:
        try:
            data = patch(raw, key)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
    os.makedirs(EDID_DIR, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    ok, out = _run(["rpm-ostree", "initramfs-etc", f"--track={dest}"])
    if not ok:
        return {"ok": False, "error": f"initramfs: {out}"}
    ok, out = _run(["rpm-ostree", "kargs", f"--append-if-missing=firmware_class.path={FW_DIR}",
                    f"--append-if-missing=drm.edid_firmware={CONNECTOR}:edid/{name}"])
    if not ok:
        _run(["rpm-ostree", "initramfs-etc", f"--untrack={dest}"])
        return {"ok": False, "error": f"kernel arguments: {out}"}
    return {"ok": True, "restart": True, "method": "boot", "file": dest, "sha256": sha256(data)}


def disable() -> dict:
    """Undo enable(): kernel arguments, initramfs tracking and the file. Takes effect at the next restart."""
    pending = _kargs_pending() or _cmdline()
    cur = _arg(pending, "drm.edid_firmware") or ""
    if cur and "clawdeck-" not in cur:
        return {"ok": False, "error": f"the EDID override ({cur}) is not ClawDeck's; leaving it"}
    args = []
    if cur:
        args.append(f"--delete-if-present=drm.edid_firmware={cur}")
    if _arg(pending, "firmware_class.path") == FW_DIR:
        args.append(f"--delete-if-present=firmware_class.path={FW_DIR}")
    if args:
        ok, out = _run(["rpm-ostree", "kargs", *args])
        if not ok:
            return {"ok": False, "error": f"kernel arguments: {out}"}
    for f in sorted(glob.glob(f"{EDID_DIR}/clawdeck-*.bin")):
        _run(["rpm-ostree", "initramfs-etc", f"--untrack={f}"])
        try:
            os.remove(f)
        except OSError:
            pass
    return {"ok": True, "restart": True}


# ---- at boot on SteamOS and CachyOS: their own documented ways to set kernel arguments ---------------------------
# SteamOS: /etc/default/grub (GRUB_CMDLINE_LINUX_DEFAULT), then SteamOS's grub.cfg regenerated (steamos-update-grub, or
#   grub-mkconfig into the grub.cfg SteamOS boots). /etc survives SteamOS updates. Its /boot is read-only, so the EDID
#   isn't put into an initramfs: firmware_class.path=/etc/firmware lets the driver find it on the root filesystem.
# CachyOS / Arch (wiki.cachyos.org "Boot Manager Configuration", Arch wiki "Kernel mode setting: EDID"): Limine
#   /etc/default/limine (KERNEL_CMDLINE[default]) + limine-mkinitcpio; systemd-boot /etc/sdboot-manage.conf
#   (LINUX_OPTIONS) + sdboot-manage gen; GRUB /etc/default/grub + grub-mkconfig. The EDID goes into the initramfs
#   through a mkinitcpio drop-in (FILES+=), so the driver has it at its first probe.
# Every file is a shell file its tool sources, so ClawDeck appends one marked line that adds its two arguments to the
# variable, and undo deletes exactly the marked lines: nothing else in the file is touched.
MARK = "# ClawDeck: VRR range fix (Fixes -> Remove VRR fix takes this out)"
MKINIT_DROPIN = "/etc/mkinitcpio.conf.d/clawdeck-edid.conf"


def _grub_cfg() -> str | None:
    for c in ("/efi/EFI/steamos/grub.cfg", "/boot/efi/EFI/steamos/grub.cfg", "/esp/efi/steamos/grub.cfg", "/boot/grub/grub.cfg"):
        if os.path.isfile(c):
            return c
    return None


def boot_backend() -> dict | None:
    """How this system takes kernel arguments: {"kind", "conf", "line" (format with {args}), "regen": [commands],
    "initramfs": bool}, or None (then the live method)."""
    d = distro.info()
    if shutil.which("rpm-ostree"):
        return {"kind": "rpm-ostree"}
    w = shutil.which
    grub_line = 'GRUB_CMDLINE_LINUX_DEFAULT="${{GRUB_CMDLINE_LINUX_DEFAULT}} {args}"'
    if d["kind"] == "steamos" and os.path.isfile("/etc/default/grub"):
        cfg = _grub_cfg()
        regen = [["steamos-update-grub"]] if w("steamos-update-grub") else [["grub-mkconfig", "-o", cfg]] if cfg and w("grub-mkconfig") else None
        if regen:
            return {"kind": "steamos-grub", "conf": "/etc/default/grub", "line": grub_line, "regen": regen, "initramfs": False}
        return None
    mkinit = w("mkinitcpio") is not None
    if os.path.isfile("/etc/default/limine") and w("limine-mkinitcpio"):
        return {"kind": "limine", "conf": "/etc/default/limine", "line": 'KERNEL_CMDLINE[default]+=" {args}"',
                "regen": [["limine-mkinitcpio"]], "initramfs": mkinit}
    if os.path.isfile("/etc/sdboot-manage.conf") and w("sdboot-manage"):
        regen = ([["mkinitcpio", "-P"]] if mkinit else []) + [["sdboot-manage", "gen"]]
        return {"kind": "systemd-boot", "conf": "/etc/sdboot-manage.conf", "line": 'LINUX_OPTIONS="${{LINUX_OPTIONS}} {args}"',
                "regen": regen, "initramfs": mkinit}
    if os.path.isfile("/etc/default/grub") and w("grub-mkconfig") and os.path.isfile("/boot/grub/grub.cfg"):
        regen = ([["mkinitcpio", "-P"]] if mkinit else []) + [["grub-mkconfig", "-o", "/boot/grub/grub.cfg"]]
        return {"kind": "grub", "conf": "/etc/default/grub", "line": grub_line, "regen": regen, "initramfs": mkinit}
    return None


def _conf_lines(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().splitlines()
    except OSError:
        return []


def conf_pending(b: dict | None) -> str | None:
    """"on" when ClawDeck's line is in the boot config, "off" when not; None without such a backend."""
    if not b or b["kind"] == "rpm-ostree":
        return None
    return "on" if any(MARK in ln for ln in _conf_lines(b["conf"])) else "off"


def _write_conf(path: str, lines: list[str]) -> None:
    tmp = path + ".clawdeck-tmp"
    st = os.stat(path) if os.path.exists(path) else None
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")
    if st:
        os.chmod(tmp, st.st_mode & 0o7777)
    os.replace(tmp, path)


def _strip_ours(lines: list[str]) -> list[str]:
    out, skip = [], False
    for ln in lines:
        if MARK in ln:
            skip = True                      # the marker line, and the one line after it
            continue
        if skip:
            skip = False
            continue
        out.append(ln)
    return out


def conf_enable(b: dict) -> dict:
    raw = _read_bytes(panel_edid_path())
    key = panel_id(raw)
    if _arg(_cmdline(), "drm.edid_firmware") and "clawdeck-" not in (_arg(_cmdline(), "drm.edid_firmware") or ""):
        return {"ok": False, "error": "another EDID override is active; ClawDeck leaves it alone"}
    if conf_pending(b) == "on":
        return {"ok": True, "restart": True, "method": "boot", "already": True}
    name = file_name(key)
    dest = f"{EDID_DIR}/{name}"
    if live_active() or "clawdeck-" in (_arg(_cmdline(), "drm.edid_firmware") or ""):
        data = _read_bytes(dest)
        if not data or vrr_range(data) != WIDE or not checksums_ok(data):
            return {"ok": False, "error": "restart once first, then turn it on again"}
    else:
        try:
            data = patch(raw, key)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
    os.makedirs(EDID_DIR, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    args = f"firmware_class.path={FW_DIR} drm.edid_firmware={CONNECTOR}:edid/{name}"
    lines = _strip_ours(_conf_lines(b["conf"]))
    _write_conf(b["conf"], lines + [MARK, b["line"].format(args=args)])
    if b.get("initramfs"):
        os.makedirs(os.path.dirname(MKINIT_DROPIN), exist_ok=True)
        _write_conf(MKINIT_DROPIN, [MARK, f'FILES+=({dest})'])
    for cmd in b["regen"]:
        ok, out = _run(cmd)
        if not ok:
            conf_disable(b, regen=False)                  # back to how it was, then regenerate once more
            for c in b["regen"]:
                _run(c)
            return {"ok": False, "error": f"{cmd[0]}: {out}"}
    return {"ok": True, "restart": True, "method": "boot", "file": dest, "backend": b["kind"]}


def conf_disable(b: dict, regen: bool = True) -> dict:
    lines = _conf_lines(b["conf"])
    if any(MARK in ln for ln in lines):
        _write_conf(b["conf"], _strip_ours(lines))
    if os.path.exists(MKINIT_DROPIN):
        try:
            os.remove(MKINIT_DROPIN)
        except OSError:
            pass
    if regen:
        for cmd in b["regen"]:
            ok, out = _run(cmd)
            if not ok:
                return {"ok": False, "error": f"{cmd[0]}: {out}"}
    if "clawdeck-" not in (_arg(_cmdline(), "drm.edid_firmware") or ""):
        for f in sorted(glob.glob(f"{EDID_DIR}/clawdeck-*.bin")):   # still in use until the restart otherwise
            try:
                os.remove(f)
            except OSError:
                pass
    return {"ok": True, "restart": True, "method": "boot"}


# ---- live: any distribution, no restart -----------------------------------------------------------------------------
# The kernel's DRM debugfs takes an EDID override per connector (edid_override), and writing "detect" to the
# connector's sysfs status probes it again: the driver then reads the patched EDID and its VRR range at once. This
# needs no bootloader, initramfs or rpm-ostree, so it works on SteamOS, CachyOS and Bazzite alike; nothing is written
# to disk, a restart drops it (ClawDeck applies it again when it starts), and undo is "reset" + detect. Kernel lockdown
# (Secure Boot on some kernels) blocks debugfs writes: then Bazzite still has the boot method above.
DEBUGFS = "/sys/kernel/debug/dri"


def _status_path() -> str | None:
    p = panel_edid_path()
    return os.path.join(os.path.dirname(p), "status") if p else None


def live_dir() -> str | None:
    """The panel's DRM debugfs folder (dri/<minor>/eDP-1), when it offers edid_override."""
    p = panel_edid_path()
    m = re.search(r"card(\d+)-", p or "")
    cands = ([f"{DEBUGFS}/{m.group(1)}/{CONNECTOR}"] if m else []) + sorted(glob.glob(f"{DEBUGFS}/*/{CONNECTOR}"))
    for d in cands:
        if os.path.isfile(os.path.join(d, "edid_override")):
            return d
    return None


def live_active() -> bool:
    """The driver runs the patched EDID right now: the panel's EDID in sysfs has the wide range and valid checksums,
    and it isn't the factory EDID (a panel that announced 30 Hz itself needs nothing)."""
    raw = _read_bytes(panel_edid_path())
    p = PANELS.get(panel_id(raw))
    if not raw or not p or vrr_range(raw) != WIDE or not checksums_ok(raw):
        return False
    return sha256(canonical(raw, p["length"]) or raw) != p["physical_sha256"]


def _probe() -> bool:
    st = _status_path()
    if not st:
        return False
    try:
        with open(st, "w") as f:
            f.write("detect")
        return True
    except OSError as e:
        log.warning("re-probing %s: %s", st, e)
        return False


def live_enable() -> dict:
    raw = _read_bytes(panel_edid_path())
    key = panel_id(raw)
    d = live_dir()
    if live_active():
        return {"ok": True, "restart": False, "method": "live", "already": True}
    if not d:
        return {"ok": False, "error": "this kernel offers no EDID override in debugfs"}
    if _arg(_cmdline(), "drm.edid_firmware"):
        return {"ok": False, "error": "a boot-time EDID override is active; ClawDeck leaves it alone"}
    try:
        data = patch(raw, key)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        with open(os.path.join(d, "edid_override"), "wb") as f:
            f.write(data)
    except OSError as e:
        return {"ok": False, "error": f"debugfs refused the EDID ({e.strerror or e}); kernel lockdown?"}
    if not _probe() or not live_active():
        live_disable()                                       # never leave a half-applied override behind
        return {"ok": False, "error": "the driver didn't take the corrected range; put back as it was"}
    return {"ok": True, "restart": False, "method": "live"}


def live_disable() -> dict:
    d = live_dir()
    if not d:
        return {"ok": True, "restart": False}
    try:
        with open(os.path.join(d, "edid_override"), "w") as f:
            f.write("reset")
    except OSError as e:
        return {"ok": False, "error": f"debugfs: {e.strerror or e}"}
    _probe()
    return {"ok": True, "restart": False, "method": "live"}


_idle_hold: bool | None = None


def gamescope_idle_hold() -> bool:
    """gamescope 3.17+ holds the refresh rate at the panel's maximum while Steam's own UI is focused
    (adaptive_sync_idle_hold, on by default; ValveSoftware/gamescope 8e0a5be). With it, VRR can stay on in the menus,
    so ClawDeck's menus-fixed VRR scope (src/vrr.ts) isn't needed: its off/on switch is a full modeset each time.
    Bazzite 45.20261104 ships gamescope 3.17.0-ogc1 (read on the maintainer's Claw, 2026-11-05)."""
    global _idle_hold
    if _idle_hold is None:
        exe = shutil.which("gamescope")
        found = False
        if exe:
            try:
                with open(exe, "rb") as f:
                    found = b"adaptive_sync_idle_hold" in f.read()
            except OSError:
                found = False
        _idle_hold = found
    return _idle_hold
