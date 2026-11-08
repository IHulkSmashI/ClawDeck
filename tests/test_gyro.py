"""Claw 8 EX gyro install (gyro.py): MSI's firmware checked before anything lands, every piece revertible, the
InputPlumber override keeps the face-button map, and the VRR fix keeps firmware_class.path while the gyro needs it."""
import hashlib
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))
from clawdeck import buttons, edid, gyro  # noqa: E402

FW = b"\x5a" * 1000
STOCK = """version: 1
kind: CompositeDevice
name: MSI Claw 8 EX AI+ CG3EM
source_devices:
  - group: gamepad
    hidraw:
      vendor_id: 0x0db0

# Optional configuration for the composite device
options:
  auto_manage: true
capability_map_id: claw2
"""


def _kit(member=gyro.KIT_MEMBER, data=FW):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("ISH_Kit/Drivers/Win10/IshHeci/x64/" + member, data)
        z.writestr("ISH_Kit/readme.txt", "x")
    return b.getvalue()


def _env(tmp_path, monkeypatch, rpm=True):
    t = str(tmp_path)
    files = tmp_path / "files"
    files.mkdir()
    (files / gyro.BPF_NAME).write_bytes(b"BPF")
    for h in gyro.HELPERS:
        (files / h).write_text("#!/usr/bin/env python3\n")
    stock = tmp_path / "stock.yaml"
    stock.write_text(STOCK)
    monkeypatch.setattr(gyro, "FILES", str(files))
    monkeypatch.setattr(gyro, "FW_SIZE", len(FW))
    monkeypatch.setattr(gyro, "FW_SHA256", hashlib.sha256(FW).hexdigest())
    monkeypatch.setattr(gyro, "FW_DIR", t + "/etc/firmware")
    monkeypatch.setattr(gyro, "FW_DEST", t + "/etc/firmware/intel/ish/ish_ptl.bin")
    monkeypatch.setattr(gyro, "BPF_DEST", t + "/etc/udev-hid-bpf/" + gyro.BPF_NAME)
    monkeypatch.setattr(gyro, "HELPER_DIR", t + "/etc/clawdeck/gyro")
    monkeypatch.setattr(gyro, "UNIT_DIR", t + "/etc/systemd/system")
    monkeypatch.setattr(gyro, "STATE_DIR", t + "/var/lib/ish-gyro-bias")
    monkeypatch.setattr(buttons, "STOCK_DEVICE", str(stock))
    monkeypatch.setattr(buttons, "DEVICE_OVERRIDE", t + "/etc/inputplumber/devices.d/50.yaml")
    monkeypatch.setattr(buttons, "MAP_OVERRIDE", t + "/etc/inputplumber/capability_maps.d/m.yaml")
    monkeypatch.setattr(buttons, "GYRO_BPF", gyro.BPF_DEST)
    ran, kargs = [], {"pending": "quiet splash"}

    def run(cmd, timeout=120):
        ran.append(cmd)
        if cmd[:2] == ["rpm-ostree", "kargs"] and len(cmd) == 2:
            return True, kargs["pending"]
        if cmd[:2] == ["rpm-ostree", "kargs"]:
            for a in cmd[2:]:
                if a.startswith("--append-if-missing="):
                    kargs["pending"] += " " + a.split("=", 1)[1]
                if a.startswith("--delete-if-present="):
                    kargs["pending"] = " ".join(x for x in kargs["pending"].split() if x != a.split("=", 1)[1])
        return True, ""
    monkeypatch.setattr(gyro, "_run", run)

    class R:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(buttons, "_run", lambda cmd, timeout=30: ran.append(cmd) or R())
    monkeypatch.setattr(gyro.shutil, "which", lambda n: "/usr/bin/" + n if (rpm or n != "rpm-ostree") else None)
    monkeypatch.setattr(gyro, "_uhb_present", lambda: True)
    monkeypatch.setattr(gyro, "_cmdline", lambda: "quiet splash")
    return ran, kargs


def test_install_puts_every_piece_in_place_and_remove_takes_it_all_out(tmp_path, monkeypatch):
    ran, kargs = _env(tmp_path, monkeypatch)
    r = gyro.enable(fetch=lambda url: _kit())
    assert r["ok"] and r["restart"], r
    assert open(gyro.FW_DEST, "rb").read() == FW
    assert os.path.exists(gyro.BPF_DEST) and gyro.installed()
    assert all(os.path.exists(os.path.join(gyro.HELPER_DIR, h)) for h in gyro.HELPERS)
    unit = open(os.path.join(gyro.UNIT_DIR, "clawdeck-gyro-keepalive.service")).read()
    assert "ExecStart=/usr/bin/python3 /etc/clawdeck/gyro/ish_imu_keepalive.py" in unit
    assert ["systemctl", "enable", "--now", *gyro.UNITS] in ran
    assert gyro.FW_ARG.replace(gyro.FW_DIR, gyro.FW_DIR) in kargs["pending"]
    ov = open(buttons.DEVICE_OVERRIDE).read()
    assert "name: gyro_3d" in ov and "capability_map_id: claw2" in ov            # stock map kept: face fix wasn't on
    assert ov.index("name: gyro_3d") < ov.index("options:")
    r = gyro.disable()
    assert r["ok"]
    for p in (gyro.FW_DEST, gyro.BPF_DEST, gyro.HELPER_DIR, buttons.DEVICE_OVERRIDE,
              os.path.join(gyro.UNIT_DIR, "clawdeck-gyro-bias.service")):
        assert not os.path.exists(p), p
    assert gyro.FW_ARG not in kargs["pending"]


def test_a_firmware_that_isnt_the_tested_one_is_never_written(tmp_path, monkeypatch):
    ran, kargs = _env(tmp_path, monkeypatch)
    r = gyro.enable(fetch=lambda url: _kit(data=b"\x00" * 1000))
    assert not r["ok"] and "isn't the tested one" in r["error"]
    assert not os.path.exists(gyro.FW_DEST) and not os.path.exists(gyro.BPF_DEST)
    r = gyro.enable(fetch=lambda url: _kit(member="FWImage/0003/ishC_other.bin"))
    assert not r["ok"] and "has no" in r["error"] and not os.path.exists(gyro.FW_DEST)
    assert not any(c[:2] == ["rpm-ostree", "kargs"] and len(c) > 2 for c in ran)


def test_someone_elses_firmware_or_path_is_left_alone(tmp_path, monkeypatch):
    ran, kargs = _env(tmp_path, monkeypatch)
    os.makedirs(os.path.dirname(gyro.FW_DEST))
    open(gyro.FW_DEST, "wb").write(b"theirs")
    r = gyro.enable(fetch=lambda url: _kit())
    assert not r["ok"] and open(gyro.FW_DEST, "rb").read() == b"theirs"
    os.remove(gyro.FW_DEST)
    kargs["pending"] = "quiet firmware_class.path=/opt/fw"
    r = gyro.enable(fetch=lambda url: _kit())
    assert not r["ok"] and "/opt/fw" in r["error"]


def test_the_face_button_map_survives_the_gyro_and_the_other_way_round(tmp_path, monkeypatch):
    ran, kargs = _env(tmp_path, monkeypatch)
    assert buttons.apply()["ok"]
    assert gyro.enable(fetch=lambda url: _kit())["ok"]
    ov = open(buttons.DEVICE_OVERRIDE).read()
    assert f"capability_map_id: {buttons.MAP_ID}" in ov and "name: gyro_3d" in ov
    assert buttons.remove()["ok"]                                               # face fix out, gyro stays
    ov = open(buttons.DEVICE_OVERRIDE).read()
    assert "capability_map_id: claw2" in ov and "name: gyro_3d" in ov
    assert gyro.disable()["ok"] and not os.path.exists(buttons.DEVICE_OVERRIDE)


def test_bazzite_only_for_now(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch, rpm=False)
    r = gyro.enable(fetch=lambda url: _kit())
    assert not r["ok"] and "Bazzite" in r["error"]


def test_vrr_fix_keeps_firmware_class_path_while_the_gyro_firmware_is_there(tmp_path, monkeypatch):
    fw = tmp_path / "ish_ptl.bin"
    fw.write_bytes(FW)
    monkeypatch.setattr(edid, "GYRO_FW", str(fw))
    monkeypatch.setattr(edid, "EDID_DIR", str(tmp_path / "edid"))
    calls = []
    monkeypatch.setattr(edid, "_kargs_pending", lambda: "quiet firmware_class.path=/etc/firmware drm.edid_firmware=eDP-1:edid/clawdeck-x.bin")
    monkeypatch.setattr(edid, "_run", lambda cmd: calls.append(cmd) or (True, ""))
    assert edid.disable()["ok"]
    args = [a for c in calls if c[:2] == ["rpm-ostree", "kargs"] for a in c[2:]]
    assert any("drm.edid_firmware" in a for a in args) and not any("firmware_class.path" in a for a in args)
    fw.unlink()
    calls.clear()
    assert edid.disable()["ok"]
    args = [a for c in calls if c[:2] == ["rpm-ostree", "kargs"] for a in c[2:]]
    assert any("firmware_class.path" in a for a in args)


def test_status_only_on_the_claw_8_ex_with_a_panther_lake_sensor_hub(monkeypatch):
    monkeypatch.setattr(gyro, "_has_ish", lambda: True)
    assert gyro.status("MS-1T52") == {"applies": False}
    monkeypatch.setattr(gyro, "_has_ish", lambda: False)
    assert gyro.status("MS-1T91") == {"applies": False}


def test_accelerometer_is_off_by_default_and_toggles_as_a_second_imu_source(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    assert not gyro.set_accel(True)["ok"]                                       # gyro first
    assert gyro.enable(fetch=lambda url: _kit())["ok"]
    ov = open(buttons.DEVICE_OVERRIDE).read()
    assert "name: gyro_3d" in ov and "name: accel_3d" not in ov
    assert gyro.set_accel(True)["ok"]
    ov = open(buttons.DEVICE_OVERRIDE).read()
    assert "name: gyro_3d" in ov and "name: accel_3d" in ov and ov.index("accel_3d") < ov.index("options:")
    assert buttons.apply()["ok"]                                                # face fix keeps the accelerometer
    assert buttons.accel_in_override() and f"capability_map_id: {buttons.MAP_ID}" in open(buttons.DEVICE_OVERRIDE).read()
    assert gyro.set_accel(False)["ok"] and not buttons.accel_in_override() and buttons.imu_in_override()
    assert gyro.disable()["ok"] and not buttons.imu_in_override()


def test_a_hand_installed_copy_of_the_helpers_is_turned_off_not_doubled(tmp_path, monkeypatch):
    ran, kargs = _env(tmp_path, monkeypatch)
    os.makedirs(gyro.UNIT_DIR, exist_ok=True)
    for u in gyro.MANUAL_UNITS:
        open(os.path.join(gyro.UNIT_DIR, u), "w").write("[Service]\n")
    r = gyro.enable(fetch=lambda url: _kit())
    assert r["ok"] and r["manual_off"] == list(gyro.MANUAL_UNITS)
    assert ["systemctl", "disable", "--now", *gyro.MANUAL_UNITS] in ran
    assert all(os.path.exists(os.path.join(gyro.UNIT_DIR, u)) for u in gyro.MANUAL_UNITS)   # their files stay
