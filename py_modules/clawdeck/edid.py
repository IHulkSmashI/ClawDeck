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
import threading
import itertools
import functools

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
    b = boot_backend()                                 # can it be turned on here
    cp = conf_pending(boot_backend(for_enable=False))  # is ClawDeck's line in the config (also where it can't be added now)
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
    taken = ours and rng == WIDE                       # booted with ours AND the driver reports the wide range
    can = shutil.which("rpm-ostree") is not None or b is not None or live_dir() is not None
    if supported and not ours and not live and not can:
        if distro.info()["kind"] == "steamos":
            why = ("on this SteamOS the graphics driver starts from the initramfs, before /etc is there, so a boot "
                   "setting can't hand it the corrected range" if _driver_in_initramfs() else
                   "this SteamOS lacks /etc/default/grub, update-grub or its keep-list folder, which the fix needs")
        else:
            why = "the fix needs a boot setting ClawDeck can't make on this system; Bazzite, SteamOS and CachyOS have it"
        supported, reason = False, why
    if ours and not taken:
        reason = (f"started with the fix, but the driver still reports {rng[0]}-{rng[1]} Hz: it didn't take the "
                  "corrected range at boot" if rng else "started with the fix, but the screen's range can't be read")
    if _after_update_error and not ours and cp != "on":
        reason = f"couldn't put the fix back after the SteamOS update: {_after_update_error}"
    return {"panel": key or None, "panel_name": p["panel"] if p else None, "range": list(rng) if rng else None,
            "wide": taken or live, "supported": supported or live, "reason": None if live else reason,
            "pending": cp if cp else None if not pending_args else ("on" if "clawdeck-" in pending else "off"),
            "rpm_ostree": shutil.which("rpm-ostree") is not None, "not_taken": bool(ours and not taken),
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


# ---- at boot on CachyOS: its own documented ways to set kernel arguments -------------------------------------------
# CachyOS / Arch (wiki.cachyos.org "Boot Manager Configuration", Arch wiki "Kernel mode setting: EDID"): Limine
#   /etc/default/limine (KERNEL_CMDLINE[default]) + limine-update; systemd-boot /etc/sdboot-manage.conf (LINUX_OPTIONS)
#   + sdboot-manage gen; GRUB /etc/default/grub + grub-mkconfig. The EDID goes into the initramfs through a mkinitcpio
#   drop-in (FILES+=, mkinitcpio 38+), so the driver has it at its first probe.
# Each config is a shell file its tool sources, so ClawDeck appends its arguments on ONE line ending in its marker, and
# undo deletes exactly the lines with the marker: nothing else in the file is touched.
# Only where that is safe (independent review, 2026-11-07):
#   - Limine: only when the file already sets KERNEL_CMDLINE[default] itself. limine-entry-tool falls back to
#     /etc/kernel/cmdline or /proc/cmdline only while KERNEL_CMDLINE is unset; setting it with just our two arguments
#     would write entries without root= (no boot). And no KERNEL_CMDLINE in /etc/limine-entry-tool.d, which would win.
#   - systemd-boot: only with OVERWRITE_EXISTING="yes"; by default sdboot-manage gen leaves existing entries alone.
#   - GRUB: on CachyOS / Arch only, with no /etc/default/grub.d drop-ins that could override the line.
# SteamOS (steamos-customizations: grub/bin/update-grub, atomic-update/rauc/*): Holo's update-grub runs grub-mkconfig on
#   /etc/default/grub and writes /efi/EFI/SteamOS/grub.cfg, the documented way to add kernel arguments there. An update
#   keeps only the /etc files on its keep list, plus drop-ins in /etc/atomic-update.conf.d/*.conf (kept by default).
#   ClawDeck keep-lists only its own EDID file (/etc/firmware/edid/clawdeck-*.bin: no SteamOS file is ever shadowed),
#   and NOT /etc/default/grub: keeping that whole file would hide Valve's future changes to it. So an update brings
#   back Valve's grub file without our line; at the next start ClawDeck adds its one line again on top of the new file
#   (steamos_after_update) and one restart brings the range back. The initramfs is Valve's (its hooks carry no GPU
#   driver): xe / i915 load after switch_root, when the /etc overlay with the EDID is there. ClawDeck checks that in the
#   running initramfs (lsinitcpio) and doesn't offer the fix where the driver would start before /etc.
# Regenerating runs as its own systemd unit, so a Steam or plugin restart can't kill mkinitcpio halfway through.
MARK = "# ClawDeck VRR range fix"
MKINIT_DROPIN = "/etc/mkinitcpio.conf.d/clawdeck-edid.conf"
KEEP_CONF = "/etc/atomic-update.conf.d/clawdeck-vrr.conf"
GRUB_LINE = 'GRUB_CMDLINE_LINUX_DEFAULT="${{GRUB_CMDLINE_LINUX_DEFAULT}} {args}"'
OURS = "drm.edid_firmware=eDP-1:edid/clawdeck-"


def _conf_lines(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().splitlines()
    except OSError:
        return []


def _sets(lines: list[str], pattern: str) -> bool:
    rx = re.compile(pattern)
    return any(rx.match(ln.strip()) for ln in lines if not ln.strip().startswith("#") and MARK not in ln)


_initramfs_cache: dict = {}


def _driver_in_initramfs() -> bool | None:
    """Is the screen's driver (xe / i915) inside the running kernel's initramfs? Then it probes before the real /etc is
    there and can't read an EDID from it. None when that can't be told (no lsinitcpio, image not found)."""
    drv = _gpu_driver()
    if drv not in ("xe", "i915"):
        return None
    try:                                              # built into the kernel: it probes before any /etc either
        with open(f"/usr/lib/modules/{os.uname().release}/modules.builtin", encoding="utf-8") as f:
            if any(re.search(rf"/{drv}\.ko$", ln.strip()) for ln in f):
                return True
    except OSError:
        pass
    m = re.search(r"BOOT_IMAGE=\S*?vmlinuz-(\S+)", _cmdline())
    if not m or not shutil.which("lsinitcpio"):
        return None
    img = f"/boot/initramfs-{m.group(1)}.img"
    try:
        key = (img, os.stat(img).st_mtime, drv)
    except OSError:
        return None
    if key not in _initramfs_cache:
        try:
            r = subprocess.run(["lsinitcpio", img], capture_output=True, text=True, timeout=60, check=False,
                               env=sysfs.clean_env())
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
        rx = re.compile(rf"/{drv}\.ko(\.\w+)?$")
        _initramfs_cache.clear()
        _initramfs_cache[key] = any(rx.search(ln.strip()) for ln in r.stdout.splitlines())
    return _initramfs_cache[key]


def boot_backend(for_enable: bool = True) -> dict | None:
    """How this system takes kernel arguments safely: {"kind", "conf", "line" (format with {args}), "regen":
    [commands], "initramfs": bool}, or None (then the live method). for_enable=False: the backend to take ClawDeck's
    line OUT again (Remove, uninstall), also where it couldn't be added any more."""
    if shutil.which("rpm-ostree"):
        return {"kind": "rpm-ostree"}
    d = distro.info()
    w = shutil.which
    if d["kind"] == "steamos":                        # (it calls itself Arch-like in os-release: checked first)
        if not (os.path.isfile("/etc/default/grub") and w("update-grub")):
            return None
        if for_enable and (not os.path.isdir(os.path.dirname(KEEP_CONF)) or _driver_in_initramfs()):
            return None
        return {"kind": "steamos-grub", "conf": "/etc/default/grub", "line": GRUB_LINE, "regen": [["update-grub"]],
                "initramfs": False, "keep": KEEP_CONF}
    if d["kind"] != "cachyos" and "arch" not in d.get("like", "").split():
        return None
    mkinit = w("mkinitcpio") is not None
    if os.path.isfile("/etc/default/limine") and (w("limine-update") or w("limine-mkinitcpio")):
        lines = _conf_lines("/etc/default/limine")
        extra = [ln for f in sorted(glob.glob("/etc/limine-entry-tool.d/*.conf")) for ln in _conf_lines(f)]
        if not _sets(lines, r"KERNEL_CMDLINE\[default\]\+?=") or _sets(extra, r"KERNEL_CMDLINE"):
            return None
        return {"kind": "limine", "conf": "/etc/default/limine", "line": 'KERNEL_CMDLINE[default]+=" {args}"',
                "regen": [["limine-update"]] if w("limine-update") else [["limine-mkinitcpio"]], "initramfs": mkinit}
    if os.path.isfile("/etc/sdboot-manage.conf") and w("sdboot-manage"):
        if not _sets(_conf_lines("/etc/sdboot-manage.conf"), r"""OVERWRITE_EXISTING=["']?yes"""):
            return None
        regen = ([["mkinitcpio", "-P"]] if mkinit else []) + [["sdboot-manage", "gen"]]
        return {"kind": "systemd-boot", "conf": "/etc/sdboot-manage.conf", "line": 'LINUX_OPTIONS="${{LINUX_OPTIONS}} {args}"',
                "regen": regen, "initramfs": mkinit}
    if os.path.isfile("/etc/default/grub") and w("grub-mkconfig") and os.path.isfile("/boot/grub/grub.cfg") \
            and not glob.glob("/etc/default/grub.d/*"):
        regen = ([["mkinitcpio", "-P"]] if mkinit else []) + [["grub-mkconfig", "-o", "/boot/grub/grub.cfg"]]
        return {"kind": "grub", "conf": "/etc/default/grub", "line": GRUB_LINE,
                "regen": regen, "initramfs": mkinit}
    return None


def conf_pending(b: dict | None) -> str | None:
    """"on" when ClawDeck's line is in the boot config, "off" when not; None without such a backend."""
    if not b or b["kind"] == "rpm-ostree":
        return None
    return "on" if any(MARK in ln or MARK_BETA4 in ln for ln in _conf_lines(b["conf"])) else "off"


def _write_conf(path: str, lines: list[str]) -> None:
    """Atomic, into the real file (a symlinked config stays a symlink), flushed to disk, mode kept."""
    real = os.path.realpath(path)
    tmp = real + ".clawdeck-tmp"
    st = os.stat(real) if os.path.exists(real) else None
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip("\n") + "\n")
            f.flush()
            os.fsync(f.fileno())
        if st:
            os.chmod(tmp, st.st_mode & 0o7777)
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except OSError:
                pass
        os.replace(tmp, real)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _strip_ours(lines: list[str]) -> list[str]:
    """Only ClawDeck's own lines: the marker is on the line itself (and an older two-line form, marker comment then a
    line with our argument, is taken only when that next line really carries ClawDeck's EDID argument)."""
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if (MARK in ln or MARK_BETA4 in ln) and ln.lstrip().startswith("#") and i + 1 < len(lines) and OURS in lines[i + 1]:
            i += 2
            continue
        if MARK in ln:
            i += 1
            continue
        out.append(ln)
        i += 1
    return out


_lock = threading.RLock()        # one boot-config change at a time: the start-up re-add never races a tap in Fixes
_unit_no = itertools.count(1)


def _locked(fn):
    @functools.wraps(fn)
    def run(*a, **k):
        with _lock:
            return fn(*a, **k)
    return run


def _regen(cmd: list[str]) -> tuple[bool, str]:
    """Bootloader / initramfs tools as their own systemd unit, waited for with no time limit: killing mkinitcpio
    halfway (a timeout, a Steam or plugin restart) could leave a cut-off initramfs."""
    if shutil.which("systemd-run"):
        cmd = ["systemd-run", "--wait", "--collect", "--pipe", "--quiet", "--unit", f"clawdeck-regen-{os.getpid()}-{next(_unit_no)}", *cmd]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, check=False, env=sysfs.clean_env())
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if r.returncode != 0:
        log.warning("%s failed: %s", " ".join(cmd[-3:]), (r.stderr or r.stdout).strip()[-400:])
    return r.returncode == 0, (r.stderr or r.stdout).strip()[-400:]


def _other_fw_path(b: dict) -> str | None:
    """A firmware_class.path the user set themselves (running, or in the config): ClawDeck won't replace it."""
    cur = _arg(_cmdline(), "firmware_class.path")
    if cur and cur != FW_DIR:
        return cur
    for ln in _conf_lines(b["conf"]):
        if MARK not in ln and not ln.strip().startswith("#"):
            m = re.search(r"firmware_class\.path=(\S+?)[\s\"']", ln + " ")
            if m and m.group(1) != FW_DIR:
                return m.group(1)
    return None


STEAMOS_GRUB_CFG = "/efi/EFI/SteamOS/grub.cfg"


def _undo_line(b: dict) -> None:
    """Only ClawDeck's line out of the config again and regenerate: the EDID and the keep-list entry stay."""
    try:
        _write_conf(b["conf"], _strip_ours(_conf_lines(b["conf"])))
    except OSError:
        return
    for c in b["regen"]:
        _regen(c)


@_locked
def conf_enable(b: dict, after_update: bool = False) -> dict:
    booted = _arg(_cmdline(), "drm.edid_firmware") or ""
    if booted and "clawdeck-" not in booted:
        return {"ok": False, "error": "another EDID override is active; ClawDeck leaves it alone"}
    other = _other_fw_path(b)
    if other:
        return {"ok": False, "error": f"firmware_class.path is already set to {other}; not changing it"}
    if live_active():                                # switching from the live method: back to the factory EDID first
        live_disable()
    raw = _read_bytes(panel_edid_path())
    key = panel_id(raw)
    name = file_name(key) if key else ""
    dest = f"{EDID_DIR}/{name}"
    if conf_pending(b) == "on" and name and os.path.isfile(dest):
        return {"ok": True, "restart": True, "method": "boot", "already": True}
    if "clawdeck-" in booted:                         # booted with ours: the sysfs EDID is the patched one
        data = _read_bytes(dest)
        if not data or vrr_range(data) != WIDE or not checksums_ok(data):
            return {"ok": False, "error": "restart once first, then turn it on again"}
    else:
        try:
            data = patch(raw, key)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
    args = f"firmware_class.path={FW_DIR} drm.edid_firmware={CONNECTOR}:edid/{name}"
    try:
        os.makedirs(EDID_DIR, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        _write_conf(b["conf"], _strip_ours(_conf_lines(b["conf"])) + [f"{b['line'].format(args=args)}  {MARK}"])
        if b.get("initramfs"):
            os.makedirs(os.path.dirname(MKINIT_DROPIN), exist_ok=True)
            _write_conf(MKINIT_DROPIN, [f"FILES+=({dest})  {MARK}"])
        if b.get("keep"):                             # SteamOS: the EDID file survives updates (only ours)
            _write_conf(b["keep"], [f"## {MARK[2:]}: keeps ClawDeck's corrected screen description (EDID) across",
                                    "## SteamOS updates. ClawDeck -> Fixes -> Remove VRR fix deletes this file.",
                                    f"{EDID_DIR}/clawdeck-*.bin"])
    except OSError as e:
        conf_disable(b, regen=False)
        return {"ok": False, "error": f"couldn't write the boot config: {e.strerror or e}"}
    for cmd in b["regen"]:
        ok, out = _regen(cmd)
        if not ok:
            if after_update:                              # keep the EDID and the keep-list entry: try again next start
                _undo_line(b)
            else:
                conf_disable(b, regen=False)              # back to how it was, then regenerate once more
                for c in b["regen"]:
                    _regen(c)
            return {"ok": False, "error": f"{cmd[0]}: {out}"}
    if b["kind"] == "steamos-grub" and os.path.isfile(STEAMOS_GRUB_CFG):
        cfg = "\n".join(_conf_lines(STEAMOS_GRUB_CFG))
        if OURS not in cfg:                               # update-grub ran, but the kernel line didn't get ours
            if after_update:
                _undo_line(b)
            else:
                conf_disable(b)
            return {"ok": False, "error": "SteamOS's boot menu didn't take the setting (update-grub left it out)"}
    return {"ok": True, "restart": True, "method": "boot", "file": dest, "backend": b["kind"]}


@_locked
def conf_disable(b: dict, regen: bool = True) -> dict:
    try:
        if b.get("keep") and os.path.exists(b["keep"]):   # first: an interrupted Remove never comes back at start
            os.remove(b["keep"])
        lines = _conf_lines(b["conf"])
        if any(MARK in ln or MARK_BETA4 in ln for ln in lines):
            _write_conf(b["conf"], _strip_ours(lines))
        if os.path.exists(MKINIT_DROPIN):
            os.remove(MKINIT_DROPIN)
    except OSError as e:
        return {"ok": False, "error": f"couldn't write the boot config: {e.strerror or e}"}
    if regen:
        for cmd in b["regen"]:
            ok, out = _regen(cmd)
            if not ok:
                return {"ok": False, "error": f"{cmd[0]}: {out}"}
    remove_files_unless_booted()
    return {"ok": True, "restart": True, "method": "boot"}


@_locked
def steamos_beta4_cleanup() -> dict | None:
    """Beta 4 (out for a few hours on 2026-11-07) put the VRR fix into SteamOS's /etc/default/grub: a marker comment
    line, then a GRUB_CMDLINE_LINUX_DEFAULT line with ClawDeck's EDID argument, regenerated with steamos-update-grub or
    grub-mkconfig into SteamOS's grub.cfg, without the keep-list drop-in. That form is taken out again, once, at start
    (Fixes sets it up again the beta 7 way). None when there is nothing to undo."""
    if distro.info()["kind"] != "steamos":
        return None
    conf = "/etc/default/grub"
    lines = _conf_lines(conf)
    if not any(MARK_BETA4 in ln for ln in lines):
        return None
    out, i = [], 0
    while i < len(lines):
        if MARK_BETA4 in lines[i] and i + 1 < len(lines) and OURS in lines[i + 1]:
            i += 2
            continue
        if MARK_BETA4 in lines[i]:                       # a lone marker comment: out too, or this runs every start
            i += 1
            continue
        out.append(lines[i])
        i += 1
    try:
        _write_conf(conf, out)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    # Holo's update-grub writes /efi/EFI/SteamOS/grub.cfg (capital S, which beta 4/5 looked for in lower case)
    cfg = next((c for c in ("/efi/EFI/SteamOS/grub.cfg", "/efi/EFI/steamos/grub.cfg") if os.path.isfile(c)), None)
    cmd = (["update-grub"] if shutil.which("update-grub") else ["steamos-update-grub"] if shutil.which("steamos-update-grub")
           else ["grub-mkconfig", "-o", cfg] if cfg else None)
    ok, msg = _regen(cmd) if cmd else (True, "no grub.cfg found")
    remove_files_unless_booted()
    return {"ok": ok, "error": None if ok else msg}


MARK_BETA4 = "# ClawDeck: VRR range fix (Fixes -> Remove VRR fix takes this out)"


_after_update_error: str | None = None


@_locked
def steamos_after_update() -> dict | None:
    """After a SteamOS update /etc/default/grub is Valve's new one, without ClawDeck's line; the keep-list drop-in and
    the EDID file it kept say the fix was on. ClawDeck puts its one line back on top of the new file and regenerates:
    the next restart has the wide range again. None when there is nothing to do."""
    global _after_update_error
    if distro.info()["kind"] != "steamos" or not os.path.isfile(KEEP_CONF):
        return None
    undo = boot_backend(for_enable=False)
    if undo and conf_pending(undo) == "on":
        return None
    b = boot_backend()
    if not b or not glob.glob(f"{EDID_DIR}/clawdeck-*.bin"):
        # the update made the fix impossible here (driver now in the initramfs, ...) or only the drop-in is left:
        # take ClawDeck's files out instead of keeping them forever
        log.info("SteamOS: the VRR fix can't come back after this update; removing its files")
        if undo:
            return conf_disable(undo)
        try:
            os.remove(KEEP_CONF)
        except OSError:
            pass
        remove_files_unless_booted()
        return {"ok": False, "error": "removed: the fix can't be applied on this SteamOS any more"}
    r = conf_enable(b, after_update=True)
    _after_update_error = None if r.get("ok") else r.get("error")
    return r


def remove_files_unless_booted() -> None:
    """ClawDeck's EDID files, unless the running kernel still uses one (then they go at the next undo or uninstall)."""
    if "clawdeck-" in (_arg(_cmdline(), "drm.edid_firmware") or ""):
        return
    for f in sorted(glob.glob(f"{EDID_DIR}/clawdeck-*.bin")):
        try:
            os.remove(f)
        except OSError:
            pass


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


def _gpu_driver() -> str:
    p = panel_edid_path()
    if not p:
        return ""
    try:
        return os.path.basename(os.path.realpath(os.path.join(os.path.dirname(os.path.dirname(p)),
                                                               os.path.basename(os.path.dirname(p)).split("-")[0], "device", "driver")))
    except OSError:
        return ""


def live_dir() -> str | None:
    """The panel's DRM debugfs folder (dri/<minor>/eDP-1), when it offers edid_override and the driver uses it.
    Intel's drivers (i915, xe) read the internal panel's EDID once when they start and keep that copy, so a live
    override never reaches the built-in screen there: on SteamOS 3.9.5 (Claw 8 EX, r/MSIClaw 2026-11-07) the probe
    kept the factory range and ClawDeck put everything back. Not offered on them."""
    if _gpu_driver() in ("i915", "xe"):
        return None
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
