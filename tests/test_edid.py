"""Wider VRR range (edid.py): a synthetic EDID shaped like the Claw 8 panel's (256 bytes, range descriptor in the base
block, DisplayID extension), registered as a validated panel for the test only."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from clawdeck import edid  # noqa: E402


def _csum(e, start):
    e[start + 127] = (256 - sum(e[start:start + 127]) % 256) % 256


def synthetic_edid() -> bytes:
    e = bytearray(256)
    e[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    v = ((ord("C") - 64) << 10) | ((ord("S") - 64) << 5) | (ord("W") - 64)
    e[8], e[9] = v >> 8, v & 0xFF
    e[10], e[11] = 0x01, 0x08            # product code 0x0801, little endian
    e[18], e[19] = 1, 4                  # EDID 1.4
    e[90:93] = b"\x00\x00\x00"
    e[93] = 0xFD                         # range limits descriptor
    e[95], e[96] = 48, 120
    e[126] = 1                           # one extension
    _csum(e, 0)
    e[128] = 0x70                        # DisplayID extension
    e[129], e[130], e[131], e[132] = 0x20, 121, 0, 0
    e[133:145] = bytes([0x2B, 0, 9, 0, 0, 0, 0, 0, 0, 48, 120, 0])   # a block carrying the range at 142/143
    e[254] = (256 - sum(e[129:254]) % 256) % 256                      # DisplayID section checksum
    _csum(e, 128)
    assert e[142] == 48 and e[143] == 120
    return bytes(e)


@pytest.fixture
def panel(monkeypatch):
    raw = synthetic_edid()
    monkeypatch.setitem(edid.PANELS, "CSW0801", dict(edid.PANELS["CSW0801"], physical_sha256=edid.sha256(raw)))
    return raw


def test_panel_id_and_range(panel):
    assert edid.panel_id(panel) == "CSW0801"
    assert edid.vrr_range(panel) == (48, 120)
    assert edid.checksums_ok(panel)


def test_patch_widens_range_and_keeps_every_checksum_valid(panel):
    out = edid.patch(panel, "CSW0801")
    assert edid.vrr_range(out) == (30, 120)
    assert out[142] == 30 and out[143] == 120
    assert edid.checksums_ok(out)                                  # including the DisplayID section Linux checks
    changed = {i for i in range(256) if out[i] != panel[i]}
    assert changed <= {95, 142, 127, 254, 255}


def test_patch_refuses_anything_not_validated(panel):
    other = bytearray(panel)
    other[20] ^= 1
    with pytest.raises(ValueError):
        edid.patch(bytes(other), "CSW0801")
    with pytest.raises(ValueError):
        edid.patch(panel, "XYZ1234")


def test_zero_padded_128_byte_edid_is_normalised():
    base = bytes(range(126)) + b"\x00\x00"
    assert edid.canonical(base + bytes(128), 128) == base
    assert edid.canonical(base + b"\x01" + bytes(127), 128) is None


def test_enable_and_disable_commands(panel, tmp_path, monkeypatch):
    f = tmp_path / "edid"
    f.write_bytes(panel)
    ran = []
    monkeypatch.setattr(edid, "panel_edid_path", lambda: str(f))
    monkeypatch.setattr(edid, "EDID_DIR", str(tmp_path / "fw/edid"))
    monkeypatch.setattr(edid.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(edid, "_cmdline", lambda: "root=x quiet")
    monkeypatch.setattr(edid, "_kargs_pending", lambda: "root=x quiet")
    monkeypatch.setattr(edid, "_run", lambda cmd: (ran.append(cmd), (True, ""))[1])
    r = edid.enable()
    assert r["ok"] and os.path.exists(r["file"])
    assert edid.vrr_range(open(r["file"], "rb").read()) == (30, 120)
    assert ran[0][:2] == ["rpm-ostree", "initramfs-etc"] and ran[0][2].startswith("--track=")
    assert "--append-if-missing=drm.edid_firmware=eDP-1:edid/clawdeck-csw0801-30-120.bin" in ran[1]
    ran.clear()
    monkeypatch.setattr(edid, "_kargs_pending",
                        lambda: "root=x firmware_class.path=/etc/firmware drm.edid_firmware=eDP-1:edid/clawdeck-csw0801-30-120.bin")
    assert edid.disable()["ok"]
    assert ran[0][:2] == ["rpm-ostree", "kargs"] and any("--delete-if-present=drm.edid_firmware" in a for a in ran[0])
    assert not os.path.exists(r["file"])


def test_someone_elses_override_is_left_alone(panel, tmp_path, monkeypatch):
    f = tmp_path / "edid"
    f.write_bytes(panel)
    monkeypatch.setattr(edid, "panel_edid_path", lambda: str(f))
    monkeypatch.setattr(edid.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(edid, "_cmdline", lambda: "drm.edid_firmware=eDP-1:edid/mine.bin")
    monkeypatch.setattr(edid, "_kargs_pending", lambda: "drm.edid_firmware=eDP-1:edid/mine.bin")
    assert not edid.enable()["ok"]
    assert not edid.disable()["ok"]
    st = edid.status()
    assert st["supported"] is False and "another EDID override" in st["reason"]
