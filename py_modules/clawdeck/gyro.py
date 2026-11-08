"""Gyro on the MSI Claw 8 EX AI+ (MS-1T91): the IMU behind Intel's sensor hub (ISH), into Steam through InputPlumber.

Found and tested on a Claw 8 EX by Kisenir (r/MSIClaw, 2026-11-08); ClawDeck packages his method, see CREDITS.md:
  1. ISH firmware. Panther Lake's ISH loads its firmware from the host (drivers/hid/intel-ish-hid/ishtp/loader.c,
     prefix "ptl"); the generic intel/ish/ish_ptl.bin in linux-firmware is not MSI's build, and MSI's is not in
     linux-firmware. MSI's own ISH kit carries it (FWImage/0004/ishS_SI_5.8.1.7782.bin). MSI doesn't allow passing it
     on, so ClawDeck downloads MSI's kit on the Claw, takes that one file, checks its size and sha256 (a wrong image
     makes the hub stop answering until a full power-off), and installs it as /etc/firmware/intel/ish/ish_ptl.bin
     with the kernel argument firmware_class.path=/etc/firmware (the same one the VRR fix uses; whoever removes last
     takes it out). The kernel looks there before /usr/lib/firmware.
  2. HID-BPF (tools/gyro/MSI__Claw-ISH-IMU.bpf.c, built from source in CI): the hub declares the IMU as two
     "custom" HID sensors no Linux driver binds; the program relabels them, length-preserving, as a standard
     Accelerometer 3D and Gyrometer 3D, so hid-sensor-hub creates accel_3d / gyro_3d IIO devices. It also subtracts
     a per-axis gyro bias. Loaded by udev-hid-bpf from /etc/udev-hid-bpf/ and only on the known descriptor (2247 bytes,
     checked bytes), so another firmware layout is left alone.
  3. Two small services (gyro_files/): a keepalive that writes the standard HID-sensor config (reporting, power,
     10 ms interval) when InputPlumber opens the gyro, never reading feature reports back and never sending vendor
     commands; and a bias helper that keeps the resting reading at 0 while the Claw lies still.
  4. InputPlumber: an imu source (IIO gyro_3d) in ClawDeck's override of the board's device config (buttons.py),
     with the Steam Deck target, so Steam Input sees a gyro. Accelerometer off: Steam's fusion made gyro aim worse.
Everything is ClawDeck's files only and comes out again with Remove. Bazzite (rpm-ostree) for now.
"""
from __future__ import annotations

import glob
import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from . import buttons, sysfs, tls

log = logging.getLogger("clawdeck.gyro")

BOARD = "MS-1T91"
KIT_URL = "https://download.msi.com/nb_drivers/cs/ISH_Kit_5.8.1.7782_1T91%20_0318_5.8.58.0_0x96746247.zip"
KIT_MEMBER = "FWImage/0004/ishS_SI_5.8.1.7782.bin"      # ends the zip path (Drivers/Win10/IshHeci/x64/...)
FW_SIZE = 432640
FW_SHA256 = "8194475367978c473d17e7e777fe196d99383f80f51b433927ea9afd0e9e661b"
FW_DIR = "/etc/firmware"
FW_DEST = f"{FW_DIR}/intel/ish/ish_ptl.bin"
FW_ARG = f"firmware_class.path={FW_DIR}"
ISH_PCI = "/sys/bus/pci/devices/0000:00:12.0"
PTL_ISH_IDS = ("0xe345", "0xe445")                     # Panther Lake ISH (pci-ish.c)
HUB = ("8087", "0AC2")
BPF_NAME = "MSI__Claw-ISH-IMU.bpf.o"
BPF_DEST = f"/etc/udev-hid-bpf/{BPF_NAME}"
FILES = os.path.join(os.path.dirname(__file__), "gyro_files")
HELPER_DIR = "/etc/clawdeck/gyro"
HELPERS = ("ish_imu_keepalive.py", "ish_gyro_bias.py", "ish_sensor_probe.py")
STATE_DIR = "/var/lib/ish-gyro-bias"
UNITS = {
    "clawdeck-gyro-keepalive.service": f"""[Unit]
Description=ClawDeck: MSI Claw ISH IMU keep-alive (gyro power and report rate while InputPlumber reads it)
After=systemd-udev-settle.service

[Service]
ExecStart=/usr/bin/python3 {HELPER_DIR}/ish_imu_keepalive.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
""",
    "clawdeck-gyro-bias.service": f"""[Unit]
Description=ClawDeck: gyro bias correction for the MSI Claw ISH IMU
After=clawdeck-gyro-keepalive.service
Wants=clawdeck-gyro-keepalive.service

[Service]
ExecStart=/usr/bin/python3 {HELPER_DIR}/ish_gyro_bias.py
Restart=always
RestartSec=3
StateDirectory=ish-gyro-bias

[Install]
WantedBy=multi-user.target
""",
}
UNIT_DIR = "/etc/systemd/system"


def _run(cmd: list[str], timeout: float = 120) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False, env=sysfs.clean_env())
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[-1500:]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return _sha256(f.read())
    except OSError:
        return None


def _cmdline() -> str:
    return sysfs.read("/proc/cmdline") or ""


def _kargs_pending() -> str:
    ok, out = _run(["rpm-ostree", "kargs"], 30) if shutil.which("rpm-ostree") else (False, "")
    return out if ok else ""


def _has_ish() -> bool:
    return (sysfs.read(f"{ISH_PCI}/vendor") or "").strip() == "0x8086" and \
        (sysfs.read(f"{ISH_PCI}/device") or "").strip().lower() in PTL_ISH_IDS


def hub_present() -> bool:
    """The sensor hub's HID device: there once MSI's ISH firmware runs."""
    for p in glob.glob("/sys/class/hidraw/hidraw*/device/uevent"):
        u = (sysfs.read(p) or "").upper()
        if HUB[0] in u and HUB[1] in u:
            return True
    return False


def iio(name: str) -> str | None:
    for d in glob.glob("/sys/bus/iio/devices/iio:device*"):
        if (sysfs.read(f"{d}/name") or "").strip() == name:
            return d
    return None


def _uhb_present() -> bool:
    return bool(shutil.which("udev-hid-bpf")) or bool(glob.glob("/usr/lib/udev/rules.d/*hid-bpf*.rules"))


def _unit_state(unit: str) -> str:
    ok, out = _run(["systemctl", "is-active", unit], 10)
    return out.splitlines()[-1].strip() if out else ("active" if ok else "inactive")


def installed() -> bool:
    """ClawDeck's gyro files are in place (firmware, program, helpers, units)."""
    return (_file_sha(FW_DEST) == FW_SHA256 and os.path.exists(BPF_DEST)
            and all(os.path.exists(os.path.join(UNIT_DIR, u)) for u in UNITS))


def status(board: str | None) -> dict:
    if board != BOARD or not _has_ish():
        return {"applies": False}
    fw = _file_sha(FW_DEST)
    arg_now = FW_ARG in _cmdline().split()
    on = installed()
    gyro = iio("gyro_3d")
    ip = buttons.imu_in_override()
    hub = hub_present()
    services = {u: _unit_state(u) for u in UNITS} if on else {}
    if not on:
        state, line = "off", "Gyro is off: the Claw 8 EX's gyro needs MSI's sensor-hub firmware, which ClawDeck can install"
    elif not arg_now or not hub or not gyro:
        state, line = "restart", ("Gyro installed: shut the Claw down fully and turn it on again (a cold boot, not a "
                                  "restart), then recalibrate gyro in Steam (Controller settings)")
    elif not ip or any(v != "active" for v in services.values()):
        state, line = "partial", "Gyro: the sensor works, but InputPlumber or ClawDeck's gyro services aren't running"
    else:
        state, line = "on", "Gyro on: Steam sees it as a Steam Deck gyro (recalibrate once in Steam's controller settings)"
    why = None
    if not on and not shutil.which("rpm-ostree"):
        why = "Gyro install needs Bazzite for now (it sets a kernel argument with rpm-ostree)"
    elif not on and not _uhb_present():
        why = "this system has no udev-hid-bpf, which loads the gyro program"
    return {"applies": True, "state": state, "line": why or line, "installed": on, "supported": why is None,
            "firmware_ok": fw == FW_SHA256, "kernel_arg": arg_now, "hub": hub, "gyro_iio": bool(gyro),
            "inputplumber_imu": ip, "services": services, "accel": buttons.accel_in_override()}


def _fetch_firmware(fetch=None) -> bytes:
    """MSI's ISH kit -> the one firmware file, checked by size and sha256. Nothing is written if it doesn't match."""
    data = fetch(KIT_URL) if fetch else _download(KIT_URL)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if n.replace("\\", "/").lower().endswith(KIT_MEMBER.lower())]
        if not names:
            raise ValueError(f"MSI's ISH kit has no {KIT_MEMBER}")
        fw = z.read(names[0])
    if len(fw) != FW_SIZE or _sha256(fw) != FW_SHA256:
        raise ValueError(f"the firmware in MSI's kit isn't the tested one ({len(fw)} bytes, sha256 {_sha256(fw)[:12]}...)")
    return fw


def _download(url: str) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "ClawDeck"})
    with tls.urlopen(req, 120) as r:
        return r.read(64 * 1024 * 1024)


def _write(path: str, data: bytes, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".clawdeck-tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _other_firmware() -> bool:
    """Anything else of ClawDeck's under /etc/firmware (the VRR fix's EDID) still needs firmware_class.path."""
    return bool(glob.glob(f"{FW_DIR}/edid/clawdeck-*.bin"))


def enable(fetch=None) -> dict:
    """Install the four pieces (module docstring). The kernel argument and the firmware take effect at the next
    restart; the rest is ready before it."""
    if not shutil.which("rpm-ostree"):
        return {"ok": False, "error": "Gyro install needs Bazzite for now (it sets a kernel argument with rpm-ostree)"}
    if not _uhb_present():
        return {"ok": False, "error": "this system has no udev-hid-bpf, which loads the gyro program"}
    bpf = os.path.join(FILES, BPF_NAME)
    if not os.path.isfile(bpf):
        return {"ok": False, "error": "the gyro program isn't in this ClawDeck build"}
    pending = _kargs_pending() or _cmdline()
    m = re.search(r"firmware_class\.path=(\S+)", pending)
    if m and m.group(1) != FW_DIR:
        return {"ok": False, "error": f"firmware_class.path is already set to {m.group(1)}; not changing it"}
    cur = _file_sha(FW_DEST)
    if cur is not None and cur != FW_SHA256:
        return {"ok": False, "error": f"{FW_DEST} exists and isn't MSI's tested firmware; leaving it"}
    done: list[str] = []
    try:
        if cur is None:
            _write(FW_DEST, _fetch_firmware(fetch))
            done.append("firmware")
        with open(bpf, "rb") as f:
            _write(BPF_DEST, f.read())
        done.append("bpf")
        for h in HELPERS:
            with open(os.path.join(FILES, h), "rb") as f:
                _write(os.path.join(HELPER_DIR, h), f.read(), 0o755)
        done.append("helpers")
        for u, text in UNITS.items():
            _write(os.path.join(UNIT_DIR, u), text.encode())
        done.append("units")
    except Exception as e:  # noqa: BLE001 - network, zip, disk: undo what was written
        _undo_files(done)
        return {"ok": False, "error": f"gyro install: {e}"}
    _run(["systemctl", "daemon-reload"], 60)
    ok, out = _run(["systemctl", "enable", "--now", *UNITS], 60)
    if not ok:
        _undo_files(done)
        return {"ok": False, "error": f"gyro services: {out}"}
    r = buttons.write_override(imu=True)
    if not r.get("ok"):
        disable()
        return {"ok": False, "error": r.get("error")}
    if FW_ARG not in pending.split():
        ok, out = _run(["rpm-ostree", "kargs", f"--append-if-missing={FW_ARG}"], 300)
        if not ok:
            disable()
            return {"ok": False, "error": f"kernel argument: {out}"}
    log.info("gyro installed (firmware, HID-BPF, services, InputPlumber imu source)")
    return {"ok": True, "restart": True,
            "next": "shut the Claw down fully and turn it on again (a cold boot clears the sensor hub), then "
                    "recalibrate gyro once in Steam (Settings -> Controller -> Gyro)"}


def set_accel(on: bool) -> dict:
    """The accelerometer as a second imu source. Off by default: with it, Steam fuses it with the gyro and gyro
    aiming gets worse (Kisenir's tests); some games' tilt controls want it."""
    if not installed():
        return {"ok": False, "error": "install the gyro first"}
    r = buttons.write_override(imu=True, accel=bool(on))
    if r.get("ok"):
        r["next"] = "accelerometer on (recalibrate gyro in Steam)" if on else "accelerometer off: gyro only"
    return r


def _undo_files(done: list[str]) -> None:
    if "units" in done:
        for u in UNITS:
            _rm(os.path.join(UNIT_DIR, u))
    if "helpers" in done:
        shutil.rmtree(HELPER_DIR, ignore_errors=True)
    if "bpf" in done:
        _rm(BPF_DEST)
    if "firmware" in done:
        _rm(FW_DEST)


def _rm(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def disable() -> dict:
    """Everything enable() did comes out. The firmware already loaded and the relabelled sensor stay until the next
    restart; after it the hub is back to how it was (no gyro)."""
    errors = []
    _run(["systemctl", "disable", "--now", *UNITS], 60)
    for u in UNITS:
        _rm(os.path.join(UNIT_DIR, u))
    _run(["systemctl", "daemon-reload"], 60)
    shutil.rmtree(HELPER_DIR, ignore_errors=True)
    shutil.rmtree(STATE_DIR, ignore_errors=True)
    _rm(BPF_DEST)
    r = buttons.write_override(imu=False)
    if not r.get("ok"):
        errors.append(r.get("error"))
    if _file_sha(FW_DEST) == FW_SHA256:
        _rm(FW_DEST)
    pending = _kargs_pending()
    if shutil.which("rpm-ostree") and FW_ARG in pending.split() and not _other_firmware():
        ok, out = _run(["rpm-ostree", "kargs", f"--delete-if-present={FW_ARG}"], 300)
        if not ok:
            errors.append(f"kernel argument: {out}")
    if errors:
        return {"ok": False, "error": "; ".join(str(e) for e in errors)}
    return {"ok": True, "restart": True, "next": "restart the Claw to finish (gyro off again)"}
