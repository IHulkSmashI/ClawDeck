"""Release downloads, the system-change check, and the ClawTweaks
extras (CPU energy mode, TDP boost)."""
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
    sysfs._originals.clear()
    yield
    sysfs._originals.clear()


# ---- downloads: GitHub's asset digest -----------------------------------------------------------------------------
def test_asset_digest_checked_when_published(tmp_path, monkeypatch):
    import hashlib, zipfile
    good = tmp_path / "pkg.zip"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("xess-xmx-linux-1/install.sh", "echo")
    digest = hashlib.sha256(good.read_bytes()).hexdigest()
    rel = {"tag_name": "1", "assets": [{"name": "xess-xmx-linux-1.zip", "browser_download_url": "u", "digest": f"sha256:{digest}"}]}
    monkeypatch.setattr(downloads, "release", lambda repo, tag: rel)

    def dl(url, dest, timeout=60):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "wb").write(good.read_bytes())
        return dest
    monkeypatch.setattr(downloads, "download", dl)
    got = downloads.fetch({"repo": "a/b", "tag": "1", "asset_glob": "xess-xmx-linux-*.zip"}, str(tmp_path / "w"))
    assert got["tag"] == "1" and os.path.exists(os.path.join(got["dir"], "xess-xmx-linux-1", "install.sh"))
    rel["assets"][0]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="sha256"):
        downloads.fetch({"repo": "a/b", "tag": "1", "asset_glob": "xess-xmx-linux-*.zip"}, str(tmp_path / "w2"))
    assert downloads.asset_digest({"digest": "md5:abc"}) is None and downloads.asset_digest({}) is None


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
