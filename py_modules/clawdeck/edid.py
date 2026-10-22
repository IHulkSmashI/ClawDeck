"""Wider VRR range for the Claw's own screen: 30-120 Hz instead of the 48-120 Hz the panel's EDID announces.

The panel itself refreshes down to 30 Hz, but its EDID (the description the screen hands the GPU driver) says 48.
Below 48 fps the driver then holds each frame for a whole 48 Hz refresh, which judders. ClawLab's Intel VRR Range Fix
(github.com/Zendo-GIT/Intel-Handheld-and-ARC-GPU-laboratory, MIT) fixes this on Windows with an EDID override; this
module is the same fix for Linux, with the same safety rule: it only touches the exact panels and the exact factory
EDIDs ClawLab validated (matched by SHA-256), and only the range bytes ClawLab changes.

What changes, and nothing else:
  - base block bytes 95/96 (the monitor range descriptor's min/max vertical rate) and, on the Claw 8 AI+ / 8 EX
    panel, bytes 142/143 in the DisplayID extension (its Adaptive-Sync range), set to 30 and 120;
  - the checksums: each 128-byte block's last byte, plus the DisplayID section checksum, which Linux validates
    (an invalid DisplayID section is dropped whole, and with it the 120 Hz timing; Windows does not check it).

How Linux uses it, the way Bazzite already overrides the AOKZOE A1's EDID (usr/libexec/bazzite-hardware-setup):
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

from . import sysfs

log = logging.getLogger("clawdeck.edid")

FW_DIR = "/etc/firmware"
EDID_DIR = f"{FW_DIR}/edid"
CONNECTOR = "eDP-1"

# From ClawLab's MSI-Claw-VRR-Fix.ps1 panel catalog. Physical = the factory EDID, as the kernel reads it.
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
    """ClawLab's normalisation: exact length, or a 128-byte EDID padded with one all-zero block."""
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
        raise ValueError(f"panel {key or '?'} is not one ClawLab validated")
    base = canonical(edid, p["length"])
    if base is None or sha256(base) != p["physical_sha256"]:
        raise ValueError(f"panel {key} has an EDID ClawLab has not validated ({sha256(edid)[:12]}...)")
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
    supported = False
    reason = None
    if not raw:
        reason = "no internal screen EDID found"
    elif p is None:
        reason = f"screen {key or '?'} is not one of the panels ClawLab validated"
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
    return {"panel": key or None, "panel_name": p["panel"] if p else None, "range": list(rng) if rng else None,
            "wide": ours, "supported": supported, "reason": reason,
            "pending": None if not pending_args else ("on" if "clawdeck-" in pending else "off"),
            "rpm_ostree": shutil.which("rpm-ostree") is not None}


def _run(cmd: list[str], dry_run: bool) -> tuple[bool, str]:
    if dry_run:
        log.info("dry-run: would run %s", " ".join(cmd))
        return True, ""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=False, env=sysfs.clean_env())
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if r.returncode != 0:
        log.warning("%s failed: %s", " ".join(cmd[:3]), (r.stderr or r.stdout).strip()[-400:])
    return r.returncode == 0, (r.stderr or r.stdout).strip()[-400:]


def enable(dry_run: bool = False) -> dict:
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
    if dry_run:
        log.info("dry-run: would write %s (%d bytes, sha256 %s)", dest, len(data), sha256(data))
    else:
        os.makedirs(EDID_DIR, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
    ok, out = _run(["rpm-ostree", "initramfs-etc", f"--track={dest}"], dry_run)
    if not ok:
        return {"ok": False, "error": f"initramfs: {out}"}
    ok, out = _run(["rpm-ostree", "kargs", f"--append-if-missing=firmware_class.path={FW_DIR}",
                    f"--append-if-missing=drm.edid_firmware={CONNECTOR}:edid/{name}"], dry_run)
    if not ok:
        _run(["rpm-ostree", "initramfs-etc", f"--untrack={dest}"], dry_run)
        return {"ok": False, "error": f"kernel arguments: {out}"}
    return {"ok": True, "restart": True, "file": dest, "sha256": sha256(data)}


def disable(dry_run: bool = False) -> dict:
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
        ok, out = _run(["rpm-ostree", "kargs", *args], dry_run)
        if not ok:
            return {"ok": False, "error": f"kernel arguments: {out}"}
    for f in sorted(glob.glob(f"{EDID_DIR}/clawdeck-*.bin")):
        _run(["rpm-ostree", "initramfs-etc", f"--untrack={f}"], dry_run)
        if not dry_run:
            try:
                os.remove(f)
            except OSError:
                pass
    return {"ok": True, "restart": True}
