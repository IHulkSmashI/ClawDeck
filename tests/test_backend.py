"""Backend tests against a fake sysfs tree (no hardware needed): detection, clamping, revert, profiles,
autotune control logic. Run: python3 -m pytest -q tests"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from clawdeck import autotune, hw, power, profiles, sysfs, thermal  # noqa: E402


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


def test_cpu_pct_never_below_min(fake_hw):
    pw = power.Power(fake_hw)
    pw.set_cpu_max_pct(1)
    assert sysfs.read_int(fake_hw.paths.cpu_max_perf_pct) == 10


def test_tdp_without_hhd_or_rapl_fails_cleanly(fake_hw):
    pw = power.Power(fake_hw)
    assert pw.set_tdp(25) is False


def test_profiles_roundtrip(tmp_path):
    p = profiles.Profiles(str(tmp_path / "profiles.json"))
    p.set("12345", False, {"tdp": 22, "target_fps": 60})
    assert p.get("12345", False)["tdp"] == 22
    assert p.get("12345", False)["base"] == "battery"
    assert p.get("12345", True)["base"] == "performance"
    q = profiles.Profiles(str(tmp_path / "profiles.json"))      # the file round-trips
    assert q.get("12345", False)["target_fps"] == 60


def test_classify():
    s = autotune.Sample(t=0, frametime_ms=20, fps=50, gpu_mhz=2380, gpu_max_mhz=2400, cpu_pct=30, pkg_w=20)
    assert autotune.classify(s) == "gpu"
    s = autotune.Sample(t=0, frametime_ms=20, fps=50, gpu_mhz=1200, gpu_max_mhz=2400, cpu_pct=92, pkg_w=20)
    assert autotune.classify(s) == "cpu"
    s = autotune.Sample(t=0, frametime_ms=20, fps=50, gpu_mhz=1500, gpu_max_mhz=2400, cpu_pct=50, pkg_w=20)
    assert autotune.classify(s) == "balanced"





def test_six_profiles_step_up_in_power():
    for table in hw.PROFILES.values():
        assert list(table) == ["saver", "battery", "balanced", "performance", "plugged", "max"]
        tdps = [table[n]["tdp"] for n in table]
        assert tdps == [10, 12, 17, 25, 30, 35]
    assert hw.profile_name("dock") == "max"                        # a saved "dock" from an earlier version still works
    assert hw.tdp_range(hw.MODELS["MS-1T91"]) == (8, 35)


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



def test_cpu_boost_lever_and_profile(fake_hw):
    pw = power.Power(fake_hw)
    assert pw.turbo() is True
    assert pw.set_cpu_turbo(False) and pw.turbo() is False and pw.status()["cpu"]["no_turbo"] == 1
    pw.revert_all()
    assert pw.turbo() is True
    r = pw.apply_profile("dock")                                   # alias of max
    assert r["profile"] == "max" and r["applied"]["cpu_max_pct"] == 100
    assert pw.status()["range"] == {"min": 8, "max": 35}


def test_gpu_floor_reset_keeps_a_user_ceiling(fake_hw):
    pw = power.Power(fake_hw)
    assert pw.set_gpu_window(None, 2000) and pw.set_gpu_window(1500, None)
    assert pw.reset_gpu_floor()
    assert sysfs.read_int(fake_hw.paths.gpu_min_freq) == 300 and sysfs.read_int(fake_hw.paths.gpu_max_freq) == 2000


def test_clean_env_drops_loader_variables(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    monkeypatch.setenv("PYTHONPATH", "/tmp/_MEI123/base_library.zip")
    monkeypatch.setenv("HOME", "/home/x")
    env = sysfs.clean_env()
    assert "LD_LIBRARY_PATH" not in env and "PYTHONPATH" not in env and env["HOME"] == "/home/x" and env["PATH"]


def test_thermald_paused_while_tdp_held_and_restarted_on_revert(fake_hw, monkeypatch, tmp_path):
    from clawdeck import power as pw
    root = tmp_path / "rapl"
    _w(str(root / "constraint_0_power_limit_uw"), 30_000_000)
    _w(str(root / "constraint_1_power_limit_uw"), 37_000_000)
    fake_hw.paths.rapl_pl1 = str(root / "constraint_0_power_limit_uw")
    fake_hw.paths.rapl_pl2 = str(root / "constraint_1_power_limit_uw")
    calls = []
    state = {"active": True}

    class R:
        def __init__(self, out=""): self.returncode, self.stdout, self.stderr = 0, out, ""

    def fake(*args):
        calls.append(args)
        if args[0] == "is-active":
            return R("active" if state["active"] else "inactive")
        state["active"] = args[0] == "start"
        return R()
    monkeypatch.setattr(pw, "_systemctl", fake)
    monkeypatch.setattr(pw.hhd, "available", lambda: False)
    p = pw.Power(fake_hw)
    assert p.set_tdp(15)
    assert ("stop", pw.THERMALD) in calls and not state["active"]
    assert sysfs.read_int(fake_hw.paths.rapl_pl1) == 15_000_000
    p.set_tdp(12)
    assert calls.count(("stop", pw.THERMALD)) == 1          # stopped once, not per write
    p.revert_all()
    assert ("start", pw.THERMALD) in calls and state["active"]
    assert sysfs.read_int(fake_hw.paths.rapl_pl1) == 30_000_000


# ---- autotune v3 (findings §22): the most the set TDP gives, CPU/GPU balance, the lowest TDP within KEEP % --------
class _Game:
    """A game whose rendered fps follows the power it gets: fps(tdp, cpu_max_pct, gpu_min_mhz, boost)."""
    def __init__(self, at, fps, **smp):
        self.at, self.fps, self.smp = at, fps, smp
        at.sampler.sample = self.sample

    def sample(self):
        l = self.at.session.learned
        f = self.fps(l.tdp, l.cpu_max_pct, l.gpu_min_mhz, l.boost)
        base = dict(gpu_mhz=1500, gpu_max_mhz=2400, cpu_pct=40, pkg_w=float(l.tdp), gpu_busy=70, source="rendered", shown_fps=f)
        base.update(self.smp)
        return autotune.Sample(t=time.monotonic(), frametime_ms=1000.0 / f, fps=f, **base)


def _tuner(fake_hw, monkeypatch, ceiling=20, **learned):
    pw = power.Power(fake_hw)
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: True)    # a Claw whose TDP writes work
    at = autotune.Autotune(fake_hw, pw, log_dir="/nonexistent", csv_log=None)
    at.session = autotune.Session(appid="1", floor_tdp=8, ceiling_tdp=ceiling, settle=1,
                                  learned=autotune.Learned(**{"tdp": ceiling, **learned}))
    return at, at.session


def test_autotune_finds_the_lowest_tdp_within_keep_of_the_most(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch)
    _Game(at, lambda tdp, *_: min(60.0, 3.0 * tdp))            # capped at 60 fps from 20 W, 3 fps per watt below
    for _ in range(3):
        at._step()
    assert s.learned.max_fps == 60 and s.phase == "balance"    # median of three windows at the set TDP
    for _ in range(12):
        at._step()
    # 19 W gives 57 fps = 95 % of 60, the line; 18 W gives 54, missed twice, so back up to 19 W and hold there
    assert s.learned.tdp == 19 and s.phase == "hold" and s.learned.fps == 57
    st = at.status()
    assert st["max_fps"] == 60 and st["pct_of_max"] == 100 or st["pct_of_max"] >= 95
    assert st["fps_per_w"] and st["source"] == "rendered"
    at.session = None


def test_autotune_one_slow_window_is_measured_again_not_acted_on(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch)
    seq = iter([60, 60, 60, 60, 59, 40, 59, 59])               # one heavy patch of the map at 18 W
    _Game(at, lambda *_: float(next(seq)))
    for _ in range(7):
        at._step()
    # 40 fps once at 18 W was measured again, not acted on: the next window held the line, so lowering went on
    assert s.learned.tdp == 17 and s.phase == "balance" and s.last_delta == "−1 W"
    at.session = None


def test_autotune_keeps_a_cpu_gpu_lever_only_if_it_helps(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch)
    # GPU-bound: giving the GPU the CPU's share helps once (100 -> 90 %), not twice
    _Game(at, lambda tdp, cpu, *_: (52.0 if cpu <= 90 else 50.0) if tdp == 20 else 30.0, cpu_pct=35, gpu_busy=97)
    for _ in range(3):
        at._step()
    assert s.learned.max_fps == 50
    at._step()                                                  # trying CPU ceiling 90 %
    assert s.learned.cpu_max_pct == 90 and "trying" in s.last_action
    at._step()                                                  # +4 %: kept, the set TDP gives 52 now; next: 80 %
    assert s.learned.max_fps == 52 and s.learned.cpu_max_pct == 80
    at._step()                                                  # no gain: undone
    assert s.learned.cpu_max_pct == 90 and "undone" in s.last_action
    assert sysfs.read_int(fake_hw.paths.cpu_max_perf_pct) == 90
    at.session = None


def test_autotune_cpu_bound_gets_boost_before_anything_else(fake_hw, monkeypatch):
    _w(fake_hw.paths.cpu_no_turbo, 1)
    at, s = _tuner(fake_hw, monkeypatch, boost=False, cpu_max_pct=70)
    _Game(at, lambda tdp, cpu, gpu, boost: 40.0 + (5 if boost else 0) + (5 if cpu == 100 else 0), cpu_pct=95, gpu_busy=50)
    for _ in range(4):
        at._step()
    assert s.learned.boost and sysfs.read_int(fake_hw.paths.cpu_no_turbo) == 0
    at._step(); at._step()
    assert s.learned.cpu_max_pct == 100 and s.learned.max_fps == 50
    sysfs.revert_all()
    assert sysfs.read_int(fake_hw.paths.cpu_no_turbo) == 1      # every lever it moved is revertible
    at.session = None


def test_autotune_counts_rendered_frames_not_generated_ones(fake_hw, tmp_path):
    mh = tmp_path / "mangohud"
    mh.mkdir()
    (mh / "game.csv").write_text("fps,frametime\n90,11.1\n")    # what the screen shows with 3x frame generation
    xl = tmp_path / "igdext_xell.log"
    xl.write_text("12:00:00.000 [1] XellHook start in game.exe\n")
    smp = autotune.Sampler(fake_hw, str(mh))
    smp.set_prefix(str(xl))
    with open(xl, "a") as f:
        f.write("12:00:03.333 [7] xellSleep stats: 100 calls in 3333 ms (33.3 ms/frame), blocked avg 0.10 ms max 0.50 ms = 0% of wall\n")
    s = smp.sample()
    assert s.source == "rendered" and round(s.fps) == 30 and s.shown_fps == 90.0
    xl.write_text("")                                           # the wrapper rotated the log at the next launch
    with open(xl, "a") as f:
        f.write("xellSleep stats: 100 calls in 2000 ms (20.0 ms/frame), blocked\n")
    assert round(smp.sample().fps) == 50
    smp._xell = (time.monotonic() - autotune.XELL_FRESH_S - 1, 50.0)   # XeLL went quiet: frame generation off
    s = smp.sample()
    assert s.source == "presented" and s.fps is None                 # (no new MangoHud row this second)


def test_autotune_measures_again_when_frame_generation_changes(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch)
    shown = {"x": 2.0}
    g = _Game(at, lambda *_: 30.0)
    real = g.sample
    g.sample = lambda: (lambda smp: (setattr(smp, "shown_fps", 30.0 * shown["x"]), smp)[1])(real())
    at.sampler.sample = g.sample
    for _ in range(5):
        at._step()
    assert s.phase != "measure" and s.multiplier == 2.0
    shown["x"] = 3.0                                            # the player switched XeSS FG from 2x to 3x
    at._step()
    assert s.phase == "measure" and "frame generation changed" in s.last_action
    at.session = None


def test_autotune_a_new_scene_or_the_clock_measures_again(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch)
    fps = {"v": 60.0}
    _Game(at, lambda tdp, *_: fps["v"] if tdp >= 19 else fps["v"] * 0.8)
    for _ in range(8):
        at._step()
    assert s.phase == "hold" and s.learned.tdp == 19
    fps["v"] = 40.0                                             # a much heavier area
    at._step(); at._step()
    assert s.phase == "measure" and s.learned.tdp == 20
    at.session.phase, at.session.measured_at = "hold", time.monotonic() - autotune.RESCAN_S - 1
    s.learned.fps = 40.0
    at._step()
    assert s.phase == "measure" and "routine" in s.last_action
    at.session = None


def test_set_tdp_is_measured_again_and_hands_control_back(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, tdp=15)
    at.pause("manual GPU clocks")
    assert at.set_ceiling(12) == 12 and s.phase == "measure" and not s.paused
    at.set_ceiling(3)
    assert s.ceiling_tdp == 8                                   # never below the model's minimum
    s.ceiling_tdp = 20
    at.set_floor(10)
    assert s.floor_tdp == 10 and s.learned.tdp >= 10


def test_autotune_start_reuses_a_result_only_for_the_same_ceiling(fake_hw, monkeypatch):
    pw = power.Power(fake_hw)
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: True)
    at = autotune.Autotune(fake_hw, pw, "/nonexistent")
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    saved = {"tdp": 14, "max_fps": 60.0, "fps": 58.0, "ceiling": 20, "cpu_max_pct": 90, "confidence": 3}   # + an old key
    at.start("1", saved, 8, 20, False)
    assert at.session.phase == "hold" and at.session.learned.tdp == 14
    at.start("1", saved, 8, 25, False)                          # a different set TDP: measure what it gives
    assert at.session.phase == "measure" and at.session.learned.tdp == 25 and at.session.learned.cpu_max_pct == 90
    at.session = None


def test_autotune_pause_stops_changes(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch)
    _Game(at, lambda tdp, *_: min(60.0, 3.0 * tdp))
    for _ in range(4):
        at._step()
    assert s.learned.tdp == 19
    at.pause()
    for _ in range(5):
        at._step()
    assert s.learned.tdp == 19
    at.session = None


def test_autotune_a_refused_tdp_is_not_learned(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch)
    monkeypatch.setattr(at.power, "set_tdp", lambda w, plugged=None: False)   # e.g. HHD's TDP controls not enabled
    _Game(at, lambda *_: 60.0)
    for _ in range(5):
        at._step()
    assert s.learned.tdp == 20 and s.phase == "hold"
    at.session = None


# ---- temperature guard while thermald is paused (findings §21) ----------------------------------------------------
def test_package_temperature_sensor_x86_pkg_temp_first_coretemp_fallback(tmp_path):
    _w(tmp_path / "class/thermal/thermal_zone0/type", "acpitz")
    _w(tmp_path / "class/thermal/thermal_zone0/temp", "40000")
    _w(tmp_path / "class/hwmon/hwmon3/name", "coretemp")
    _w(tmp_path / "class/hwmon/hwmon3/temp1_label", "Core 0")
    _w(tmp_path / "class/hwmon/hwmon3/temp1_input", "60000")
    _w(tmp_path / "class/hwmon/hwmon3/temp2_label", "Package id 0")
    _w(tmp_path / "class/hwmon/hwmon3/temp2_input", "71000")
    path, sensor = hw.find_pkg_temp(str(tmp_path))                 # no x86_pkg_temp zone: coretemp's package line
    assert sensor == "coretemp" and path.endswith("temp2_input") and thermal.read_c(path) == 71.0
    _w(tmp_path / "class/thermal/thermal_zone4/type", "x86_pkg_temp")
    _w(tmp_path / "class/thermal/thermal_zone4/temp", "72000")
    path, sensor = hw.find_pkg_temp(str(tmp_path))
    assert sensor == "x86_pkg_temp" and path.endswith("thermal_zone4/temp")
    assert hw.find_pkg_temp(str(tmp_path / "none")) == (None, None)


def test_thermal_guard_steps_2w_per_check_from_95_to_under_90_never_below_floor(tmp_path):
    t = tmp_path / "temp"
    g = thermal.Guard(str(t), "x86_pkg_temp")
    tdp = {"w": 17}
    set_w = lambda w: tdp.update(w=w) or True
    check = lambda c: (_w(t, int(c * 1000)), g.check(tdp["w"], 8, set_w))[1]
    assert check(95) is None and tdp["w"] == 17 and not g.tripped        # 95 °C itself does not trip
    assert check(96) == 15 and g.tripped                                 # over 95: −2 W
    assert check(92) == 13 and check(90) == 11                           # still 90 or more: another 2 W each check
    assert check(89.5) is None and not g.tripped and tdp["w"] == 11      # under 90: stops, the step stays
    assert g.status()["line"] == "thermal: stepped to 11 W" and g.status()["temp_c"] == 90
    assert check(93) is None and tdp["w"] == 11                          # 90-95 again does not re-trip
    assert check(99) == 9 and check(99) == 8 and check(99) is None       # the floor, then the SoC's own limit
    assert tdp["w"] == 8
    g.reset()
    assert g.status()["line"] is None and not g.tripped
    assert thermal.Guard(None).check(17, 8, set_w) is None               # no sensor: never acts


def test_autotune_stays_under_the_thermal_step_until_the_user_sets_a_tdp(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, ceiling=25)
    _Game(at, lambda tdp, *_: 2.0 * tdp)                  # every watt buys frames: autotune wants the full 25 W
    for _ in range(3):
        at._step()
    assert s.learned.max_fps == 50 and s.learned.tdp == 25
    assert at.thermal_step(23) and s.learned.tdp == 23 and s.thermal_cap == 23
    assert at.status()["thermal_cap"] == 23 and s.last_action == "thermal: stepped to 23 W"
    for _ in range(30):
        at._step()
        assert s.learned.tdp <= 23                         # never back above the step, whatever the phase
    s.phase = "measure"
    at._remeasure(s, "routine check")                      # a re-measure goes to the step, not the set TDP
    assert s.learned.tdp == 23 and "23 W" in s.last_action
    assert at.set_ceiling(25) == 25 and s.thermal_cap is None   # the user's choice replaces the step
    at.session = None


def test_thermal_step_undoes_a_lever_on_trial(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, ceiling=20)
    undone = []
    s.phase, s.balance_undo = "balance", ("cpu_down", lambda: undone.append(1), 50.0, "lowered CPU ceiling", "−10 %")
    assert at.thermal_step(18) and undone == [1] and s.balance_undo is None and s.learned.tdp == 18
    at.session = None


def _load_main(tmp_path, monkeypatch):
    """main.py with a stand-in for Decky's module (it is only there inside Decky Loader)."""
    import importlib.util
    import types
    d = types.ModuleType("decky")
    for k in ("DECKY_PLUGIN_SETTINGS_DIR", "DECKY_PLUGIN_RUNTIME_DIR", "DECKY_PLUGIN_LOG_DIR", "DECKY_USER_HOME"):
        setattr(d, k, str(tmp_path / k.lower()))
    import logging
    d.logger = logging.getLogger("decky-stub")
    monkeypatch.setitem(sys.modules, "decky", d)
    env = dict(os.environ)
    try:
        spec = importlib.util.spec_from_file_location("clawdeck_main", os.path.join(os.path.dirname(__file__), "..", "main.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    finally:
        os.environ.clear()
        os.environ.update(env)          # main.py drops Decky's loader variables at import; give the tests theirs back
    return m


def test_guard_steps_a_plain_held_tdp_to_the_users_floor_only_while_thermald_is_paused(tmp_path, fake_hw, monkeypatch):
    import asyncio
    m = _load_main(tmp_path, monkeypatch)
    p = m.Plugin.__new__(m.Plugin)
    pw = power.Power(fake_hw)
    held = {"w": 17}
    monkeypatch.setattr(pw, "read_tdp", lambda: held["w"])
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: held.update(w=w) or True)
    t = tmp_path / "pkg_temp"
    _w(t, 97000)
    p.hw, p.power, p.settings = fake_hw, pw, {"autotune_floor": 12}
    p.battery = type("B", (), {"plugged": lambda self: False})()
    p.autotune = autotune.Autotune(fake_hw, pw, "/nonexistent")          # autotune off: no session
    p.guard = thermal.Guard(str(t), "x86_pkg_temp")

    async def run():
        p._power_lock = asyncio.Lock()
        out = []
        pw._thermald_paused = False
        out.append(await p._guard_check())                               # thermald in charge: the guard keeps out
        pw._thermald_paused = True
        for _ in range(4):
            out.append(await p._guard_check())
        return out
    assert asyncio.run(run()) == [None, 15, 13, 12, None]                # 2 W per check, never below the user's 12 W
    assert held["w"] == 12 and p.guard.status()["line"] == "thermal: stepped to 12 W"


# ---- PL1 time window while autotune learns (findings §22.2) ---------------------------------------------------------
TAU_FW = 55_970_000          # what the Claw 8 EX's firmware sets: 55.97 s


def _with_tau(fake_hw, tmp_path):
    t = tmp_path / "powercap/intel-rapl-mmio:0/constraint_0_time_window_us"
    _w(t, TAU_FW)
    fake_hw.paths.rapl_pl1_window = str(t)
    return str(t)


def test_pl1_window_is_found_next_to_pl1(tmp_path, monkeypatch):
    d = tmp_path / "intel-rapl-mmio:0"
    for n, v in (("name", "package-0"), ("constraint_0_name", "long_term"), ("constraint_0_power_limit_uw", 17000000),
                 ("constraint_0_time_window_us", TAU_FW), ("constraint_1_name", "short_term"),
                 ("constraint_1_power_limit_uw", 24000000), ("energy_uj", 1)):
        _w(d / n, v)
    real_glob = hw.glob.glob
    monkeypatch.setattr(hw.glob, "glob", lambda pat: [str(d)] if pat == "/sys/class/powercap/intel-rapl-mmio:[0-9]" else
                        ([] if pat.startswith("/sys/") else real_glob(pat)))
    p = hw.discover_paths()
    assert p.rapl_pl1_window == str(d / "constraint_0_time_window_us") and p.rapl_pl1 == str(d / "constraint_0_power_limit_uw")


def test_pl1_window_set_restore_and_revert(fake_hw, tmp_path):
    t = _with_tau(fake_hw, tmp_path)
    pw = power.Power(fake_hw)
    assert pw.set_pl1_window(8_000_000) and sysfs.read_int(t) == 8_000_000
    assert pw.restore_pl1_window() and sysfs.read_int(t) == TAU_FW
    assert pw.set_pl1_window(8_000_000)
    pw.revert_all()
    assert sysfs.read_int(t) == TAU_FW                               # a revert (game closed, unload) restores it too
    pw.apply_profile("battery", plugged=False)
    assert sysfs.read_int(t) == TAU_FW                               # profile writes outside autotune leave it alone


def test_autotune_learns_on_an_8s_window_and_gives_the_firmwares_back_once_settled(fake_hw, monkeypatch, tmp_path):
    t = _with_tau(fake_hw, tmp_path)
    at, s = _tuner(fake_hw, monkeypatch)
    _Game(at, lambda tdp, *_: min(60.0, 3.0 * tdp))
    at._step()                                                       # first window, measuring
    assert sysfs.read_int(t) == 8_000_000 and s.short_tau and at.status()["learning"]
    steps = 0
    while s.phase != "hold" and steps < 40:
        at._step(); steps += 1
        assert sysfs.read_int(t) == 8_000_000                        # every learning window on sustained power
    assert s.phase == "hold"
    for _ in range(autotune.SETTLED_WINDOWS):
        at._step()
    assert sysfs.read_int(t) == TAU_FW and not s.short_tau           # learned: the 56 s boost window is back
    assert not at.status()["learning"] and at.status()["pl1_window_s"] == 56.0
    at._remeasure(s, "routine check")                                # learning again: short at once
    assert sysfs.read_int(t) == 8_000_000
    at.stop()                                                        # game closed: everything reverts
    assert sysfs.read_int(t) == TAU_FW


def test_autotune_gives_the_window_back_when_paused_and_stops_asking_if_refused(fake_hw, monkeypatch, tmp_path):
    t = _with_tau(fake_hw, tmp_path)
    at, s = _tuner(fake_hw, monkeypatch)
    _Game(at, lambda *_: 50.0)
    at._step()
    assert sysfs.read_int(t) == 8_000_000
    at.pause("CPU ceiling by hand")
    at._step()
    assert sysfs.read_int(t) == TAU_FW and not s.short_tau           # the user has the controls
    at.session = None
    at, s = _tuner(fake_hw, monkeypatch)
    _Game(at, lambda *_: 50.0)
    tries = []
    monkeypatch.setattr(at.power, "set_pl1_window", lambda us: tries.append(us) or False)
    for _ in range(5):
        at._step()
    assert tries == [8_000_000] and s.tau_refused and sysfs.read_int(t) == TAU_FW
    at.session = None


def test_a_saved_result_starts_settled_with_the_firmwares_window(fake_hw, monkeypatch, tmp_path):
    t = _with_tau(fake_hw, tmp_path)
    pw = power.Power(fake_hw)
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: True)
    at = autotune.Autotune(fake_hw, pw, log_dir="/nonexistent")
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    at.start("1", {"tdp": 17, "ceiling": 20, "max_fps": 60}, 8, 20)
    assert at.session.phase == "hold" and sysfs.read_int(t) == TAU_FW
    at.start("2", None, 8, 20)
    assert at.session.phase == "measure" and sysfs.read_int(t) == 8_000_000
    at.stop()
    assert sysfs.read_int(t) == TAU_FW
