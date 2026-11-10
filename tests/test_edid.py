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


def _boot_env(tmp_path, monkeypatch, cmdline="quiet splash"):
    raw = synthetic_edid()
    (tmp_path / "edid").write_bytes(raw)
    monkeypatch.setattr(edid, "panel_edid_path", lambda: str(tmp_path / "edid"))
    monkeypatch.setattr(edid, "_cmdline", lambda: cmdline)
    monkeypatch.setattr(edid, "EDID_DIR", str(tmp_path / "etc/firmware/edid"))
    monkeypatch.setattr(edid, "MKINIT_DROPIN", str(tmp_path / "etc/mkinitcpio.conf.d/clawdeck-edid.conf"))
    ran = []
    monkeypatch.setattr(edid, "_regen", lambda cmd: ran.append(cmd) or (True, ""))
    return raw, ran


LINES = {"limine": 'KERNEL_CMDLINE[default]+=" {args}"', "systemd-boot": 'LINUX_OPTIONS="${{LINUX_OPTIONS}} {args}"',
         "grub": 'GRUB_CMDLINE_LINUX_DEFAULT="${{GRUB_CMDLINE_LINUX_DEFAULT}} {args}"'}


@pytest.mark.parametrize("kind", ["limine", "systemd-boot", "grub"])
def test_cachyos_boot_config_one_marked_line_undo_restores_the_file_exactly(panel, tmp_path, monkeypatch, kind):
    """CachyOS: ClawDeck's two arguments on ONE line carrying its marker, the EDID into the initramfs (mkinitcpio
    drop-in), the system's regenerate command. Undo leaves the file byte for byte as it was."""
    raw, ran = _boot_env(tmp_path, monkeypatch)
    conf = tmp_path / "conf"
    original = '# user config\nKERNEL_CMDLINE[default]="root=UUID=x rw quiet"\nOVERWRITE_EXISTING="yes"\nGRUB_TIMEOUT=0\n'
    conf.write_text(original)
    conf.chmod(0o640)
    b = {"kind": kind, "conf": str(conf), "line": LINES[kind], "regen": [["regen"]], "initramfs": True,
         "marker_above": kind == "limine"}
    r = edid.conf_enable(b)
    text = conf.read_text()
    added = text[len(original):].strip()
    assert r["ok"] and r["restart"] and text.startswith(original)
    if kind == "limine":                       # limine-entry-tool reads lines, not shell: marker on its own line (#155)
        mark, line = added.split("\n")
        assert mark == edid.MARK and line == LINES[kind].format(args="firmware_class.path=/etc/firmware drm.edid_firmware=eDP-1:edid/" + line.split("edid/")[1].rstrip('"'))
        assert "#" not in line
    else:
        assert "\n" not in added and added.endswith(edid.MARK)
    assert "firmware_class.path=/etc/firmware drm.edid_firmware=eDP-1:edid/clawdeck-" in added
    assert ran == [["regen"]] and edid.conf_pending(b) == "on" and oct(conf.stat().st_mode & 0o777) == "0o640"
    bins = list((tmp_path / "etc/firmware/edid").glob("clawdeck-*.bin"))
    assert len(bins) == 1 and edid.vrr_range(bins[0].read_bytes()) == (30, 120)
    dropin = tmp_path / "etc/mkinitcpio.conf.d/clawdeck-edid.conf"
    assert f"FILES+=({bins[0]})" in dropin.read_text()
    assert edid.conf_enable(b).get("already") and conf.read_text().count(edid.MARK) == 1
    r = edid.conf_disable(b)
    assert r["ok"] and conf.read_text() == original and not dropin.exists() and edid.conf_pending(b) == "off"
    assert not list((tmp_path / "etc/firmware/edid").glob("clawdeck-*.bin"))


def test_undo_never_takes_a_user_line_after_a_stray_marker(panel, tmp_path, monkeypatch):
    lines = ["GRUB_TIMEOUT=0", "# ClawDeck VRR range fix", "GRUB_CMDLINE_LINUX_DEFAULT=\"mine\"", "X=1"]
    assert edid._strip_ours(lines) == ["GRUB_TIMEOUT=0", "GRUB_CMDLINE_LINUX_DEFAULT=\"mine\"", "X=1"]
    old_form = ["A=1", "# ClawDeck VRR range fix", 'G="${G} firmware_class.path=/etc/firmware drm.edid_firmware=eDP-1:edid/clawdeck-x.bin"']
    assert edid._strip_ours(old_form) == ["A=1"]


def test_boot_config_refuses_someone_elses_firmware_path_and_survives_failures(panel, tmp_path, monkeypatch):
    raw, ran = _boot_env(tmp_path, monkeypatch, cmdline="quiet firmware_class.path=/opt/fw")
    conf = tmp_path / "conf"
    conf.write_text('GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n')
    b = {"kind": "grub", "conf": str(conf), "line": LINES["grub"], "regen": [["grub-mkconfig"]], "initramfs": False}
    r = edid.conf_enable(b)
    assert not r["ok"] and "/opt/fw" in r["error"] and not ran
    monkeypatch.setattr(edid, "_cmdline", lambda: "quiet")
    monkeypatch.setattr(edid, "_regen", lambda cmd: (False, "error"))          # regenerate fails: config put back
    r = edid.conf_enable(b)
    assert not r["ok"] and conf.read_text() == 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n'
    monkeypatch.setattr(edid, "_regen", lambda cmd: (True, ""))
    monkeypatch.setattr(edid, "_write_conf", lambda p, l: (_ for _ in ()).throw(PermissionError(13, "Read-only file system")))
    r = edid.conf_enable(b)
    assert not r["ok"] and "Read-only" in r["error"]


def test_switching_from_live_to_boot_takes_the_live_one_off_first(panel, tmp_path, monkeypatch):
    raw, ran = _boot_env(tmp_path, monkeypatch)
    state = {"live": True}
    monkeypatch.setattr(edid, "live_active", lambda: state["live"])
    monkeypatch.setattr(edid, "live_disable", lambda: state.update(live=False) or {"ok": True})
    conf = tmp_path / "conf"
    conf.write_text('LINUX_OPTIONS="quiet"\n')
    b = {"kind": "systemd-boot", "conf": str(conf), "line": LINES["systemd-boot"], "regen": [["g"]], "initramfs": False}
    assert edid.conf_enable(b)["ok"] and not state["live"]


def test_boot_backend_only_where_it_is_safe(monkeypatch, tmp_path):
    """SteamOS: /etc/default/grub + Holo's update-grub, only when the GPU driver isn't in the initramfs. CachyOS Limine only when the file sets KERNEL_CMDLINE[default] itself
    (else limine-entry-tool would write entries without root=); systemd-boot only with OVERWRITE_EXISTING=yes; GRUB
    without grub.d drop-ins. Bazzite: rpm-ostree."""
    from clawdeck import distro
    files, progs, texts, globs = set(), set(), {}, {}
    monkeypatch.setattr(edid.os.path, "isfile", lambda p: p in files)
    monkeypatch.setattr(edid.shutil, "which", lambda n: f"/usr/bin/{n}" if n in progs else None)
    monkeypatch.setattr(edid, "_conf_lines", lambda p: texts.get(p, "").splitlines())
    monkeypatch.setattr(edid.glob, "glob", lambda pat: globs.get(pat, []))
    monkeypatch.setattr(distro, "info", lambda: {"kind": "steamos", "name": "SteamOS", "atomic": False, "like": "arch"})
    files |= {"/etc/default/grub", "/boot/grub/grub.cfg"}
    progs |= {"grub-mkconfig"}
    monkeypatch.setattr(edid.os.path, "isdir", lambda p: p == "/etc/atomic-update.conf.d")
    monkeypatch.setattr(edid, "_driver_in_initramfs", lambda: None)
    assert edid.boot_backend() is None                                         # SteamOS without Holo's update-grub
    progs.add("update-grub")
    b = edid.boot_backend()
    assert b["kind"] == "steamos-grub" and b["regen"] == [["update-grub"]] and not b["initramfs"]
    assert b["keep"] == "/etc/atomic-update.conf.d/clawdeck-vrr.conf"
    monkeypatch.setattr(edid, "_driver_in_initramfs", lambda: True)
    assert edid.boot_backend() is None                                         # driver starts before /etc: no
    progs.discard("update-grub")
    monkeypatch.setattr(distro, "info", lambda: {"kind": "cachyos", "name": "CachyOS", "atomic": False, "like": "arch"})
    assert edid.boot_backend()["kind"] == "grub"
    globs["/etc/default/grub.d/*"] = ["/etc/default/grub.d/x.cfg"]
    assert edid.boot_backend() is None
    files, progs = {"/etc/default/limine"}, {"limine-update", "limine-mkinitcpio", "mkinitcpio"}
    monkeypatch.setattr(edid.os.path, "isfile", lambda p: p in files)
    monkeypatch.setattr(edid.shutil, "which", lambda n: f"/usr/bin/{n}" if n in progs else None)
    texts["/etc/default/limine"] = 'TARGET_OS_NAME="CachyOS"\n#KERNEL_CMDLINE[default]="quiet"\n'
    assert edid.boot_backend() is None                                         # no KERNEL_CMDLINE of its own
    texts["/etc/default/limine"] += 'KERNEL_CMDLINE[default]+="quiet nowatchdog"\n'
    b = edid.boot_backend()
    assert b["kind"] == "limine" and b["regen"] == [["limine-update"]] and b["initramfs"] and b["marker_above"]
    globs["/etc/limine-entry-tool.d/*.conf"] = ["/etc/limine-entry-tool.d/a.conf"]
    texts["/etc/limine-entry-tool.d/a.conf"] = 'KERNEL_CMDLINE[default]="root=x"\n'
    assert edid.boot_backend() is None                                         # a drop-in would win
    files, progs = {"/etc/sdboot-manage.conf"}, {"sdboot-manage", "mkinitcpio"}
    monkeypatch.setattr(edid.os.path, "isfile", lambda p: p in files)
    monkeypatch.setattr(edid.shutil, "which", lambda n: f"/usr/bin/{n}" if n in progs else None)
    texts["/etc/sdboot-manage.conf"] = 'LINUX_OPTIONS="quiet"\n#OVERWRITE_EXISTING="yes"\n'
    assert edid.boot_backend() is None                                         # gen would leave entries alone
    texts["/etc/sdboot-manage.conf"] += 'OVERWRITE_EXISTING="yes"\n'
    assert edid.boot_backend()["regen"] == [["mkinitcpio", "-P"], ["sdboot-manage", "gen"]]
    progs.add("rpm-ostree")
    assert edid.boot_backend() == {"kind": "rpm-ostree"}


def test_beta4_steamos_grub_line_is_taken_out_once(panel, tmp_path, monkeypatch):
    from clawdeck import distro
    raw, ran = _boot_env(tmp_path, monkeypatch)
    grub = tmp_path / "grub"
    user = 'GRUB_TIMEOUT=0\nGRUB_CMDLINE_LINUX_DEFAULT="quiet"\n'
    grub.write_text(user + edid.MARK_BETA4 + '\nGRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT} firmware_class.path=/etc/firmware drm.edid_firmware=eDP-1:edid/clawdeck-CSW0801-30-120.bin"\n')
    real_lines = edid._conf_lines
    monkeypatch.setattr(edid, "_conf_lines", lambda p: real_lines(str(grub) if p == "/etc/default/grub" else p))
    real_write = edid._write_conf
    monkeypatch.setattr(edid, "_write_conf", lambda p, l: real_write(str(grub) if p == "/etc/default/grub" else p, l))
    monkeypatch.setattr(edid.shutil, "which", lambda n: "/usr/bin/steamos-update-grub" if n == "steamos-update-grub" else None)
    monkeypatch.setattr(distro, "info", lambda: {"kind": "cachyos", "name": "CachyOS", "atomic": False, "like": "arch"})
    assert edid.steamos_beta4_cleanup() is None and not ran                  # not SteamOS: nothing
    monkeypatch.setattr(distro, "info", lambda: {"kind": "steamos", "name": "SteamOS", "atomic": False, "like": "arch"})
    r = edid.steamos_beta4_cleanup()
    assert r["ok"] and grub.read_text() == user and ran == [["steamos-update-grub"]]
    assert edid.steamos_beta4_cleanup() is None and len(ran) == 1             # once


def test_beta4_cachyos_line_is_recognised_and_removed(panel, tmp_path, monkeypatch):
    raw, ran = _boot_env(tmp_path, monkeypatch)
    conf = tmp_path / "conf"
    user = 'KERNEL_CMDLINE[default]+="quiet"\n'
    conf.write_text(user + edid.MARK_BETA4 + '\nKERNEL_CMDLINE[default]+=" firmware_class.path=/etc/firmware drm.edid_firmware=eDP-1:edid/clawdeck-x.bin"\n')
    b = {"kind": "limine", "conf": str(conf), "line": LINES["limine"], "regen": [["limine-update"]], "initramfs": False}
    assert edid.conf_pending(b) == "on"
    assert edid.conf_disable(b)["ok"] and conf.read_text() == user and edid.conf_pending(b) == "off"


def test_live_method_not_offered_on_intels_built_in_screen(panel, tmp_path, monkeypatch):
    """i915 / xe keep the eDP EDID they read at start, so the live override never reaches the panel (SteamOS 3.9.5 on a
    Claw 8 EX: 'the driver didn't take the corrected range'). Not offered there."""
    raw = synthetic_edid()
    sysd, dbg = _fake_kernel(tmp_path, monkeypatch, raw)
    card = tmp_path / "sys/class/drm/card0"
    (card / "device").mkdir(parents=True)
    drv = tmp_path / "drivers/xe"
    drv.mkdir(parents=True)
    (card / "device" / "driver").symlink_to(drv)
    assert edid._gpu_driver() == "xe" and edid.live_dir() is None and not edid.status()["live_possible"]


def test_no_way_to_apply_says_why_instead_of_offering_the_button(panel, tmp_path, monkeypatch):
    raw = synthetic_edid()
    _fake_kernel(tmp_path, monkeypatch, raw)
    monkeypatch.setattr(edid, "live_dir", lambda: None)
    monkeypatch.setattr(edid, "boot_backend", lambda for_enable=True: None)
    monkeypatch.setattr(edid.shutil, "which", lambda n: None)
    st = edid.status()
    assert not st["supported"] and "boot setting" in st["reason"]


def _steamos_env(tmp_path, monkeypatch):
    from clawdeck import distro
    raw, ran = _boot_env(tmp_path, monkeypatch)
    grub = tmp_path / "etc/default/grub"
    grub.parent.mkdir(parents=True)
    valve = 'GRUB_DEFAULT=saved\nGRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 splash quiet"\n'
    grub.write_text(valve)
    keep = tmp_path / "etc/atomic-update.conf.d/clawdeck-vrr.conf"
    keep.parent.mkdir(parents=True)
    monkeypatch.setattr(edid, "KEEP_CONF", str(keep))
    monkeypatch.setattr(distro, "info", lambda: {"kind": "steamos", "name": "SteamOS", "atomic": False, "like": "arch"})
    b = {"kind": "steamos-grub", "conf": str(grub), "line": edid.GRUB_LINE, "regen": [["update-grub"]],
         "initramfs": False, "keep": str(keep)}
    monkeypatch.setattr(edid, "boot_backend", lambda for_enable=True: b)
    return b, grub, keep, valve, ran


def test_steamos_boot_line_keep_list_and_exact_undo(panel, tmp_path, monkeypatch):
    """SteamOS: one marked line in /etc/default/grub, Holo's update-grub, and a keep-list drop-in naming ONLY
    ClawDeck's EDID file (never /etc/default/grub, which would shadow Valve's changes). No initramfs rebuild. Undo
    leaves Valve's file byte for byte and removes the drop-in."""
    b, grub, keep, valve, ran = _steamos_env(tmp_path, monkeypatch)
    r = edid.conf_enable(b)
    assert r["ok"] and r["restart"] and ran == [["update-grub"]]
    text = grub.read_text()
    assert text.startswith(valve) and text.rstrip().endswith(edid.MARK) and "drm.edid_firmware=eDP-1:edid/clawdeck-" in text
    entries = [ln for ln in keep.read_text().splitlines() if ln and not ln.startswith("#")]
    assert entries == [f"{edid.EDID_DIR}/clawdeck-*.bin"]
    assert not (tmp_path / "etc/mkinitcpio.conf.d/clawdeck-edid.conf").exists()
    assert edid.conf_disable(b)["ok"] and grub.read_text() == valve and not keep.exists()
    assert not list((tmp_path / "etc/firmware/edid").glob("clawdeck-*.bin"))


def test_steamos_update_brings_back_valves_grub_file_and_clawdeck_adds_its_line_again(panel, tmp_path, monkeypatch):
    b, grub, keep, valve, ran = _steamos_env(tmp_path, monkeypatch)
    assert edid.steamos_after_update() is None and not ran                   # fix never turned on: nothing
    assert edid.conf_enable(b)["ok"]
    assert edid.steamos_after_update() is None and len(ran) == 1             # line still there: nothing
    new_valve = 'GRUB_DEFAULT=saved\nGRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 splash quiet newparam=1"\n'
    grub.write_text(new_valve)                                              # the update: Valve's new file, EDID kept
    r = edid.steamos_after_update()
    assert r["ok"] and len(ran) == 2
    text = grub.read_text()
    assert text.startswith(new_valve) and "newparam=1" in text and text.rstrip().endswith(edid.MARK)
    grub.write_text(new_valve)
    for f in (tmp_path / "etc/firmware/edid").glob("clawdeck-*.bin"):
        f.unlink()                                                          # drop-in alone: nothing to restore
    r = edid.steamos_after_update()                                         # drop-in alone: cleaned up, not kept forever
    assert not keep.exists() and grub.read_text() == new_valve


def test_steamos_failed_re_add_after_update_keeps_the_fix_for_next_time(panel, tmp_path, monkeypatch):
    b, grub, keep, valve, ran = _steamos_env(tmp_path, monkeypatch)
    assert edid.conf_enable(b)["ok"]
    grub.write_text(valve)                                                  # the update
    monkeypatch.setattr(edid, "_regen", lambda cmd: (False, "grub-probe: error"))
    r = edid.steamos_after_update()
    assert not r["ok"] and grub.read_text() == valve and keep.exists()
    assert list((tmp_path / "etc/firmware/edid").glob("clawdeck-*.bin"))       # EDID kept: next start tries again
    assert "after the SteamOS update" in edid.status()["reason"]
    monkeypatch.setattr(edid, "_regen", lambda cmd: ran.append(cmd) or (True, ""))
    assert edid.steamos_after_update()["ok"] and edid.status()["reason"] is None


def test_steamos_update_that_makes_the_fix_impossible_takes_its_files_out(panel, tmp_path, monkeypatch):
    b, grub, keep, valve, ran = _steamos_env(tmp_path, monkeypatch)
    assert edid.conf_enable(b)["ok"]
    grub.write_text(valve)
    monkeypatch.setattr(edid, "boot_backend", lambda for_enable=True: None if for_enable else b)   # driver in initramfs now
    edid.steamos_after_update()
    assert not keep.exists() and grub.read_text() == valve
    assert not list((tmp_path / "etc/firmware/edid").glob("clawdeck-*.bin"))


def test_steamos_boot_menu_without_our_argument_is_an_error_not_a_silent_no(panel, tmp_path, monkeypatch):
    b, grub, keep, valve, ran = _steamos_env(tmp_path, monkeypatch)
    cfg = tmp_path / "grub.cfg"
    cfg.write_text("linux /boot/vmlinuz-linux-neptune-72 quiet\n")              # update-grub left our line out
    monkeypatch.setattr(edid, "STEAMOS_GRUB_CFG", str(cfg))
    r = edid.conf_enable(b)
    assert not r["ok"] and "boot menu" in r["error"] and grub.read_text() == valve and not keep.exists()
    monkeypatch.setattr(edid, "_regen", lambda cmd: (cfg.write_text(
        f"linux /boot/vmlinuz quiet firmware_class.path=/etc/firmware {edid.OURS}CSW0801-30-120.bin\n"), (True, ""))[1])
    assert edid.conf_enable(b)["ok"]


def test_started_with_the_fix_but_the_driver_kept_48_says_so(panel, tmp_path, monkeypatch):
    b, grub, keep, valve, ran = _steamos_env(tmp_path, monkeypatch)
    assert edid.conf_enable(b)["ok"]
    monkeypatch.setattr(edid, "_cmdline", lambda: "quiet firmware_class.path=/etc/firmware drm.edid_firmware=eDP-1:edid/clawdeck-CSW0801-30-120.bin")
    monkeypatch.setattr(edid, "kernel_edid_lines", lambda limit=12: [])
    st = edid.status()                                                      # sysfs EDID still the factory 48-120
    assert not st["wide"] and st["not_taken"] and "48-120" in st["reason"] and st["pending"] == "on"
    # the kernel's own reason, when its log has one (#155)
    said = 'xe 0000:00:02.0: [drm] *ERROR* Requesting EDID firmware "edid/clawdeck-csw0801-30-120.bin" failed (err=-2)'
    monkeypatch.setattr(edid, "kernel_edid_lines", lambda limit=12: ["Command line: quiet", said])
    assert edid.status()["reason"].endswith(f"The kernel says: {said}")
    r = edid.boot_report()
    assert r["status"]["not_taken"] and r["cmdline_edid"].startswith("eDP-1:edid/clawdeck-") and r["kernel_log"][1] == said
    assert r["backend"]["kind"] == "steamos-grub" and r["conf_line"] and isinstance(r["initramfs_has_edid"], dict)


def test_kernel_edid_lines_reads_only_edid_lines(monkeypatch):
    class R:
        returncode, stdout = 0, "xe: probing\n[drm] Got external EDID base block and 1 extension from \"edid/x.bin\"\nusb 1-1: new\n"
    monkeypatch.setattr(edid.subprocess, "run", lambda *a, **k: R())
    assert edid.kernel_edid_lines() == ['[drm] Got external EDID base block and 1 extension from "edid/x.bin"']


def test_interrupted_remove_never_brings_the_fix_back(panel, tmp_path, monkeypatch):
    b, grub, keep, valve, ran = _steamos_env(tmp_path, monkeypatch)
    assert edid.conf_enable(b)["ok"]
    real = edid._write_conf
    monkeypatch.setattr(edid, "_write_conf", lambda p, l: (_ for _ in ()).throw(OSError(5, "I/O error")))
    assert not edid.conf_disable(b)["ok"]
    assert not keep.exists()                                                # keep-list entry went first
    monkeypatch.setattr(edid, "_write_conf", real)
    assert edid.steamos_after_update() is None                              # no drop-in: nothing re-added


def test_beta4_lone_marker_line_goes_too(panel, tmp_path, monkeypatch):
    from clawdeck import distro
    raw, ran = _boot_env(tmp_path, monkeypatch)
    grub = tmp_path / "grub"
    user = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n'
    grub.write_text(user + edid.MARK_BETA4 + "\n")
    real_lines, real_write = edid._conf_lines, edid._write_conf
    monkeypatch.setattr(edid, "_conf_lines", lambda p: real_lines(str(grub) if p == "/etc/default/grub" else p))
    monkeypatch.setattr(edid, "_write_conf", lambda p, l: real_write(str(grub) if p == "/etc/default/grub" else p, l))
    monkeypatch.setattr(edid.shutil, "which", lambda n: "/usr/bin/update-grub" if n == "update-grub" else None)
    monkeypatch.setattr(distro, "info", lambda: {"kind": "steamos", "name": "SteamOS", "atomic": False, "like": "arch"})
    assert edid.steamos_beta4_cleanup()["ok"] and grub.read_text() == user and ran == [["update-grub"]]
    assert edid.steamos_beta4_cleanup() is None


def test_driver_in_the_initramfs_is_read_from_the_running_image(tmp_path, monkeypatch):
    img = tmp_path / "initramfs-linux-neptune-72.img"
    img.write_bytes(b"x")
    monkeypatch.setattr(edid, "_gpu_driver", lambda: "xe")
    monkeypatch.setattr(edid, "_cmdline", lambda: "BOOT_IMAGE=/boot/vmlinuz-linux-neptune-72 quiet")
    monkeypatch.setattr(edid.shutil, "which", lambda n: "/usr/bin/lsinitcpio" if n == "lsinitcpio" else None)
    real_stat = edid.os.stat
    monkeypatch.setattr(edid.os, "stat", lambda p, *a, **k: real_stat(str(img) if p == "/boot/initramfs-linux-neptune-72.img" else p))
    listing = {"out": "usr/lib/modules/7.2/kernel/drivers/nvme/host/nvme.ko.zst\n"}

    class R:
        returncode = 0
        @property
        def stdout(self):
            return listing["out"]
    monkeypatch.setattr(edid.subprocess, "run", lambda *a, **k: R())
    edid._initramfs_cache.clear()
    assert edid._driver_in_initramfs() is False                              # Valve's hooks: no GPU driver
    edid._initramfs_cache.clear()
    listing["out"] += "usr/lib/modules/7.2/kernel/drivers/gpu/drm/xe/xe.ko.zst\n"
    assert edid._driver_in_initramfs() is True
    monkeypatch.setattr(edid, "_gpu_driver", lambda: "amdgpu")
    assert edid._driver_in_initramfs() is None                               # not Intel: not this check's business


def test_limine_line_from_beta14_is_repaired_once(panel, tmp_path, monkeypatch):
    """#155: beta 14 and earlier wrote the marker after the Limine setting; limine-entry-tool put it on the kernel
    command line. At start ClawDeck rewrites it with the marker above, regenerates once, and leaves good files alone."""
    raw, ran = _boot_env(tmp_path, monkeypatch)
    conf = tmp_path / "limine"
    user = 'KERNEL_CMDLINE[default]="quiet splash rw"\n'
    args = "firmware_class.path=/etc/firmware drm.edid_firmware=eDP-1:edid/clawdeck-csw0801-30-120.bin"
    conf.write_text(user + f'KERNEL_CMDLINE[default]+=" {args}"  {edid.MARK}\n')
    b = {"kind": "limine", "conf": str(conf), "line": LINES["limine"], "regen": [["limine-update"]], "initramfs": True,
         "marker_above": True}
    monkeypatch.setattr(edid, "boot_backend", lambda for_enable=True: b)
    r = edid.limine_marker_repair()
    assert r == {"ok": True, "restart": True} and ran == [["limine-update"]]
    assert conf.read_text() == user + f'{edid.MARK}\nKERNEL_CMDLINE[default]+=" {args}"\n'
    assert edid.limine_marker_repair() is None and len(ran) == 1          # repaired: nothing more
    assert edid.conf_pending(b) == "on"
    assert edid.conf_disable(b)["ok"] and conf.read_text() == user        # Remove takes both lines out
