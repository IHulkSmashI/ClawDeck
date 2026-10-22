"""Release downloads, the system-change check, and the ClawTweaks
extras (CPU energy mode, TDP boost)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from clawdeck import changes, downloads, hhd, hw, power, sysfs  # noqa: E402


def _w(path, value="x"):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"{value}\n")


@pytest.fixture(autouse=True)
def _clean():
    sysfs.set_dry_run(False)
    sysfs._originals.clear()
    yield
    sysfs._originals.clear()


# ---- downloads: prereleases, published checksums ---------------------------------------------------------------
def test_newest_with_asset_takes_prereleases(monkeypatch):
    rels = [{"tag_name": "draft", "draft": True, "assets": [{"name": "Moonlight-x-x86_64.AppImage"}]},
            {"tag_name": "v6.2.0-daily.191", "prerelease": True, "assets": [{"name": "SHA256SUMS"}, {"name": "Moonlight-6.2.0-daily.191-x86_64.AppImage"}]}]
    monkeypatch.setattr(downloads, "_get", lambda url, timeout=20.0: json.dumps(rels).encode())
    assert downloads.newest_with_asset("Nonary/moonlight-qt", "Moonlight-*-x86_64.AppImage")["tag_name"] == "v6.2.0-daily.191"
    assert downloads.newest_with_asset("Nonary/moonlight-qt", "Moonlight-*-x86_64.AppImage", prereleases=False) is None


def test_expected_sha256_reads_sha256sums(monkeypatch):
    rel = {"assets": [{"name": "SHA256SUMS", "browser_download_url": "s"}, {"name": "Moonlight-1-x86_64.AppImage"}]}
    monkeypatch.setattr(downloads, "_get", lambda url, timeout=20.0: b"abc123  Moonlight-1-x86_64.AppImage\nfff  other.flatpak\n")
    assert downloads.expected_sha256(rel, "Moonlight-1-x86_64.AppImage") == "abc123"
    assert downloads.expected_sha256({"assets": []}, "x") is None


# ---- system changes ------------------------------------------------------------------------------------------------
def test_changes_first_run_then_diff(tmp_path):
    path = str(tmp_path / "snap.json")
    a = {"kernel": "6.19.4", "mesa": "26.2.1", "os": "Bazzite 44.1", "controller": "inputplumber", "xess": None, "decky": "3.1"}
    assert changes.check(path, a) == {"first_run": True, "changed": []}
    assert changes.check(path, a)["changed"] == []
    b = {**a, "kernel": "6.19.6", "mesa": "26.2.3"}
    got = changes.check(path, b)["changed"]
    assert [(c["what"], c["from"], c["to"]) for c in got] == [("Kernel", "6.19.4", "6.19.6"), ("Mesa", "26.2.1", "26.2.3")]


# ---- ClawTweaks extras: CPU energy mode, TDP boost -----------------------------------------------------------------
def test_epp_written_to_every_cpu_and_profiles_set_it(tmp_path, monkeypatch):
    monkeypatch.setattr(hhd, "available", lambda: False)
    epp = []
    for i in range(3):
        f = tmp_path / f"cpu{i}/cpufreq/energy_performance_preference"
        _w(f, "balance_performance")
        epp.append(str(f))
    h = hw.Hardware(product="MS-1T91", model=hw.MODELS["MS-1T91"], known=True, kernel="t", cpu_model="t", gpu_driver="xe",
                    paths=hw.Paths(cpu_epp=epp))
    pw = power.Power(h)
    assert pw.set_epp("power") and all(sysfs.read(p) == "power" for p in epp)
    with pytest.raises(ValueError):
        pw.set_epp("turbo")
    r = pw.apply_profile("dock")
    assert r["applied"]["epp"] == "performance" and pw.epp() == "performance"
    pw.revert_all()
    assert all(sysfs.read(p) == "balance_performance" for p in epp)


def test_tdp_boost_off_keeps_pl2_one_watt_over(tmp_path, monkeypatch):
    monkeypatch.setattr(hhd, "available", lambda: False)
    base = tmp_path / "fw"
    for a, v in (("ppt_pl1_spl", 20), ("ppt_pl2_sppt", 30), ("ppt_pl3_fppt", 40)):
        _w(base / a / "current_value", v)
    p = hw.Paths(msi_pl1=str(base / "ppt_pl1_spl/current_value"), msi_pl2=str(base / "ppt_pl2_sppt/current_value"),
                 msi_pl3=str(base / "ppt_pl3_fppt/current_value"))
    pw = power.Power(hw.Hardware(product="MS-1T91", model=hw.MODELS["MS-1T91"], known=True, kernel="t", cpu_model="t",
                                 gpu_driver="xe", paths=p))
    pw.boost = False
    pw.set_tdp(20, plugged=False)
    assert [sysfs.read_int(x) for x in (p.msi_pl1, p.msi_pl2, p.msi_pl3)] == [20, 21, 21]
    pw.boost = True
    pw.set_tdp(20, plugged=False)
    assert sysfs.read_int(p.msi_pl2) == 26


def test_tar_with_a_link_outside_is_refused(tmp_path):
    import io, tarfile as tf
    from clawdeck import downloads as dl
    arc = tmp_path / "evil.tar.gz"
    with tf.open(arc, "w:gz") as t:
        info = tf.TarInfo("lib/shadow"); info.type = tf.LNKTYPE; info.linkname = "/etc/shadow"
        t.addfile(info)
    with pytest.raises(ValueError):
        dl.extract(str(arc), str(tmp_path / "out"))
    ok = tmp_path / "ok.tar.gz"
    with tf.open(ok, "w:gz") as t:
        data = b"layer"
        f = tf.TarInfo("lib/liblsfg.so.1"); f.size = len(data); t.addfile(f, io.BytesIO(data))
        l = tf.TarInfo("lib/liblsfg.so"); l.type = tf.SYMTYPE; l.linkname = "liblsfg.so.1"; t.addfile(l)
    dl.extract(str(ok), str(tmp_path / "ok"))
    assert (tmp_path / "ok/lib/liblsfg.so.1").read_bytes() == b"layer"


def test_write_new_does_not_follow_a_planted_link(tmp_path):
    from clawdeck import userexec
    target = tmp_path / "precious"
    target.write_text("keep")
    link = tmp_path / "clawdeck-launch.sh"
    link.symlink_to(target)
    userexec.write_new(str(link), "#!/bin/bash\n", 0o755)
    assert target.read_text() == "keep" and not link.is_symlink() and link.read_text() == "#!/bin/bash\n"
