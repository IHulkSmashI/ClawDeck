"""Streaming (Moonlight VRR), checksum-verified updates with rollback, the system-change check, and the ClawTweaks
extras (CPU energy mode, TDP boost)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from clawdeck import changes, downloads, hhd, hw, power, streaming, sysfs, updates  # noqa: E402


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


# ---- Moonlight VRR -------------------------------------------------------------------------------------------------
def test_moonlight_install_unpacks_and_keeps_one_launcher(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()

    def fake_run(cmd, h, timeout=600, cwd=None, env=None):      # what `Moonlight.AppImage --appimage-extract` leaves
        _w(os.path.join(cwd, "squashfs-root", "AppRun"), "#!/bin/sh")
        return {"rc": 0, "out": ""}
    monkeypatch.setattr(streaming.userexec, "run", fake_run)
    staged = tmp_path / "assets" / "moonlight"
    _w(staged / "Moonlight.AppImage", "ELF")
    r = streaming.install(str(home), str(staged), "v6.2.0-daily.190")
    assert r["previous"] is None and streaming.current(str(home)) == "v6.2.0-daily.190"
    launcher = open(streaming.launcher(str(home))).read()
    assert 'cat "$D/CURRENT"' in launcher and os.access(streaming.launcher(str(home)), os.X_OK)
    streaming.install(str(home), str(staged), "v6.2.0-daily.191")
    streaming.install(str(home), str(staged), "v6.2.0-daily.192")
    kept = sorted(d for d in os.listdir(streaming.root(str(home))) if d.startswith("v"))
    assert kept == ["v6.2.0-daily.191", "v6.2.0-daily.192"]          # current + previous only
    with pytest.raises(ValueError):
        streaming.install(str(home), str(staged), "../evil")


def test_stream_args_validates_host():
    assert streaming.stream_args("192.168.1.20", "Steam Big Picture") == 'stream 192.168.1.20 "Steam Big Picture"'
    assert streaming.stream_args("gaming-pc.local", "") == 'stream gaming-pc.local "Desktop"'
    with pytest.raises(ValueError):
        streaming.stream_args("pc; rm -rf ~", "Desktop")


# ---- updates: checksum, prerelease, rollback -----------------------------------------------------------------------
def _fake_dl(monkeypatch, content):
    def download(url, dest, timeout=60):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "w").write(content)
        return dest
    monkeypatch.setattr(updates.downloads, "download", download)


def test_update_refused_when_checksum_does_not_match(tmp_path, monkeypatch):
    up = updates.Updates(str(tmp_path / "a"), str(tmp_path / "u.json"))
    monkeypatch.setattr(up, "latest", lambda c: {"version": "v2", "url": "u", "name": "Moonlight-v2-x86_64.AppImage", "sha256": "0" * 64})
    _fake_dl(monkeypatch, "tampered")
    with pytest.raises(ValueError, match="checksum"):
        up.ensure("moonlight")
    assert up.installed("moonlight") is None


def test_update_with_matching_checksum_then_rollback_and_hold(tmp_path, monkeypatch):
    up = updates.Updates(str(tmp_path / "a"), str(tmp_path / "u.json"))
    import hashlib
    ver = {"v": "v1"}
    monkeypatch.setattr(up, "latest", lambda c: {"version": ver["v"], "url": "u", "name": "Moonlight.AppImage",
                                                 "sha256": hashlib.sha256(f"build {ver['v']}\n".encode()).hexdigest()})

    def dl(url, dest, timeout=60):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "w").write(f"build {ver['v']}\n")
        return dest
    monkeypatch.setattr(updates.downloads, "download", dl)
    up.ensure("moonlight")
    ver["v"] = "v2"
    up.check(force=True)
    assert up.ensure("moonlight")["updated"] and up.installed("moonlight") == "v2" and up.previous("moonlight") == "v1"
    r = up.rollback("moonlight")
    assert r["version"] == "v1" and r["previous"] == "v2"
    st = up.check(force=True)
    assert not st["moonlight"]["update"]                       # v2 is the one rolled away from: not offered again
    assert not up.ensure("moonlight")["updated"]
    ver["v"] = "v3"
    assert up.check(force=True)["moonlight"]["update"]          # a newer build is offered again


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
