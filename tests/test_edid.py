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


def _fake_kernel(tmp_path, monkeypatch, raw, writable=True):
    """sysfs card0-eDP-1/{edid,status} and debugfs dri/0/eDP-1/edid_override; writing "detect" to status makes the
    driver read the override (or the factory EDID after "reset"), like drm's probe."""
    sysd = tmp_path / "sys/class/drm/card0-eDP-1"
    sysd.mkdir(parents=True)
    (sysd / "edid").write_bytes(raw)
    (sysd / "status").write_text("connected")
    dbg = tmp_path / "debug/dri/0/eDP-1"
    dbg.mkdir(parents=True)
    (dbg / "edid_override").write_bytes(b"")
    if not writable:
        (dbg / "edid_override").chmod(0o444)
    monkeypatch.setattr(edid, "panel_edid_path", lambda: str(sysd / "edid"))
    monkeypatch.setattr(edid, "DEBUGFS", str(tmp_path / "debug/dri"))
    monkeypatch.setattr(edid, "_cmdline", lambda: "quiet")
    real_probe = edid._probe

    def probe():
        ok = real_probe()
        ov = (dbg / "edid_override").read_bytes()
        (sysd / "edid").write_bytes(raw if ov in (b"", b"reset") else ov)
        return ok
    monkeypatch.setattr(edid, "_probe", probe)
    return sysd, dbg


def test_live_vrr_range_without_restart(panel, tmp_path, monkeypatch):
    """Any distribution: the patched EDID goes into DRM's debugfs override and the panel is probed again, so the
    driver runs 30-120 Hz at once; undo puts the factory EDID back the same way. Nothing on disk, no rpm-ostree."""
    raw = synthetic_edid()
    sysd, dbg = _fake_kernel(tmp_path, monkeypatch, raw)
    monkeypatch.setattr(edid.shutil, "which", lambda n: None)          # SteamOS / CachyOS: no rpm-ostree
    st = edid.status()
    assert st["live_possible"] and st["supported"] and not st["wide"] and st["method"] is None
    r = edid.live_enable()
    assert r["ok"] and not r["restart"] and edid.vrr_range((sysd / "edid").read_bytes()) == (30, 120)
    assert (sysd / "status").read_text() == "detect"
    st = edid.status()
    assert st["wide"] and st["method"] == "live" and st["range"] == [30, 120]
    assert edid.live_enable().get("already")                          # at the next start: nothing to redo
    assert edid.live_disable()["ok"] and (sysd / "edid").read_bytes() == raw and not edid.live_active()


def test_live_vrr_refused_by_the_kernel_changes_nothing(panel, tmp_path, monkeypatch):
    raw = synthetic_edid()
    sysd, dbg = _fake_kernel(tmp_path, monkeypatch, raw)
    real_open = open

    def guarded(path, *a, **k):                                        # lockdown: debugfs writes fail
        if str(path).endswith("edid_override") and a and "w" in a[0]:
            raise PermissionError(1, "Operation not permitted")
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", guarded)
    r = edid.live_enable()
    assert not r["ok"] and "lockdown" in r["error"] and (sysd / "edid").read_bytes() == raw


def test_live_vrr_not_taken_is_put_back(panel, tmp_path, monkeypatch):
    raw = synthetic_edid()
    sysd, dbg = _fake_kernel(tmp_path, monkeypatch, raw)
    monkeypatch.setattr(edid, "_probe", lambda: True)                  # the driver ignores the override
    r = edid.live_enable()
    assert not r["ok"] and "put back" in r["error"] and (dbg / "edid_override").read_text() == "reset"


@pytest.mark.parametrize("kind,line_has", [
    ("steamos-grub", 'GRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT} firmware_class.path=/etc/firmware drm.edid_firmware=eDP-1:edid/'),
    ("limine", 'KERNEL_CMDLINE[default]+=" firmware_class.path=/etc/firmware drm.edid_firmware=eDP-1:edid/'),
    ("systemd-boot", 'LINUX_OPTIONS="${LINUX_OPTIONS} firmware_class.path=/etc/firmware'),
])
def test_boot_config_on_steamos_and_cachyos_adds_one_marked_line_and_undo_takes_only_it(panel, tmp_path, monkeypatch, kind, line_has):
    """SteamOS (GRUB) and CachyOS (Limine, systemd-boot, GRUB): one marked line that adds ClawDeck's two arguments to the
    bootloader's variable, the EDID into the initramfs through a mkinitcpio drop-in where there is one, then the
    system's own regenerate command. Undo deletes exactly those lines and regenerates."""
    raw = synthetic_edid()
    sysd = tmp_path / "sys"
    sysd.mkdir()
    (sysd / "edid").write_bytes(raw)
    monkeypatch.setattr(edid, "panel_edid_path", lambda: str(sysd / "edid"))
    monkeypatch.setattr(edid, "_cmdline", lambda: "quiet splash")
    monkeypatch.setattr(edid, "EDID_DIR", str(tmp_path / "etc/firmware/edid"))
    monkeypatch.setattr(edid, "MKINIT_DROPIN", str(tmp_path / "etc/mkinitcpio.conf.d/clawdeck-edid.conf"))
    conf = tmp_path / "conf"
    original = "# user's config\nGRUB_TIMEOUT=0\nLINUX_OPTIONS=\"quiet\"\nKERNEL_CMDLINE[default]=\"quiet\"\n"
    conf.write_text(original)
    ran = []
    monkeypatch.setattr(edid, "_run", lambda cmd: ran.append(cmd) or (True, ""))
    lines = {"steamos-grub": 'GRUB_CMDLINE_LINUX_DEFAULT="${{GRUB_CMDLINE_LINUX_DEFAULT}} {args}"',
             "limine": 'KERNEL_CMDLINE[default]+=" {args}"', "systemd-boot": 'LINUX_OPTIONS="${{LINUX_OPTIONS}} {args}"'}
    b = {"kind": kind, "conf": str(conf), "line": lines[kind], "regen": [["regen-" + kind]], "initramfs": kind != "steamos-grub"}
    r = edid.conf_enable(b)
    text = conf.read_text()
    assert r["ok"] and r["restart"] and text.startswith(original) and line_has in text and edid.MARK in text
    assert ran == [["regen-" + kind]] and edid.conf_pending(b) == "on"
    written = list((tmp_path / "etc/firmware/edid").glob("clawdeck-*.bin"))
    assert len(written) == 1 and edid.vrr_range(written[0].read_bytes()) == (30, 120)
    dropin = tmp_path / "etc/mkinitcpio.conf.d/clawdeck-edid.conf"
    assert dropin.exists() == (kind != "steamos-grub")
    if dropin.exists():
        assert f"FILES+=({written[0]})" in dropin.read_text()
    assert edid.conf_enable(b).get("already") and conf.read_text().count(edid.MARK) == 1     # twice: one line
    r = edid.conf_disable(b)
    assert r["ok"] and conf.read_text() == original and not dropin.exists() and ran[-1] == ["regen-" + kind]
    assert not list((tmp_path / "etc/firmware/edid").glob("clawdeck-*.bin")) and edid.conf_pending(b) == "off"


def test_boot_config_failure_puts_the_config_back(panel, tmp_path, monkeypatch):
    raw = synthetic_edid()
    (tmp_path / "edid").write_bytes(raw)
    monkeypatch.setattr(edid, "panel_edid_path", lambda: str(tmp_path / "edid"))
    monkeypatch.setattr(edid, "_cmdline", lambda: "quiet")
    monkeypatch.setattr(edid, "EDID_DIR", str(tmp_path / "fw"))
    conf = tmp_path / "grub"
    conf.write_text('GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n')
    monkeypatch.setattr(edid, "_run", lambda cmd: (False, "grub-mkconfig: error") if cmd == ["grub-mkconfig"] else (True, ""))
    b = {"kind": "steamos-grub", "conf": str(conf), "line": 'GRUB_CMDLINE_LINUX_DEFAULT="${{GRUB_CMDLINE_LINUX_DEFAULT}} {args}"',
         "regen": [["grub-mkconfig"]], "initramfs": False}
    r = edid.conf_enable(b)
    assert not r["ok"] and "grub-mkconfig" in r["error"] and conf.read_text() == 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n'


def test_boot_backend_is_chosen_by_system(monkeypatch):
    files = set()
    progs = set()
    monkeypatch.setattr(edid.os.path, "isfile", lambda p: p in files)
    monkeypatch.setattr(edid.shutil, "which", lambda n: f"/usr/bin/{n}" if n in progs else None)
    from clawdeck import distro
    monkeypatch.setattr(distro, "info", lambda: {"kind": "steamos", "name": "SteamOS", "atomic": False})
    files |= {"/etc/default/grub", "/efi/EFI/steamos/grub.cfg"}
    progs |= {"grub-mkconfig"}
    b = edid.boot_backend()
    assert b["kind"] == "steamos-grub" and b["regen"] == [["grub-mkconfig", "-o", "/efi/EFI/steamos/grub.cfg"]] and not b["initramfs"]
    progs.add("steamos-update-grub")
    assert edid.boot_backend()["regen"] == [["steamos-update-grub"]]
    monkeypatch.setattr(distro, "info", lambda: {"kind": "cachyos", "name": "CachyOS", "atomic": False})
    files, progs = {"/etc/default/limine"}, {"limine-mkinitcpio", "mkinitcpio"}
    monkeypatch.setattr(edid.os.path, "isfile", lambda p: p in files)
    monkeypatch.setattr(edid.shutil, "which", lambda n: f"/usr/bin/{n}" if n in progs else None)
    b = edid.boot_backend()
    assert b["kind"] == "limine" and b["regen"] == [["limine-mkinitcpio"]] and b["initramfs"]
    files.clear(); files.add("/etc/sdboot-manage.conf"); progs.clear(); progs |= {"sdboot-manage", "mkinitcpio"}
    assert edid.boot_backend()["regen"] == [["mkinitcpio", "-P"], ["sdboot-manage", "gen"]]
    files.clear(); progs.clear()
    assert edid.boot_backend() is None                        # nothing known: the live method
    progs.add("rpm-ostree")
    assert edid.boot_backend() == {"kind": "rpm-ostree"}
