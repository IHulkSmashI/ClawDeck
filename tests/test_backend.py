"""Backend tests against a fake sysfs tree (no hardware needed): detection, clamping, revert, dry-run, profiles,
autotune control logic. Run: python3 -m pytest -q tests"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from clawdeck import autotune, battery, hw, power, profiles, sysfs  # noqa: E402


def _w(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"{value}\n")


@pytest.fixture
def fake_hw(tmp_path, monkeypatch):
    root = tmp_path
    gt = root / "drm/card0/device/tile0/gt0/freq0"
    for n, v in (("min_freq", 300), ("max_freq", 2400), ("cur_freq", 1200), ("rp0_freq", 2400), ("rpe_freq", 1300), ("rpn_freq", 300)):
        _w(str(gt / n), v)
    _w(str(root / "drm/card0/device/vendor"), "0x8086")
    _w(str(root / "cpu/intel_pstate/max_perf_pct"), 100)
    _w(str(root / "cpu/intel_pstate/min_perf_pct"), 10)
    _w(str(root / "cpu/intel_pstate/no_turbo"), 0)
    _w(str(root / "bat/BAT1/capacity"), 77)
    _w(str(root / "bat/BAT1/status"), "Discharging")
    _w(str(root / "bat/BAT1/charge_control_end_threshold"), 100)
    p = hw.Paths(gpu_card=str(root / "drm/card0"), gpu_min_freq=str(gt / "min_freq"), gpu_max_freq=str(gt / "max_freq"),
                 gpu_cur_freq=str(gt / "cur_freq"), gpu_rp0_freq=str(gt / "rp0_freq"), gpu_rpe_freq=str(gt / "rpe_freq"),
                 gpu_rpn_freq=str(gt / "rpn_freq"), cpu_max_perf_pct=str(root / "cpu/intel_pstate/max_perf_pct"),
                 cpu_min_perf_pct=str(root / "cpu/intel_pstate/min_perf_pct"), cpu_no_turbo=str(root / "cpu/intel_pstate/no_turbo"),
                 battery=str(root / "bat/BAT1"), charge_limit=str(root / "bat/BAT1/charge_control_end_threshold"))
    h = hw.Hardware(product="MS-1T91", model=hw.MODELS["MS-1T91"], known=True, kernel="test", cpu_model="test",
                    gpu_driver="xe", paths=p)
    sysfs.set_dry_run(False)
    sysfs._originals.clear()
    monkeypatch.setattr("clawdeck.hhd.available", lambda: False)
    return h


def test_model_table_has_ex():
    assert hw.MODELS["MS-1T91"]["soc"] == "Panther Lake"
    assert hw.MODELS["MS-1T91"]["tdp"]["sppt_max"] == 45


def test_gpu_window_clamps_and_orders(fake_hw):
    pw = power.Power(fake_hw)
    assert pw.set_gpu_window(9999, 9999)          # clamped to rp0
    assert sysfs.read_int(fake_hw.paths.gpu_max_freq) == 2400
    assert sysfs.read_int(fake_hw.paths.gpu_min_freq) == 2400
    assert pw.set_gpu_window(100, 1500)           # min clamped to rpn, lowering: min first then max
    assert sysfs.read_int(fake_hw.paths.gpu_min_freq) == 300
    assert sysfs.read_int(fake_hw.paths.gpu_max_freq) == 1500
    assert pw.set_gpu_window(1800, 1500)          # floor above ceiling collapses to ceiling
    assert sysfs.read_int(fake_hw.paths.gpu_min_freq) == 1500


def test_revert_restores_originals(fake_hw):
    pw = power.Power(fake_hw)
    pw.set_cpu_max_pct(50)
    pw.set_gpu_window(1000, 2000)
    assert sysfs.read_int(fake_hw.paths.cpu_max_perf_pct) == 50
    n = pw.revert_all()
    assert n == 3
    assert sysfs.read_int(fake_hw.paths.cpu_max_perf_pct) == 100
    assert sysfs.read_int(fake_hw.paths.gpu_min_freq) == 300
    assert sysfs.read_int(fake_hw.paths.gpu_max_freq) == 2400


def test_dry_run_writes_nothing(fake_hw):
    sysfs.set_dry_run(True)
    pw = power.Power(fake_hw)
    assert pw.set_cpu_max_pct(40)
    assert sysfs.read_int(fake_hw.paths.cpu_max_perf_pct) == 100
    sysfs.set_dry_run(False)


def test_cpu_pct_never_below_min(fake_hw):
    pw = power.Power(fake_hw)
    pw.set_cpu_max_pct(1)
    assert sysfs.read_int(fake_hw.paths.cpu_max_perf_pct) == 10


def test_tdp_without_hhd_or_rapl_fails_cleanly(fake_hw):
    pw = power.Power(fake_hw)
    assert pw.set_tdp(25) is False


def test_charge_limit_choices_and_write(fake_hw):
    b = battery.Battery(fake_hw)
    with pytest.raises(ValueError):
        b.set_charge_limit(75)
    assert b.set_charge_limit(60)
    assert sysfs.read_int(fake_hw.paths.charge_limit) == 60
    assert b.status()["capacity_pct"] == 77


def test_profiles_roundtrip(tmp_path):
    p = profiles.Profiles(str(tmp_path / "profiles.json"))
    p.set("12345", False, {"tdp": 22, "target_fps": 60})
    assert p.get("12345", False)["tdp"] == 22
    assert p.get("12345", False)["base"] == "battery"
    assert p.get("12345", True)["base"] == "performance"
    txt = p.export_json("12345")
    q = profiles.Profiles(str(tmp_path / "other.json"))
    assert q.import_json(txt) == 1
    assert q.get("12345", False)["target_fps"] == 60
    with pytest.raises(ValueError):
        q.import_json(json.dumps({"version": 99}))


def test_classify():
    s = autotune.Sample(t=0, frametime_ms=20, fps=50, gpu_mhz=2380, gpu_max_mhz=2400, cpu_pct=30, pkg_w=20)
    assert autotune.classify(s) == "gpu"
    s = autotune.Sample(t=0, frametime_ms=20, fps=50, gpu_mhz=1200, gpu_max_mhz=2400, cpu_pct=92, pkg_w=20)
    assert autotune.classify(s) == "cpu"
    s = autotune.Sample(t=0, frametime_ms=20, fps=50, gpu_mhz=1500, gpu_max_mhz=2400, cpu_pct=50, pkg_w=20)
    assert autotune.classify(s) == "balanced"


def test_autotune_lowers_then_raises(fake_hw, monkeypatch):
    pw = power.Power(fake_hw)
    applied = []
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: applied.append(w) or True)
    at = autotune.Autotune(fake_hw, pw, log_dir=str("/nonexistent"), csv_log=None)
    s = autotune.Session(appid="1", target_fps=60, floor_tdp=8, ceiling_tdp=35,
                         learned=autotune.Learned(tdp=30, cpu_max_pct=100, gpu_min_mhz=None), settle=3)
    at.session = s
    good = autotune.Sample(t=0, frametime_ms=15.0, fps=66, gpu_mhz=1500, gpu_max_mhz=2400, cpu_pct=50, pkg_w=20)
    monkeypatch.setattr(at.sampler, "sample", lambda: good)
    for _ in range(3):
        at._step()
    assert s.learned.tdp == 29 and applied[-1] == 29
    bad = autotune.Sample(t=0, frametime_ms=25.0, fps=40, gpu_mhz=2390, gpu_max_mhz=2400, cpu_pct=40, pkg_w=20)
    monkeypatch.setattr(at.sampler, "sample", lambda: bad)
    at._step(); at._step()
    # GPU-bound: raises the GPU floor first, not the TDP
    assert s.learned.gpu_min_mhz == 1400 and s.learned.tdp == 29
    at.session = None


def test_four_profiles_step_up_in_power():
    for table in hw.PROFILES.values():
        assert list(table) == ["battery", "balanced", "performance", "dock"]
        tdps = [table[n]["tdp"] for n in table]
        assert tdps == sorted(tdps) and len(set(tdps)) == 4
        assert table["battery"]["tdp"] <= 15 and table["balanced"]["tdp"] <= 20   # battery play stays efficient


def test_balanced_profile_applies(fake_hw):
    r = power.Power(fake_hw).apply_profile("balanced")
    assert r["applied"]["cpu_max_pct"] == 85
    assert sysfs.read_int(fake_hw.paths.cpu_max_perf_pct) == 85


def test_face_buttons_override(tmp_path, monkeypatch):
    from clawdeck import buttons
    stock = tmp_path / "50-msi_claw8_ex_ai_cg3em.yaml"
    stock.write_text("name: MSI Claw 8 EX AI+ CG3EM\nsource_devices: []\ncapability_map_id: claw2\n")
    monkeypatch.setattr(buttons, "STOCK_DEVICE", str(stock))
    monkeypatch.setattr(buttons, "DEVICE_OVERRIDE", str(tmp_path / "devices.d/50-msi_claw8_ex_ai_cg3em.yaml"))
    monkeypatch.setattr(buttons, "MAP_OVERRIDE", str(tmp_path / "capability_maps.d/msiclaw_claw8ex.yaml"))
    monkeypatch.setattr(buttons, "LEGACY_HWDB", (str(tmp_path / "old.hwdb"),))
    assert buttons.stock_map_id() == "claw2"
    assert not buttons.status("MS-1T41")["applies"]
    st = buttons.status("MS-1T91")
    assert st["applies"] and not st["ok"] and not st["installed"]
    assert "capability_map_id: claw8ex" in buttons.device_override_text()
    sysfs.set_dry_run(True)
    assert buttons.apply()["dry_run"]
    sysfs.set_dry_run(False)
    calls = []
    monkeypatch.setattr(buttons, "_run", lambda cmd, timeout=30: calls.append(cmd) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    r = buttons.apply()
    assert r["ok"] and calls == [["systemctl", "restart", "inputplumber"]]
    assert buttons.status("MS-1T91")["ok"]
    assert "- keyboard: KeyG" in (tmp_path / "capability_maps.d/msiclaw_claw8ex.yaml").read_text()
    stock.write_text("capability_map_id: claw8ex_upstream\n")
    assert buttons.status("MS-1T91")["upstream_fixed"]
    r = buttons.remove()
    assert r["ok"] and len(r["removed"]) == 2 and not buttons.status("MS-1T91")["installed"]


def test_autotune_battery_ceiling():
    lim = hw.MODELS["MS-1T91"]["tdp"]
    assert autotune.ceiling(lim, False, None) == 17                # default: Balanced
    assert autotune.ceiling(lim, False, 25) == 25                  # the user's choice
    assert autotune.ceiling(lim, False, 99) == lim["battery_max"]  # never above the model's limit
    assert autotune.ceiling(lim, False, 2) == lim["min"]
    assert autotune.ceiling(lim, False, "x") == 17
    assert autotune.ceiling(lim, True, 17) == lim["plugged_max"]    # plugged in: the full limit
