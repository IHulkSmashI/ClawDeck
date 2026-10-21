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
    at._step(); at._step()
    assert s.learned.tdp == 30                     # two seconds are not enough to judge
    at._step()
    assert s.learned.tdp == 29 and applied[-1] == 29 and s.window == []   # the window restarts after a change
    bad = autotune.Sample(t=0, frametime_ms=25.0, fps=40, gpu_mhz=2390, gpu_max_mhz=2400, cpu_pct=40, pkg_w=20)
    monkeypatch.setattr(at.sampler, "sample", lambda: bad)
    for _ in range(3):
        at._step()
    # GPU-bound with an idle CPU: moves budget from the CPU to the GPU first, no extra watts
    assert s.learned.cpu_max_pct == 90 and s.learned.gpu_min_mhz is None and s.learned.tdp == 29
    for _ in range(9):                             # three more decisions: CPU 80, 70, 60 (its floor)
        at._step()
    assert s.learned.cpu_max_pct == 60
    for _ in range(3):                             # CPU can't give more: then the GPU floor, still no extra watts
        at._step()
    assert s.learned.gpu_min_mhz == 1400 and s.learned.tdp == 29
    at.session = None


def _session(fake_hw, monkeypatch, settle=1, **learned):
    pw = power.Power(fake_hw)
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: True)    # a Claw whose TDP writes work
    at = autotune.Autotune(fake_hw, pw, log_dir="/nonexistent", csv_log=None)
    at.session = autotune.Session(appid="1", target_fps=60, floor_tdp=8, ceiling_tdp=17,
                                  learned=autotune.Learned(**{"tdp": 17, "cpu_max_pct": 100, "gpu_min_mhz": None, **learned}),
                                  settle=settle)
    return at, at.session


def _sample(fps, t=0.0, **kw):
    base = dict(gpu_mhz=1500, gpu_max_mhz=2400, cpu_pct=40, pkg_w=12, gpu_busy=70)
    base.update(kw)
    return autotune.Sample(t=t, frametime_ms=1000.0 / fps, fps=fps, **base)


def test_autotune_on_target_trims_the_side_that_isnt_the_bottleneck(fake_hw, monkeypatch):
    at, s = _session(fake_hw, monkeypatch)
    monkeypatch.setattr(at.sampler, "sample", lambda: _sample(62, cpu_pct=35, gpu_busy=97))
    at._step()                                # rebalance turn: CPU gives up budget
    assert s.learned.cpu_max_pct == 90 and s.learned.tdp == 17
    at._step()                                # TDP turn
    assert s.learned.tdp == 16
    assert sysfs.read_int(fake_hw.paths.cpu_max_perf_pct) == 90
    at.session = None


def test_autotune_cpu_bound_gets_cpu_then_gives_back_gpu_floor(fake_hw, monkeypatch):
    at, s = _session(fake_hw, monkeypatch, cpu_max_pct=70, gpu_min_mhz=1800)
    monkeypatch.setattr(at.sampler, "sample", lambda: _sample(45, gpu_mhz=1200, cpu_pct=95, gpu_busy=55))
    at._step()
    assert s.learned.cpu_max_pct == 80 and s.learned.tdp == 17     # more CPU, not more watts
    at._step(); at._step()
    assert s.learned.cpu_max_pct == 100
    at._step()
    assert s.learned.gpu_min_mhz is None and s.learned.tdp == 17   # then the GPU floor goes, freeing budget
    at.session = None


def test_autotune_judges_the_average_not_one_second(fake_hw, monkeypatch):
    at, s = _session(fake_hw, monkeypatch, settle=10)
    seq = iter([_sample(f) for f in (61, 60, 62, 58, 61, 60, 63, 59, 60, 61)])   # one dip to 58 among 60s
    monkeypatch.setattr(at.sampler, "sample", lambda: next(seq))
    for _ in range(10):
        at._step()
    assert s.learned.tdp == 16                     # average 60.5, worst 58: on target, so power goes down
    assert at.status()["avg_fps"] == 60.5 and at.status()["low_fps"] == 58
    seq2 = iter([_sample(f) for f in (60, 60, 60, 60, 40, 60, 60, 60, 60, 60)])  # average fine, one big stutter
    monkeypatch.setattr(at.sampler, "sample", lambda: next(seq2))
    for _ in range(10):
        at._step()
    assert s.learned.tdp == 17                     # one 40 fps second is a stutter: missing, power back up
    at.session = None


def test_autotune_rechecks_a_learned_game(fake_hw, monkeypatch):
    at, s = _session(fake_hw, monkeypatch, settle=1, confidence=3)
    t = [0.0]
    monkeypatch.setattr(at.sampler, "sample", lambda: _sample(61, t=t[0]))
    at._step()
    assert s.learned.tdp == 17                     # learned: no lowering right away
    t[0] = autotune.RECHECK_S + 1
    at._step()
    assert s.learned.tdp == 16                     # after RECHECK_S on target it probes one step lower
    monkeypatch.setattr(at.sampler, "sample", lambda: _sample(50, t=t[0] + 1))
    at._step()
    assert s.learned.tdp == 17                     # too low: back up at once
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



def test_picked_power_is_autotunes_ceiling(fake_hw, monkeypatch):
    at = autotune.Autotune(fake_hw, power.Power(fake_hw), "/nonexistent")
    monkeypatch.setattr(at, "_apply", lambda l: None)
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    at.start("1", 60, {"tdp": 9, "cpu_max_pct": 100, "gpu_min_mhz": None}, 8, 17, False)
    at.pause("manual GPU clocks")
    at.set_ceiling(12)                      # the user picks Battery (12 W) mid-game
    s = at.session
    assert s.ceiling_tdp == 12 and s.learned.tdp == 12 and not s.paused
    at.set_ceiling(3)
    assert s.ceiling_tdp == 8               # never below the model's minimum


def test_sampler_averages_every_new_mangohud_row(fake_hw, tmp_path):
    log = tmp_path / "game_2026.csv"
    log.write_text("os,cpu,gpu\nLinux,x,y\nfps,frametime,cpu_load\n60,16.6,30\n")
    smp = autotune.Sampler(fake_hw, str(tmp_path))
    assert smp.frametime() == (16.6, 60.0)
    with open(log, "a") as f:
        f.write("50,20.0,30\n70,14.2,30\n")          # two rows since the last read: both count
    ft, fps = smp.frametime()
    assert fps == 60.0 and abs(ft - 17.1) < 0.01
    assert smp.frametime() == (None, None)            # nothing new


def test_autotune_learns_again_when_graphics_are_lowered(fake_hw, monkeypatch):
    at, s = _session(fake_hw, monkeypatch, settle=1, confidence=4)
    monkeypatch.setattr(at.sampler, "sample", lambda: _sample(61))
    at._step()
    assert s.learned.tdp == 17 and s.learned.confidence == 4      # learned and on target: left alone
    monkeypatch.setattr(at.sampler, "sample", lambda: _sample(80))  # player lowered the settings: 80 fps for 60
    at._step()
    assert s.learned.confidence == 0 and (s.learned.tdp < 17 or s.learned.cpu_max_pct < 100)   # saving power again
    at.session = None


def test_sampler_caches_the_dear_reads(fake_hw, tmp_path, monkeypatch):
    (tmp_path / "a.csv").write_text("fps,frametime\n60,16.6\n")
    smp = autotune.Sampler(fake_hw, str(tmp_path))
    globs, busy = [], []
    real_glob = autotune.glob.glob
    monkeypatch.setattr(autotune.glob, "glob", lambda p: globs.append(p) or real_glob(p))
    monkeypatch.setattr(autotune.shadowblip, "gpu_busy_percent", lambda: busy.append(1) or 80)
    for _ in range(20):
        smp.frametime()
        smp.gpu_busy()
    assert len(globs) == 1 and len(busy) == 1        # one directory listing, one D-Bus call, not 20 of each


def test_charge_limit_survives_closing_a_game(fake_hw):
    pw = power.Power(fake_hw)
    assert battery.Battery(fake_hw).set_charge_limit(80)
    pw.set_cpu_max_pct(50)
    pw.revert_all()                                   # what closing a game does
    assert sysfs.read_int(fake_hw.paths.charge_limit) == 80
    assert sysfs.read_int(fake_hw.paths.cpu_max_perf_pct) == 100


def test_dry_run_keeps_the_revert_record(fake_hw):
    pw = power.Power(fake_hw)
    pw.set_cpu_max_pct(50)                            # a real write
    sysfs.set_dry_run(True)
    pw.revert_all()                                   # only logged
    sysfs.set_dry_run(False)
    assert sysfs.read_int(fake_hw.paths.cpu_max_perf_pct) == 50
    pw.revert_all()                                   # still knows the original
    assert sysfs.read_int(fake_hw.paths.cpu_max_perf_pct) == 100


def test_autotune_ignores_a_tdp_the_system_refused(fake_hw, monkeypatch):
    at, s = _session(fake_hw, monkeypatch, settle=1)
    monkeypatch.setattr(at.power, "set_tdp", lambda w, plugged=None: False)   # e.g. HHD's TDP controls not enabled
    monkeypatch.setattr(at.sampler, "sample", lambda: _sample(61))
    at._step()
    assert s.learned.tdp == 17                         # not "learned" as 16 W when nothing changed
    at.session = None


def test_half_written_mangohud_row_waits_for_the_rest(fake_hw, tmp_path):
    log = tmp_path / "g.csv"
    log.write_text("fps,frametime\n60.1,16.6\n60.1,1")          # MangoHud mid-way through a row
    smp = autotune.Sampler(fake_hw, str(tmp_path))
    assert smp.frametime() == (16.6, 60.1)                       # only the complete row
    with open(log, "a") as f:
        f.write("6.6\n")
    assert smp.frametime() == (16.6, 60.1)                       # the finished row, not a fake 6.6 fps


def test_gpu_window_without_hardware_max_never_pins_to_zero(fake_hw):
    fake_hw.paths.gpu_rp0_freq = None
    pw = power.Power(fake_hw)
    assert pw.set_gpu_window(1200, None)
    assert sysfs.read_int(fake_hw.paths.gpu_min_freq) == 1200 and sysfs.read_int(fake_hw.paths.gpu_max_freq) == 2400


def test_plugin_runs_as_root():
    # Decky's template ships "_root" (= off). Every sysfs write in ClawDeck needs root; the first on-device run
    # (2026-10-21) failed with Permission denied on all of them because of the underscore.
    with open(os.path.join(os.path.dirname(__file__), "..", "plugin.json")) as f:
        assert "root" in json.load(f)["flags"]


def test_tls_context_uses_system_bundle(monkeypatch, tmp_path):
    from clawdeck import tls
    bundle = tmp_path / "ca.crt"
    bundle.write_text("")  # existence is what is probed; an empty file still yields a context
    monkeypatch.setattr(tls, "CA_BUNDLES", (str(bundle),))
    tls._ctx = None
    try:
        tls.context()
    except Exception:  # an empty PEM may be rejected by OpenSSL; the fallback path is then exercised below
        pass
    tls._ctx = None
    monkeypatch.setattr(tls, "CA_BUNDLES", ("/nonexistent/ca.crt",))
    assert tls.context().verify_mode.name == "CERT_REQUIRED"
    tls._ctx = None


def test_tdp_reads_back_from_rapl_and_failures_are_explained(fake_hw, tmp_path):
    pl1 = tmp_path / "rapl/constraint_0_power_limit_uw"
    _w(str(pl1), 25_000_000)
    fake_hw.paths.rapl_package = str(tmp_path / "rapl")
    fake_hw.paths.rapl_pl1 = str(pl1)
    pw = power.Power(fake_hw)
    assert pw.read_tdp() == 25 and pw.status()["tdp_w"] == 25
    assert pw.set_tdp(17, plugged=False) and pw.read_tdp() == 17
    sysfs.clear_error()
    assert sysfs.last_error() is None
    assert not sysfs.write(str(tmp_path / "missing/dir/file"), 1)      # a refused write says why
    assert "writing 1 to" in (sysfs.last_error() or "") and "file" in sysfs.last_error()
