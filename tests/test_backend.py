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



def test_sampler_reads_only_the_running_games_mangohud_log(fake_hw, tmp_path):
    """After a plugin restart the newest log was another game's, read whole: Enshrouded's fps for the next game
    (2026-11-12). Only a log written since the game started counts, and it is found the second it appears."""
    old = tmp_path / "enshrouded_2026.csv"
    old.write_text("fps,frametime\n50,20.0\n")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    smp = autotune.Sampler(fake_hw, str(tmp_path))
    smp.set_prefix(str(tmp_path / "igdext_xell.log"))           # a game starts
    assert smp.frametime() == (None, None)                       # not Enshrouded's 50 fps
    (tmp_path / "sinking_star_2026.csv").write_text("fps,frametime\n120,8.3\n")
    smp._log_checked -= 1.0                                      # a second later
    assert smp.frametime() == (8.3, 120.0)


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
    # 40 fps once was measured again, not acted on: the next window held the line, so lowering went on
    assert s.learned.tdp < 18 and s.phase == "balance" and s.last_delta.startswith("−")
    at.session = None


def test_autotune_keeps_a_cpu_gpu_lever_only_if_it_helps(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch)
    # GPU-bound: a higher GPU floor helps once (+4 %), the second step doesn't and is undone. The CPU ceiling is never
    # trimmed any more (no proven tool does it; it hurts frame pacing)
    _Game(at, lambda tdp, cpu, gmin, *_: (52.0 if gmin else 50.0) if tdp == 20 else 30.0, cpu_pct=35, gpu_busy=97)
    for _ in range(3):
        at._step()
    assert s.learned.max_fps == 50
    at._step()
    assert s.learned.gpu_min_mhz and "trying" in s.last_action and "GPU floor" in s.last_action
    first = s.learned.gpu_min_mhz
    at._step()                                                  # +4 %: kept; the next step is tried
    assert s.learned.max_fps == 52 and s.learned.gpu_min_mhz == first + autotune.GPU_STEP_MHZ
    at._step()                                                  # no gain: undone
    assert s.learned.gpu_min_mhz == first and "undone" in s.last_action
    assert s.learned.cpu_max_pct == 100
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
    for _ in range(14):
        at._step()
    assert s.phase == "hold" and s.learned.tdp == 19
    fps["v"] = 40.0                                             # a much heavier area: watts back at once
    at._step(); at._step()
    assert s.learned.tdp == 20 and "at once" in s.last_action
    for _ in range(4):
        at._step()
    assert s.learned.max_fps == 40.0 and s.learned.tdp == 20    # ... and the new area is measured
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
    t = s.learned.tdp
    assert t < 20                                               # it lowered (a 3 W step from the top)
    at.pause()
    assert s.learned.tdp == 20                                  # the set TDP again, not autotune's
    for _ in range(5):
        at._step()
    assert s.learned.tdp == 20
    assert at.stop(revert=False)["learned"]["tdp"] == t         # what it had found is what the game's profile gets


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


def _hot_guard(tmp_path, monkeypatch):
    """A guard with a package temperature and a RAPL energy counter, 10 s between checks."""
    t, e = tmp_path / "temp", tmp_path / "energy_uj"
    _w(e, 0)
    clock = {"t": 0.0, "uj": 0}
    monkeypatch.setattr(thermal.time, "monotonic", lambda: clock["t"])
    g = thermal.Guard(str(t), "x86_pkg_temp", str(e))

    def check(c, draw_w, tdp, set_w, cap=None, limit=None):
        clock["t"] += 10
        clock["uj"] += int(draw_w * 10 * 1e6)
        _w(t, int(c * 1000))
        _w(e, clock["uj"])
        return g.check(tdp["w"], 8, set_w, limit_w=limit, set_clock=cap)
    return g, check


def test_thermal_guard_caps_the_cpu_clock_for_a_hot_spot_under_the_limit(tmp_path, monkeypatch):
    """xemu: two busy threads at 95-97 °C while the package draws ~20 W of a 30 W limit. Stepping the TDP did nothing
    until it was under 20 W, and 2 W every 10 s took it to 10 W in 90 s (2026-11-14), the game at a third of its
    speed. A hot spot under the limit gets the CPU's top clock; the TDP stays."""
    g, check = _hot_guard(tmp_path, monkeypatch)
    tdp, caps = {"w": 30}, []
    set_w = lambda w: tdp.update(w=w) or True
    cap = lambda pct: caps.append(pct) or True
    assert check(80, 20, tdp, set_w, cap) is None                       # the first reading: no draw yet
    assert check(96, 20, tdp, set_w, cap) is None and caps == [90] and tdp["w"] == 30
    for _ in range(3):
        check(96, 20, tdp, set_w, cap)
    assert caps == [90, 80, 70, 60] and g.clock_pct == 60 and tdp["w"] == 30    # not under CLOCK_FLOOR_PCT
    assert g.status()["line"] == "thermal: CPU clock 60 %"
    # clock at its floor and still hot: now the TDP, from what the package draws, not from the 30 W set
    caps.clear()
    assert check(96, 20, tdp, set_w, cap) == 18 and caps == []


def test_thermal_guard_steps_watts_from_the_draw_and_waits_for_each_step(tmp_path, monkeypatch):
    """No hot spot (the package draws its limit): the TDP steps from the draw, and the next step waits until the
    package came down to the last one (PL1 is averaged over up to a minute)."""
    g, check = _hot_guard(tmp_path, monkeypatch)
    tdp = {"w": 25}
    set_w = lambda w: tdp.update(w=w) or True
    check(80, 25, tdp, set_w)
    assert check(97, 24.6, tdp, set_w) == 22                            # 24 W drawn: 22 W
    assert check(96, 24.0, tdp, set_w) is None and tdp["w"] == 22       # still drawing 24 W: wait
    assert check(96, 22.4, tdp, set_w) == 20                            # it came down: the next step
    assert check(89, 20, tdp, set_w) is None and not g.tripped and tdp["w"] == 20


def test_thermal_guard_gives_the_clock_back_first_then_the_watts(tmp_path, monkeypatch):
    g, check = _hot_guard(tmp_path, monkeypatch)
    tdp, caps = {"w": 30}, []
    set_w = lambda w: tdp.update(w=w) or True
    cap = lambda pct: caps.append(pct) or True
    check(80, 20, tdp, set_w, cap, limit=30)
    check(96, 20, tdp, set_w, cap, limit=30)
    check(96, 20, tdp, set_w, cap, limit=30)
    assert g.clock_pct == 80
    check(75, 12, tdp, set_w, cap, limit=30)                            # under 90 °C: the trip ends, the cap stays
    assert not g.tripped and g.clock_pct == 80
    check(85, 12, tdp, set_w, cap, limit=30)                            # 80-90 °C: not cool, no step back
    assert g.clock_pct == 80
    check(75, 12, tdp, set_w, cap, limit=30)                            # cool: a step back at each check
    assert g.clock_pct == 90 and caps[-1] == 90
    check(75, 12, tdp, set_w, cap, limit=30)
    assert g.clock_pct is None and caps[-1] is None and g.status()["line"] is None


def test_thermal_guard_takes_no_clock_step_while_the_last_one_shows_and_gives_the_clock_back_at_once(tmp_path, monkeypatch):
    """Project Gotham Racing 2 in xemu, 2026-11-16, the guard's own readings 10 s apart: 97, 97, 91, 79, 72, 69 °C. The
    cap went 90, 80, 70 % (the last one at 91 °C, 6 °C down in 10 s), and came back a step per 30 s under 80 °C: 160 s
    from the trip to no cap, at 68-77 °C for 140 s of it."""
    g, check = _hot_guard(tmp_path, monkeypatch)
    tdp, caps = {"w": 30}, []
    set_w = lambda w: tdp.update(w=w) or True
    cap = lambda pct: caps.append(pct) or True
    check(94, 19, tdp, set_w, cap, limit=30)
    for c in (97, 97, 91, 79, 72, 69):
        check(c, 15, tdp, set_w, cap, limit=30)
    assert caps == [90, 80, 90, None] and g.clock_pct is None and tdp["w"] == 30    # no cap 50 s after the trip
    # between 90 and 95 °C and not cooler than at the check before: the last step did not do it
    for c in (97, 93, 93):
        check(c, 19, tdp, set_w, cap, limit=30)
    assert caps[4:] == [90, 80]


def test_cpu_clock_cap_holds_under_every_other_write_and_lets_it_back(fake_hw):
    """Autotune and profiles write the CPU's top clock too: under the guard's cap the chip gets the lower value, and
    what they asked for comes back when the cap goes."""
    pw = power.Power(fake_hw)
    p = fake_hw.paths.cpu_max_perf_pct
    assert pw.cap_cpu_max_pct(80) and sysfs.read_int(p) == 80               # nothing asked: the chip's 100 under 80
    assert pw.set_cpu_max_pct(95) and sysfs.read_int(p) == 80
    assert pw.set_cpu_max_pct(70) and sysfs.read_int(p) == 70
    assert pw.set_cpu_max_pct(90) and pw.cap_cpu_max_pct(None) and sysfs.read_int(p) == 90
    pw.cap_cpu_max_pct(60)
    pw.revert_all()
    assert pw.cpu_cap is None and sysfs.read_int(p) == 100


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
    p.running_appid, p.profiles = None, profiles.Profiles(str(tmp_path / "profiles.json"))
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


def test_thermal_csv_gets_a_row_per_guard_check_and_none_once_thermald_is_back(tmp_path, fake_hw, monkeypatch):
    """No timer of its own (battery first): the rows come with the guard's checks, which run only while thermald is
    paused."""
    import asyncio
    import csv
    m = _load_main(tmp_path, monkeypatch)
    p = m.Plugin.__new__(m.Plugin)
    pw = power.Power(fake_hw)
    monkeypatch.setattr(pw, "read_tdp", lambda: 17)
    t = tmp_path / "pkg_temp"
    _w(t, 61000)
    p.hw, p.power, p.settings = fake_hw, pw, {"autotune_floor": 12}
    p.running_appid, p.profiles = None, profiles.Profiles(str(tmp_path / "profiles.json"))
    p.battery = type("B", (), {"plugged": lambda self: False})()
    p.autotune = autotune.Autotune(fake_hw, pw, "/nonexistent")
    p.guard = thermal.Guard(str(t), "x86_pkg_temp")
    p.thermal_log = thermal.Recorder(str(tmp_path / "thermal.csv"))
    monkeypatch.setattr(m.thermal, "CHECK_S", 0)
    checks = {"n": 0}
    real = pw.thermald_paused

    def paused():                                      # thermald comes back after two checks
        return real() and checks["n"] < 2
    monkeypatch.setattr(pw, "thermald_paused", paused)

    async def run():
        p._power_lock = asyncio.Lock()
        orig = p._guard_check

        async def counted():
            checks["n"] += 1
            return await orig()
        p._guard_check = counted
        pw._thermald_paused = True
        await p._guard_loop()
    asyncio.run(run())
    with open(tmp_path / "thermal.csv", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == thermal.CSV_FIELDS and [r[1:5] for r in rows[1:]] == [["61", "", "17", ""], ["61", "", "17", ""]]


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


# ---- GPU efficiency mode: xe power_profile (findings §22.3) --------------------------------------------------------
def _with_power_profile(fake_hw, monkeypatch, active="base", offered=("base", "power_saving")):
    """A power_profile file that behaves like xe's: reads "[base] power_saving", takes one word."""
    path = os.path.join(os.path.dirname(fake_hw.paths.gpu_min_freq), "power_profile")
    _w(path, active)
    fake_hw.paths.gpu_power_profile = path
    real_read = sysfs.read

    def read(p, default=None):
        if p != path:
            return real_read(p, default)
        word = real_read(p)
        return " ".join(f"[{o}]" if o == word else o for o in offered)
    monkeypatch.setattr(sysfs, "read", read)
    return path


def test_gpu_power_profile_is_discovered_with_the_clock_files(tmp_path, monkeypatch):
    card = tmp_path / "card0"
    f0 = card / "device/tile0/gt0/freq0"
    for n in ("min_freq", "max_freq", "power_profile"):
        _w(f0 / n, "1")
    _w(card / "device/vendor", "0x8086")
    real_glob, real_isdir = hw.glob.glob, hw.os.path.isdir
    monkeypatch.setattr(hw.glob, "glob", lambda pat: [str(card)] if pat == "/sys/class/drm/card[0-9]" else
                        ([] if pat.startswith("/sys/") else real_glob(pat)))
    assert hw.discover_paths().gpu_power_profile == str(f0 / "power_profile")
    assert real_isdir(f0)


def test_gpu_power_profile_set_revert_and_the_profiles(fake_hw, monkeypatch):
    path = _with_power_profile(fake_hw, monkeypatch)
    pw = power.Power(fake_hw)
    assert pw.gpu_power_profile() == ("base", ["base", "power_saving"])
    assert pw.set_gpu_power_profile("power_saving") and pw.status()["gpu"]["power_profile"] == "power_saving"
    pw.revert_all()
    assert open(path).read().strip() == "base"                      # the active word goes back, not "[base] ..."
    with pytest.raises(ValueError):
        pw.set_gpu_power_profile("turbo")
    for name, want in (("saver", "power_saving"), ("battery", "power_saving"), ("balanced", "base"),
                       ("performance", "base"), ("max", "base")):
        r = pw.apply_profile(name, plugged=True)
        assert r["applied"].get("gpu_power_profile") == want and pw.gpu_power_profile()[0] == want, name
    pw.revert_all()
    assert pw.gpu_power_profile()[0] == "base"


def test_a_kernel_without_power_saving_is_left_alone(fake_hw, monkeypatch):
    _with_power_profile(fake_hw, monkeypatch, offered=("base",))
    r = power.Power(fake_hw).apply_profile("saver", plugged=False)
    assert "gpu_power_profile" not in r["applied"] and "gpu_power_profile" not in r["failed"]


def test_autotune_gpu_bound_turns_efficiency_mode_off_before_any_floor(fake_hw, monkeypatch):
    _with_power_profile(fake_hw, monkeypatch, active="power_saving")
    at, s = _tuner(fake_hw, monkeypatch)
    _Game(at, lambda *_: 40.0, gpu_mhz=2400, gpu_max_mhz=2400, gpu_busy=98)     # GPU-bound
    s.learned.bound = "gpu"
    assert at._levers(s)[0][0] == "gpu_base"
    for _ in range(4):
        at._step()
    assert "gpu_base" in s.balance_tried and at.power.gpu_power_profile()[0] == "base"    # tried first
    assert s.last_delta == "GPU efficiency off"
    at.session = None


def test_autotune_tries_efficiency_mode_at_the_floor_and_keeps_it_only_on_the_line(fake_hw, monkeypatch):
    _with_power_profile(fake_hw, monkeypatch)
    at, s = _tuner(fake_hw, monkeypatch, ceiling=9)
    fps = {"v": 60.0}
    _Game(at, lambda *_: fps["v"])
    for _ in range(3):
        at._step()                                                   # measured: 60 at 9 W
    for _ in range(2):
        at._step()                                                   # lowered to the 8 W floor, still on the line
    assert s.learned.tdp == 8 and s.phase == "hold" and s.eff_trial
    assert at.power.gpu_power_profile()[0] == "power_saving" and s.last_delta == "GPU efficiency on"
    at._step()                                                       # 60 fps held: kept
    assert at.power.gpu_power_profile()[0] == "power_saving" and s.learned.gpu_profile == "power_saving"
    assert "kept" in s.last_action
    for _ in range(3):
        at._step()
    assert not s.eff_trial and at.power.gpu_power_profile()[0] == "power_saving"   # tried once, not every window
    at.session = None
    # a game that loses frames with it: undone after one window
    at, s = _tuner(fake_hw, monkeypatch, ceiling=9)
    at.power.set_gpu_power_profile("base")
    seq = iter([60.0] * 5 + [50.0])
    _Game(at, lambda *_: next(seq))
    for _ in range(6):
        at._step()
    assert at.power.gpu_power_profile()[0] == "base" and "undone" in s.last_action
    at.session = None


def test_autotune_short_in_hold_and_gpu_bound_turns_efficiency_off_before_adding_watts(fake_hw, monkeypatch):
    _with_power_profile(fake_hw, monkeypatch, active="power_saving")
    at, s = _tuner(fake_hw, monkeypatch, ceiling=20)
    s.phase, s.learned.max_fps, s.learned.fps, s.learned.tdp = "hold", 60.0, 58.0, 15
    s.learned.bound = "gpu"
    s.measured_at = time.monotonic()
    _Game(at, lambda *_: 54.0, gpu_mhz=2400, gpu_max_mhz=2400, gpu_busy=98)
    at._step(); at._step()
    assert at.power.gpu_power_profile()[0] == "base" and s.learned.tdp == 15 and s.last_delta == "GPU efficiency off"
    at.session = None


# ---- efficiency sweep tool (findings §22.1): TDP stepping + CSV; the run itself is manual -------------------------
def _sweep_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("efficiency_sweep", os.path.join(os.path.dirname(__file__), "..", "tools", "efficiency_sweep.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_efficiency_sweep_steps_the_tdp_writes_a_row_per_step_and_reverts(fake_hw, monkeypatch, tmp_path):
    import csv as _csv
    es = _sweep_module()
    _with_power_profile(fake_hw, monkeypatch)
    energy = tmp_path / "energy_uj"
    _w(energy, 0)
    temp = tmp_path / "pkg_temp"
    _w(temp, 71000)
    fake_hw.paths.rapl_energy, fake_hw.paths.pkg_temp = str(energy), str(temp)
    logs = tmp_path / "mangohud"
    log = logs / "game_2026.csv"
    _w(log, "os,cpu,gpu\nfps,frametime,cpu_load\n")
    pw = power.Power(fake_hw)
    held = {"w": None}
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: held.update(w=w) or True)
    curve = {8: 30.0, 12: 42.0, 17: 50.0, 20: 50.5}                 # flattens past 17 W
    clock = {"t": 0.0}
    monkeypatch.setattr(es.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(autotune.time, "monotonic", lambda: clock["t"])

    def tick(_s):                                                   # one second of game: a MangoHud row, energy at TDP
        clock["t"] += 1.0
        w = held["w"]
        with open(log, "a") as f:
            f.write(f"{curve[w]},{1000 / curve[w]:.2f},50\n")
        _w(energy, sysfs.read_int(str(energy)) + int(w * 0.9 * 1e6))
    smp = autotune.Sampler(fake_hw, str(logs))
    out = tmp_path / "out/ptl-efficiency-test.csv"
    said, asked = [], []
    rows = es.sweep(fake_hw, pw, smp, [8, 12, 17, 20], 5, "test", str(out), power_saving_points=[12],
                    ask=asked.append, say=said.append, sleep=tick)
    assert [(r["tdp_w"], r["gpu_profile"]) for r in rows] == [(8, "base"), (12, "base"), (17, "base"), (20, "base"), (12, "power_saving")]
    assert rows[0]["avg_fps"] == 30.0 and rows[0]["samples"] == 5 and rows[0]["pkg_w"] == 7.2 and rows[0]["pkg_temp_c"] == 71.0
    assert rows[0]["fps_per_w"] == round(30 / 7.2, 2) and len(asked) == 10                 # warm-up + measured per step
    with open(out) as f:
        got = list(_csv.DictReader(f))
    assert len(got) == 5 and list(got[0]) == es.COLUMNS and got[4]["gpu_profile"] == "power_saving"
    assert any(s.startswith("\nknee: 17 W") for s in said)                                  # +3 W past 17 buys 1 %
    pw.revert_all()
    assert pw.gpu_power_profile()[0] == "base"


def test_efficiency_sweep_knee_and_percentile():
    es = _sweep_module()
    assert es.percentile([10, 20, 30, 40, 50], 0) == 10 and es.percentile([], 1) is None
    rows = [{"tdp_w": 10, "avg_fps": 40}, {"tdp_w": 12, "avg_fps": 48}, {"tdp_w": 14, "avg_fps": 49}]
    assert es.knee(rows)["tdp_w"] == 12
    assert es.knee([{"tdp_w": 10, "avg_fps": 40}, {"tdp_w": 12, "avg_fps": 50}])["tdp_w"] == 12   # never flattens: the top
    assert es.knee([]) is None


# ---- autotune efficiency (maintainer 2026-11-04: lose 1-2 fps, save 5-10 W) ------------------------------------------
def _run(at, n):
    for _ in range(n):
        at._step()


def test_autotune_plateau_goes_low_with_big_steps(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, ceiling=35)
    # a plateau: 60 fps at 35 W, 59 at 22 W, then steep below (3 fps per watt)
    _Game(at, lambda tdp, *_: 60.0 - (35 - tdp) / 13.0 if tdp >= 22 else 59.0 - 3.0 * (22 - tdp))
    steps = []
    orig = at._did
    at._did = lambda sess, a, d: (steps.append(d), orig(sess, a, d))[1]
    _run(at, 80)
    assert s.learned.tdp <= 23 and s.phase == "hold"
    assert s.learned.fps >= 57                                  # lost at most ~3 fps (5 % of 60)
    assert "−3 W" in steps                                      # big steps while clearly above the line
    st = at.status()
    assert st["saves_w"] and st["saves_w"] >= 12
    at.session = None


def test_autotune_steep_curve_stops_early(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, ceiling=35)
    _Game(at, lambda tdp, *_: min(60.0, 2.5 * tdp))            # 60 fps needs 24 W; every watt below costs 2.5 fps
    _run(at, 80)
    assert 23 <= s.learned.tdp <= 25 and s.learned.fps >= 57 and s.phase == "hold"
    at.session = None


def test_autotune_low_fps_game_keeps_two_fps(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, ceiling=30)
    _Game(at, lambda tdp, *_: min(30.0, 1.0 * tdp + 8))         # 30 fps from 22 W, -1 fps per watt below
    _run(at, 80)
    # 5 % of 30 is 1.5 fps, so the 2 fps rule decides: 28 fps at 20 W is the lowest that holds
    assert s.learned.tdp == 20 and s.phase == "hold"
    at.session = None


def test_autotune_frame_limited_holds_the_limit_at_the_floor(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, ceiling=35)
    _Game(at, lambda tdp, *_: 40.0 if tdp >= 12 else 40.0 - 4 * (12 - tdp))   # a 40 fps limit, reached from 12 W
    _run(at, 80)
    assert s.learned.tdp >= s.floor_tdp and s.learned.tdp <= 13 and s.learned.fps >= 38
    at.session = None


# ---- the power path end to end (maintainer, 2026-11-04): your pick, Autotune under it, back to the pick on exit -----
FW_PL1, FW_PL2 = 25_000_000, 37_000_000      # what the firmware holds before ClawDeck (boot values)


@pytest.fixture
def plugin(fake_hw, tmp_path, monkeypatch):
    """main.Plugin with real Power / Autotune / Profiles on a fake RAPL tree, a fake thermald and a fake charger.
    No background tasks: autotune's loop and the guard's are not started, the tests drive autotune's steps."""
    from clawdeck import power as pw_mod
    from clawdeck import quiet
    m = _load_main(tmp_path, monkeypatch)
    quiet_calls = []
    monkeypatch.setattr(quiet, "background", lambda on, *a: quiet_calls.append(on) or True)
    root = tmp_path / "rapl"
    _w(str(root / "constraint_0_power_limit_uw"), FW_PL1)
    _w(str(root / "constraint_1_power_limit_uw"), FW_PL2)
    fake_hw.paths.rapl_pl1 = str(root / "constraint_0_power_limit_uw")
    fake_hw.paths.rapl_pl2 = str(root / "constraint_1_power_limit_uw")
    thermald = {"active": True}

    class R:
        def __init__(self, out=""): self.returncode, self.stdout, self.stderr = 0, out, ""

    def systemctl(*args):
        if args[0] == "is-active":
            return R("active" if thermald["active"] else "inactive")
        thermald["active"] = args[0] == "start"
        return R()
    monkeypatch.setattr(pw_mod, "_systemctl", systemctl)
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    p = m.Plugin.__new__(m.Plugin)
    p.hw = fake_hw
    p.power = power.Power(fake_hw)
    ac = {"plugged": False}
    p.battery = type("B", (), {"plugged": lambda self: ac["plugged"]})()
    p.profiles = profiles.Profiles(str(tmp_path / "profiles.json"))
    p.autotune = autotune.Autotune(fake_hw, p.power, "/nonexistent")
    p.settings = {"autotune": True, "autotune_plugged": True}
    p._save_settings = lambda: None
    p.running_appid, p.running_plugged, p._holding_pick = None, None, False
    p.running_key, p.running_title, p._frontend_watch, p._frontend_seen = None, None, None, None
    p.guard = thermal.Guard(None, None)
    p._guard_task = None

    import asyncio
    p._power_lock = asyncio.Lock()
    p.ac, p.thermald = ac, thermald
    p.run = lambda coro: asyncio.run(coro)
    p.chip = lambda: p.power.read_tdp()
    yield p
    p.autotune.session = None
    sysfs.revert_all()


def _lower(p, steps=30):
    """Autotune runs for a while in a game that gets 60 fps from 15 W up (4 fps per watt below)."""
    s = p.autotune.session
    s.settle = 1
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp))
    for _ in range(steps):
        p.autotune._step()
    return s


def _frontend_event(p, event, system, rom, name):
    """What ClawDeck's ES-DE script writes on game-start / game-end (frontend.py)."""
    home = p.game_started.__globals__["HOME"]
    path = os.path.join(home, ".local/share/clawdeck/frontend-game")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{event}\n{system}\n{rom}\n{name}\n")
    t = time.time() + len(getattr(p, "_events", [])) + 1        # a new mtime for every event
    p._events = getattr(p, "_events", []) + [t]
    os.utime(path, (t, t))
    return p.run(p._frontend_poll())


def test_each_game_an_emulator_frontend_starts_has_its_own_power_and_autotune(plugin):
    """ES-DE is one Steam shortcut: an NES ROM, a PS3 game and a Switch game all ran at ES-DE's limit, with one Autotune
    session that had no frame rate (maintainer, 2026-11-14). With ES-DE's game-start/end events each one is its own
    game: its own pick and learning, a new one starting at ES-DE's pick (never with ES-DE's learning), ES-DE's menus at
    ES-DE's own pick."""
    p = plugin
    esde, demons, ratchet = "3119184808", "3119184808#ps3/Demon's Souls", "3119184808#ps3/Ratchet"
    p.profiles.set(esde, False, {"base": "custom", "tdp": 12})        # ES-DE's own pick: 12 W in its menus
    p.run(p.game_started(esde))
    assert p.chip() == 12 and p.autotune.session.appid == esde
    assert _frontend_event(p, "start", "ps3", "/roms/ps3/Demon's Souls.iso", "Demon's Souls")
    assert p.running_key == demons and p.running_appid == esde and p.autotune.session.appid == demons
    assert p.chip() == 12                                              # never played: starts at ES-DE's pick
    p.run(p.set_tdp(18))                                               # a pick in this game is this game's
    assert p.profiles.own(demons, False)["tdp"] == 18 and p.profiles.own(esde, False)["tdp"] == 12
    slot = p.run(p.power_slot(esde))
    assert slot["title"] == "Demon's Souls" and slot["tdp"] == 18
    assert p.run(p.get_autotune())["game"] == esde                     # Steam's app: the panel doesn't report it again
    assert _frontend_event(p, "end", "ps3", "/roms/ps3/Demon's Souls.iso", "Demon's Souls")
    assert p.running_key == esde and p.chip() == 12 and p.autotune.session.appid == esde
    p.profiles.set(esde, False, {"learned": {"tdp": 9, "ceiling": 12, "max_fps": 200}})   # Autotune's result in ES-DE's menus
    assert _frontend_event(p, "start", "ps3", "/roms/ps3/Ratchet.iso", "Ratchet")
    assert p.running_key == ratchet and p.chip() == 12                 # a new game: ES-DE's pick, not its learned 9 W
    assert _frontend_event(p, "start", "ps3", "/roms/ps3/Ratchet.iso", "Ratchet") is None   # the same game: no restart
    p.run(p.game_stopped(esde))
    assert p.running_key is None and p.running_title is None


def test_a_frontend_game_has_no_launch_file_of_its_own(plugin, monkeypatch):
    """At start, every saved game's launch file is rewritten; a game an emulator frontend started launches as the
    frontend, so it has none ("launch file not rewritten: appid must be numeric" at each start, 2026-11-14)."""
    p = plugin
    p.profiles.set("3119184808#ps3/Ratchet", False, {"base": "custom", "tdp": 12})
    p.profiles.set("42", False, {"base": "custom", "tdp": 12})
    seen = []
    monkeypatch.setattr(p, "_write_launch", lambda appid: seen.append(appid) or {})
    p._refresh_game_launches()
    assert "42" in seen and "3119184808#ps3/Ratchet" not in seen


def test_a_frontend_event_from_before_the_game_started_is_ignored(plugin):
    p = plugin
    esde = "3119184808"
    _frontend_event(p, "start", "nes", "/roms/nes/Mario.nes", "Mario")     # left over from an earlier ES-DE session
    p.run(p.game_started(esde))
    assert p.run(p._frontend_poll()) is None and p.running_key == esde
    p.run(p.game_stopped(esde))


def test_game_exit_puts_your_pick_back_not_the_firmwares_limits(plugin):
    p = plugin
    r = p.run(p.set_tdp(20))                                       # the slider in the menus, no game
    assert r["ok"] and p.chip() == 20 and not p.thermald["active"]
    p.run(p.game_started("42"))
    assert p.chip() == 20 and p.autotune.session.ceiling_tdp == 20
    s = _lower(p)
    assert s.learned.tdp < 20 and p.chip() == s.learned.tdp       # Autotune uses less than your 20 W
    p.run(p.game_stopped("42"))
    assert p.chip() == 20                                          # your limit, not the firmware's 25 W
    assert sysfs.read_int(p.hw.paths.rapl_pl1) == 20_000_000
    assert p.profiles.own("42", False)["learned"]["tdp"] < 20      # what it learned is kept for the next start
    p.run(p._unload())                                             # unloading still reverts everything
    assert sysfs.read_int(p.hw.paths.rapl_pl1) == FW_PL1 and p.thermald["active"]


def test_never_picked_on_this_power_source_leaves_the_firmware_and_thermald(plugin):
    p = plugin
    p.run(p.game_started("42"))                                    # default for battery: Battery 12 W
    assert p.chip() == 12 and not p.thermald["active"]
    _lower(p, 5)
    p.run(p.game_stopped("42"))
    assert sysfs.read_int(p.hw.paths.rapl_pl1) == FW_PL1 and p.thermald["active"]


def test_status_shows_your_limit_and_what_autotune_uses(plugin):
    p = plugin
    p.settings["last_profile_battery"] = "max"                     # 35 W
    p.run(p.game_started("42"))
    s = _lower(p)
    at = p.run(p.get_autotune())
    st = p.run(p.get_power())
    slot = p.run(p.power_slot("42"))
    assert at["ceiling_tdp"] == 35 and at["tdp"] == s.learned.tdp < 35 and at["plugged"] is False
    assert st["limit_w"] == 35 and st["autotune_w"] == at["tdp"] == st["tdp_w"]
    assert slot["pick"] == {"profile": "max", "tdp": 35, "from": "last"} and slot["running"]


def test_a_pick_in_a_game_is_the_new_limit_at_once_and_comes_back_at_the_next_start(plugin):
    p = plugin
    p.run(p.game_started("7"))
    s = _lower(p)
    assert s.ceiling_tdp == 12 and p.chip() == s.learned.tdp
    p.autotune.pause("manual CPU ceiling")
    r = p.run(p.apply_profile("balanced", "7"))
    s = p.autotune.session
    assert r["ok"] and p.chip() == 17 and r["limit_w"] == 17 and r["autotune_w"] == 17
    assert s.ceiling_tdp == 17 and s.learned.tdp == 17 and s.phase == "measure" and not s.paused
    assert s.learned.max_fps is None                               # measured again from the new limit
    r = p.run(p.set_tdp(26))                                       # the slider, in the game
    assert r["ok"] and p.chip() == 26 and p.autotune.session.ceiling_tdp == 26
    assert p.profiles.own("7", False)["base"] == "custom" and p.profiles.own("7", False)["tdp"] == 26
    p.run(p.game_stopped("7"))
    assert p.chip() == 26                                          # the last pick on battery
    p.run(p.apply_profile("saver"))                                # a different pick for all games, in the menus
    assert p.chip() == 10
    p.run(p.game_started("7"))
    assert p.chip() == 26 and p.autotune.session.ceiling_tdp == 26  # the game's own pick
    p.run(p.game_stopped("7"))
    assert p.chip() == 10


def test_use_default_follows_the_last_pick_again(plugin):
    p = plugin
    p.settings["last_profile_battery"] = "balanced"
    p.run(p.game_started("7"))
    p.run(p.set_tdp(22))
    assert p.chip() == 22
    p.settings["last_profile_battery"] = "balanced"                # as if picked again in the menus
    r = p.run(p.reset_power_slot("7"))
    assert r["ok"] and p.chip() == 17 and p.autotune.session.ceiling_tdp == 17


def test_unplugging_during_and_after_a_game_follows_each_power_sources_pick(plugin):
    p = plugin
    p.ac["plugged"] = True
    p.run(p.apply_profile("max"))                                  # plugged in: Max 35 W
    p.ac["plugged"] = False
    p.run(p.power_source_changed(False))
    p.run(p.set_tdp(20))                                           # on battery: 20 W
    assert p.chip() == 20
    p.run(p.game_started("42"))
    _lower(p, 10)
    p.autotune.session.settled = True                              # it finished learning on battery
    p.ac["plugged"] = True
    r = p.run(p.power_source_changed(True))
    assert r["changed"] and p.chip() == 35 and p.autotune.session.ceiling_tdp == 35 and p.autotune.plugged
    assert p.profiles.own("42", False).get("learned")              # battery's learning kept in its own slot
    p.run(p.game_stopped("42"))
    assert p.chip() == 35                                          # exit on the charger: the plugged-in pick
    p.ac["plugged"] = False
    r = p.run(p.power_source_changed(False))
    assert r["changed"] and p.chip() == 20                         # unplugged with no game: the battery pick
    r = p.run(p.resumed())
    assert r["reapplied"] and p.chip() == 20


def test_autotune_skips_loading_screens_and_menus(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch)
    seq = iter([60, 60, 60, 12, 61, 200, 60, 60])              # a loading screen (12) and an uncapped menu (200)
    _Game(at, lambda *_: float(next(seq)))
    for _ in range(3):
        at._step()
    assert s.learned.max_fps == 60
    tdp = s.learned.tdp
    at._step()                                                  # 12 fps: skipped, nothing changes
    assert s.learned.tdp == tdp and s.skips == 1
    at._step(); at._step()                                      # 61 judged (skips reset), 200 skipped
    assert s.skips == 1 and s.phase != "measure"
    at.session = None


def test_autotune_three_odd_windows_in_a_row_are_the_game(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch)
    _Game(at, lambda *_: 60.0)
    for _ in range(3):
        at._step()
    _Game(at, lambda *_: 15.0)                                  # settings turned way up: it stays at 15
    for _ in range(3):
        at._step()
    assert s.skips == 0                                         # the third is used, not skipped
    at.session = None


def test_autotune_saves_only_a_finished_result(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch)
    _Game(at, lambda tdp, *_: min(60.0, 3.0 * tdp))
    for _ in range(5):
        at._step()
    r = at.stop(revert=False)
    assert r["settled"] is False and "still learning" in r["summary"]
    at, s = _tuner(fake_hw, monkeypatch)
    _Game(at, lambda tdp, *_: min(60.0, 3.0 * tdp))
    for _ in range(25):
        at._step()
    r = at.stop(revert=False)
    assert r["settled"] and r["summary"].startswith("Autotune saved 19 W of your 20 W") and "saves 1 W" in r["summary"]


def test_quiet_moves_only_this_process_to_the_efficiency_cores(tmp_path, monkeypatch):
    from clawdeck import quiet
    atom = tmp_path / "cpus"
    atom.write_text("2-7\n")
    for c in (2, 3):                                            # E-cores on the L3; 4-7 have none: LP-E
        (tmp_path / "sys" / f"cpu{c}" / "cache" / "index3").mkdir(parents=True)
    calls = []
    monkeypatch.setattr(quiet.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(quiet, "_threads", lambda: [11, 12])
    monkeypatch.setattr(quiet.os, "sched_setaffinity", lambda t, c: calls.append(("aff", t, sorted(c))))
    monkeypatch.setattr(quiet.os, "setpriority", lambda w, t, n: calls.append(("nice", t, n)))
    monkeypatch.setattr(quiet, "_on", False)
    assert quiet.background(True, str(atom), str(tmp_path / "sys"))
    assert ("aff", 11, [4, 5, 6, 7]) in calls and ("nice", 12, 10) in calls
    assert not quiet.background(True, str(atom), str(tmp_path / "sys"))                # already there: nothing again
    calls.clear()
    assert quiet.background(False, str(atom), str(tmp_path / "sys"))
    assert ("aff", 11, list(range(8))) in calls and ("nice", 11, 0) in calls


def test_thermal_guard_steps_back_up_to_your_limit_once_cool(tmp_path):
    t = tmp_path / "temp"
    g = thermal.Guard(str(t), "x86_pkg_temp")
    tdp = {"w": 35}
    set_w = lambda w: tdp.update(w=w) or True
    check = lambda c: (_w(t, int(c * 1000)), g.check(tdp["w"], 8, set_w, limit_w=35))[1]
    assert check(97) == 33 and check(93) == 31 and check(85) is None     # hot, then under 90: stops at 31 W
    assert check(85) is None and check(85) is None and tdp["w"] == 31    # 80-90: holds, no climbing yet
    assert check(70) is None and check(70) is None and check(70) == 33    # 30 s under 80: +2 W
    assert g.status()["line"] == "thermal: stepped to 33 W"
    assert check(70) is None and check(81) is None and check(70) is None  # a warm check starts the count over
    assert check(70) is None and check(70) == 35 and g.stepped_w is None  # back at your limit: the step is gone
    assert g.status()["line"] is None
    assert check(60) is None and tdp["w"] == 35                           # nothing to recover: left alone


def test_autotune_thermal_release_lifts_the_cap_and_measures_again(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, ceiling=25)
    assert at.thermal_step(19) and s.thermal_cap == 19
    assert at.thermal_release(21) and s.thermal_cap == 21 and s.learned.tdp == 19
    assert at.thermal_release(25) and s.thermal_cap is None and s.phase == "measure" and s.learned.tdp == 25
    assert not at.thermal_release(25)
    at.session = None


def test_the_guard_gives_the_tdp_back_when_autotune_is_out_of_the_game(plugin, tmp_path):
    """A paused autotune never climbs (xemu, no frame rate, a manual change), and the guard's steps back up only moved
    autotune's cap: with a target, the chip stayed at the guard's step for the rest of the game (26 W of 30) while the
    guard said "back at your 30 W"."""
    p = plugin
    p.settings["autotune_target"] = 60
    p.run(p.set_tdp(30))                                           # Custom 30 W
    p.run(p.game_started("7"))
    s = p.autotune.session
    s.own_pace, s.paused = "xemu", True                            # as the xemu check leaves the session
    p.autotune._give_back(s)
    assert p.chip() == 30
    t = tmp_path / "pkg_temp"
    p.guard = thermal.Guard(str(t), "x86_pkg_temp")
    check = lambda c: (_w(t, c * 1000), p.run(p._guard_check()))[1]
    assert check(97) == 28 and check(97) == 26 and p.chip() == 26  # hot: two steps down
    assert check(70) is None and check(70) is None and check(70) is None
    assert check(70) == 28 and p.chip() == 28                      # 30 s under 80: +2 W, on the chip
    assert check(70) is None and check(70) is None and check(70) == 30
    assert p.chip() == 30 and p.guard.stepped_w is None and s.thermal_cap is None and s.paused
    assert not p.autotune.thermal_release(30)                      # nothing left to give back
    p.autotune._step()
    assert p.chip() == 30


def test_a_refused_tdp_keeps_the_guards_cap_for_a_paused_autotune(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, ceiling=25)
    at.pause("manual CPU ceiling")
    assert at.thermal_step(19)
    monkeypatch.setattr(at.power, "set_tdp", lambda w, plugged=None: False)
    assert not at.thermal_release(21) and s.thermal_cap == 19 and s.learned.tdp == 19
    monkeypatch.setattr(at.power, "set_tdp", lambda w, plugged=None: True)
    assert at.thermal_release(21) and s.thermal_cap == 21 and s.learned.tdp == 21
    at.session = None


def test_get_autotune_says_why_it_is_not_running(plugin):
    p = plugin
    p.settings["autotune"] = True
    p.settings["autotune_plugged"] = False
    st = p.run(p.get_autotune())
    assert not st["running"] and st["why"] == "no game reported to ClawDeck yet" and st["game"] is None
    p.ac["plugged"] = True
    p.run(p.game_started("42"))
    st = p.run(p.get_autotune())
    assert st["game"] == "42" and not st["running"] and st["why"].startswith("off on the charger")
    p.settings["autotune"] = False
    assert p.run(p.get_autotune())["why"] == "off"


def test_get_autotune_says_the_running_game_has_frame_generation(plugin):
    p = plugin
    p.profiles.set_launch("42", {"opti": "xess", "opti_fg": "dlssg", "fg": 3})
    assert p.run(p.get_autotune())["game_fg"] == 0            # no game running
    p.run(p.game_started("42"))
    assert p.run(p.get_autotune())["game_fg"] == 3
    p.profiles.set_launch("42", {"opti_fg": "off"})
    assert p.run(p.get_autotune())["game_fg"] == 0


def test_onimusha_one_tap_is_the_capcom_setup_and_fsr4_needs_xmx(plugin, tmp_path, monkeypatch):
    """Per-game → Install OptiScaler in Onimusha: v10 in OptiScaler/, onehoon's REFramework, dxgi + dinput8 overrides;
    FSR 4.1.2 refused while XMX is off; Uninstall leaves the game folder as it was."""
    import asyncio
    from clawdeck import downloads, optiscaler, reframework, xess, xmxscope
    p = plugin
    p._install_lock = asyncio.Lock()
    home = str(tmp_path / "decky_user_home")
    monkeypatch.setattr(optiscaler.userexec, "chown_tree", lambda a, b: None)
    monkeypatch.setattr(reframework.userexec, "chown_tree", lambda a, b: None)
    v = p._versions()
    v["reframework"].pop("dll_sha256")                  # the stand-in dinput8.dll below isn't the real one
    monkeypatch.setattr(p, "_versions", lambda: v)
    pkg = optiscaler.package_dir(home, v["optiscaler_v10"]["version"])
    for f in ("OptiScaler.dll", "OptiScaler/libxess.dll", "OptiScaler/amd_fidelityfx_upscaler_dx12.dll",
              *[p for x in v["optiscaler_v10"]["extras"] for p in optiscaler.extra_dests(x)]):
        _w(os.path.join(pkg, f), f)
    _w(os.path.join(pkg, "OptiScaler.ini"), "[FrameGen]\nFGInput=auto\n\n[Spoofing]\nDxgi=auto\n")
    game = tmp_path / "steam" / "Onimusha"
    _w(game / "OnimushaWotS.exe", "x")
    _w(game / "nvngx_dlss.dll", "x")
    before = sorted(str(q.relative_to(game)) for q in game.rglob("*"))
    monkeypatch.setattr(xess, "steam_install_dir", lambda h, a: str(game))
    monkeypatch.setattr(xmxscope, "prefix_shim", lambda *a: {"placed": False})
    monkeypatch.setattr(p, "_fsr4_xmx_files", lambda: {"dir": "x", "ready": True, "missing": []})
    ref = tmp_path / "ref"
    _w(ref / "dinput8.dll", "onehoon")
    got = []
    monkeypatch.setattr(downloads, "fetch", lambda e, w, a=False: got.append(e["repo"]) or {"tag": e["tag"], "repo": e["repo"], "archive": "REFramework.zip", "dir": str(ref)})
    p.settings["opti_games"] = {}
    p.profiles.set_launch("2638890", {"xmx": False})
    r = p.run(p.game_choose("2638890", "fsr4_xmx", 2))
    assert not r["ok"] and "XMX" in r["error"] and not (game / "OptiScaler.ini").exists()
    rec = p.run(p.game_recommend("2638890"))
    assert rec["capcom"] and rec["fsr4"]["xmx"] is False
    p.profiles.set_launch("2638890", {"xmx": None})
    r = p.run(p.game_choose("2638890", "xess", 2))
    assert r["ok"], r
    assert got == ["onehoon/REFramework"] and (game / "dinput8.dll").exists() and (game / "OptiScaler" / "libxess.dll").exists()
    assert (game / "OptiScaler" / "fakenvapi.dll").exists() and (game / "OptiScaler" / "fakenvapi.ini").exists()
    assert "UseFakenvapi=true" in (game / "OptiScaler.ini").read_text()
    assert (game / "re2_fw_config.txt").read_text() == "REFrameworkConfig_MenuKey_V2=46\n"
    ini = (game / "OptiScaler.ini").read_text()
    assert "FGInput=DLSSG" in ini and "Dxgi=auto" in ini and "OptiDllPath=.\\OptiScaler" in ini
    assert r["env"]["WINEDLLOVERRIDES"] == "dxgi=n,b;dinput8=n,b;" and "Spoofing_Dxgi" not in r["env"]
    assert optiscaler.owner(str(game))["version"] == v["optiscaler_v10"]["version"]
    r = p.run(p.game_choose("2638890", "own", 0))
    assert r["ok"] and sorted(str(q.relative_to(game)) for q in game.rglob("*")) == before


def test_launch_env_e_cores_only():
    from clawdeck import launchenv
    assert launchenv.compose({"cores": "e"})["CLAWDECK_CORES"] == "e"
    assert "CLAWDECK_CORES" not in launchenv.compose({"cores": "all"})
    assert "CLAWDECK_CORES" not in launchenv.compose({})
    import pytest
    with pytest.raises(ValueError):
        launchenv.compose({"cores": "p"})


def test_pl2_by_hand_only_with_custom_and_back_to_pl1_plus_2(plugin):
    p = plugin
    p.run(p.set_tdp(20))                                           # Custom 20 W on battery
    pl2 = lambda: sysfs.read_int(p.power.p.rapl_pl2) // 1_000_000
    assert pl2() == 22                                             # PL1 + 2 W
    r = p.run(p.set_pl2(True, 30))
    assert r["ok"] and r["pl2_w"] == 30 and pl2() == 30
    assert r["read_w"] == 30 and r["summary"] == "PL2 30 W (yours)"   # read back from the chip, not "PL2 set"
    st = p.run(p.get_power())
    assert st["pl2_manual"] and st["pl2_set_w"] == 30 and st["pl2_max"] == 45
    p.run(p.set_pl2(True, 99))                                     # clamped to the model's PL2
    assert pl2() == 45
    p.run(p.set_tdp(25))                                           # a new TDP keeps the hand-set PL2
    assert pl2() == 45
    p.run(p.apply_profile("battery"))                              # not Custom: PL1 + 2 W again
    assert pl2() == sysfs.read_int(p.power.p.rapl_pl1) // 1_000_000 + 2
    p.run(p.set_tdp(20))
    assert pl2() == 45                                             # back to Custom: yours again
    r = p.run(p.set_pl2(False))
    assert pl2() == 22 and r["ok"] and r["summary"] == "PL2 22 W (PL1 + 2 W)"


def test_set_pl2_reads_the_chip_back_and_says_when_it_did_not_take_it(plugin, monkeypatch):
    """set_pl2 read status()["pl2_w"], a key that only exists under "rapl": the read-back was always None, so a PL2
    the chip did not take was reported as done and the line said "PL2 set" without the watts."""
    p = plugin
    p.run(p.set_tdp(20))                                           # Custom 20 W
    r = p.run(p.set_pl2(True, 12))                                 # under PL1: the chip gets PL1
    assert r["ok"] and r["read_w"] == 20 and r["summary"] == "PL2 20 W (yours)"
    status = p.power.status
    monkeypatch.setattr(p.power, "status", lambda: {**status(), "rapl": {**status()["rapl"], "pl2_w": 25.0}})
    r = p.run(p.set_pl2(True, 30))                                 # a chip that keeps PL2 at 25 W
    assert not r["ok"] and r["read_w"] == 25 and r["summary"] == "PL2 25 W (yours)" and r["error"]
    monkeypatch.setattr(p.power, "status", lambda: {**status(), "tdp_backend": "hhd"})
    r = p.run(p.set_pl2(True, 30))                                 # not RAPL's limit to read: no claim
    assert r["ok"] and r["read_w"] is None and r["summary"] == "PL2 set (yours)"


def test_clawdecks_cabinet_dll_next_to_the_wrapper_and_gone_after_uninstall(tmp_path, monkeypatch):
    """FSR 4 on XMX: the user provides nitec's two files only; ClawDeck's own cabinet.dll (bin/, from
    tools/cabinet-lzms) goes next to the launch wrapper at start (its Decky Framegen stage takes it from there), and an
    uninstall removes it with the rest."""
    import asyncio
    m = _load_main(tmp_path, monkeypatch)
    cab = tmp_path / "plugin" / "bin" / "cabinet.dll"
    _w(cab, "clawdeck-lzms")
    monkeypatch.setattr(m.optiscaler, "OWN_CABINET", str(cab))
    monkeypatch.setattr(m.userexec, "chown_tree", lambda p, h: None)
    home = tmp_path / "decky_user_home"
    p = m.Plugin.__new__(m.Plugin)
    f = p._fsr4_xmx_files()
    assert f["missing"] == ["amd_fidelityfx_upscaler_dx12.dll", "fsr-original.dll"] and not f["ready"]
    _w(home / "fsr4-xmx" / "amd_fidelityfx_upscaler_dx12.dll", "nitec")
    _w(home / "fsr4-xmx" / "fsr-original.dll", "o")
    assert p._fsr4_xmx_files() == {"dir": str(home / "fsr4-xmx"), "ready": True, "missing": []}
    p._install_wrapper()
    assert m.WRAPPER_CABINET == str(home / ".local/share/clawdeck/cabinet.dll")
    assert (home / ".local/share/clawdeck/cabinet.dll").read_bytes() == cab.read_bytes()
    assert (home / ".local/share/clawdeck/clawdeck-launch.sh").exists()
    p.power = type("P", (), {"revert_all": lambda self: 0})()
    p.settings = {}
    monkeypatch.setattr(m.edid, "EDID_DIR", str(tmp_path / "noedid"))
    for mod, fn in ((m.xmxscope, "remove_scope"), (m.xmxscope, "restore_session"), (m.quietlog, "remove"), (m.telemetry, "apply")):
        monkeypatch.setattr(mod, fn, lambda *a: {"ok": True})
    asyncio.run(p._uninstall())
    assert not (home / ".local/share/clawdeck/cabinet.dll").exists()
    assert (home / ".local/share/clawdeck/clawdeck-launch.sh").read_text() == m.PASSTHROUGH


def test_recommended_upscaler_is_fsr4_where_it_runs_and_the_supported_list_no_blocks(plugin, monkeypatch, tmp_path):
    m = type(plugin)._prefer_fsr4.__globals__          # main.py's globals (loaded under another name)
    from clawdeck import gamedb, livedb
    g = tmp_path / "game"
    g.mkdir()
    (g / "nvngx_dlss.dll").write_bytes(b"x")
    (g / "d3d12core.dll").write_bytes(b"x")
    base = lambda: gamedb.recommend({"games": [], "re_engine_exes": []}, str(g))
    files = {"ready": True, "missing": [], "dir": "~/fsr4-xmx"}
    monkeypatch.setattr(type(plugin), "_fsr4_xmx_files", staticmethod(lambda: files))
    rec = base(); plugin._prefer_fsr4(rec, str(g), "1")
    assert rec["launch"]["opti"] == "fsr4_xmx" and rec["upscaler"] == "fsr4_xmx" and "FSR 4.1.2" in rec["reasons"][0]
    # XMX off for the game, or nitec's files missing: XeSS stays, and the reason says what FSR 4.1.2 needs
    plugin.profiles.set_launch("2", {"xmx": False})
    rec = base(); plugin._prefer_fsr4(rec, str(g), "2"); assert rec["launch"]["opti"] == "xess"
    files.update(ready=False, missing=["fsr-original.dll"])
    rec = base(); plugin._prefer_fsr4(rec, str(g), "1")
    assert rec["launch"]["opti"] == "xess" and any("~/fsr4-xmx" in r for r in rec["reasons"])
    # the public list says not supported on Intel Arc: not recommended, and applying it refuses with that reason
    lists = tmp_path / "lists"; lists.mkdir()
    (lists / livedb.SUPPORTED_LIST_FILE).write_text(
        "| a | Titan Quest II | Not Supported | x | y |\n<!-- opticlick-supported-games-meta\n"
        '{"games": [{"game_name_en": "Titan Quest II", "cover_steam_app_ids": ["1154030"]}]}\n-->\n')
    monkeypatch.setitem(m, "LISTS_DIR", str(lists))
    rec = base(); plugin._supported_list_verdict(rec, "1154030")
    assert rec["recommended"] is False and "not working on Intel Arc" in rec["reasons"][0]
    rec = base(); plugin._supported_list_verdict(rec, "99")
    assert "recommended" not in rec and "Not on the public supported-games list" in rec["reasons"][-1]


def test_optiscaler_compat_entry_sets_hook_names_reframework_and_blocks_broken(plugin, monkeypatch, tmp_path):
    from clawdeck import gamedb
    g = tmp_path / "game"
    g.mkdir()
    (g / "nvngx_dlss.dll").write_bytes(b"x")
    base = lambda: gamedb.recommend({"games": [], "re_engine_exes": []}, str(g))
    wiki = {}
    monkeypatch.setattr(type(plugin), "_wiki_for", staticmethod(lambda appid, fetch=True: wiki[appid]))
    wiki["1"] = ({"name": "Enshrouded", "status": "works", "notes": "", "reframework": False, "slug": "Enshrouded"},
                 {"proxies": ["winmm.dll", "dbghelp.dll"], "issues": ["FSR3 inputs don't work."], "notes": []})
    rec = base(); plugin._optiscaler_entry(rec, "1")
    assert rec["proxies"] == ["winmm.dll", "dbghelp.dll"] and any("winmm.dll or dbghelp.dll" in r for r in rec["reasons"])
    assert any(r.startswith("Known: FSR3") for r in rec["reasons"]) and "recommended" not in rec
    wiki["2"] = ({"name": "EA Sports WRC", "status": "broken", "notes": "Crashes", "reframework": False, "slug": None}, None)
    rec = base(); plugin._optiscaler_entry(rec, "2")
    assert rec["recommended"] is False and "not working" in rec["reasons"][0]
    wiki["3"] = ({"name": "PRAGMATA", "status": "works", "notes": "Requires REFramework", "reframework": True, "slug": None}, None)
    rec = base(); plugin._optiscaler_entry(rec, "3")
    assert rec["route"] == "re_engine" and rec["launch"]["reframework"] is True and rec["re_engine"]


def test_autotune_gives_up_on_a_game_without_a_frame_rate(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch)
    at.sampler.sample = lambda: autotune.Sample(t=time.monotonic(), frametime_ms=None, fps=None, gpu_mhz=1500,
                                                gpu_max_mhz=2400, cpu_pct=40, pkg_w=20.0, gpu_busy=70,
                                                source="presented", shown_fps=None)   # no XeLL log, no MangoHud log
    tdp = s.learned.tdp
    for _ in range(autotune.NO_FPS_S - 1):
        at._step()
    assert not s.paused and not at.status()["no_frames"]
    at._step()
    st = at.status()
    assert s.paused and st["no_frames"] and s.learned.tdp == tdp     # nothing changed, the limit stays
    at.session = None


def test_autotune_takes_over_when_the_first_frames_come_late(fake_hw, monkeypatch):
    """Order of the Sinking Star shows its first frame 54 s after launch: autotune gave up at 30 s for the whole
    session. Giving up puts your pick back and waits; frames after that hand it back to autotune. A manual change
    still pauses it for good."""
    at, s = _tuner(fake_hw, monkeypatch)
    at.sampler.sample = lambda: autotune.Sample(t=time.monotonic(), frametime_ms=None, fps=None, gpu_mhz=1500,
                                                gpu_max_mhz=2400, cpu_pct=40, pkg_w=20.0, gpu_busy=70,
                                                source="presented", shown_fps=None)
    for _ in range(autotune.NO_FPS_S + 20):
        at._step()
    assert s.paused and s.no_frames
    _Game(at, lambda tdp, *_: min(60.0, 3.0 * tdp))
    at._step()
    assert not s.paused and not s.no_frames and s.phase == "measure" and not at.status()["no_frames"]
    at.pause("manual CPU ceiling")
    s.no_frames = True                                           # even if it had given up before
    at.pause("again")
    at._step()
    assert s.paused and not s.no_frames                          # yours stays yours
    at.session = None


def test_autotune_without_a_frame_rate_gives_back_the_limit_you_picked(fake_hw, monkeypatch):
    """What start() applied stayed on the chip after autotune gave up on a game without a frame rate: a saved result
    (here 17 W, CPU 90 %, boost, a GPU floor), or a target's mid-range start (22 W of a 35 W pick; Enshrouded,
    2026-11-12: 30-40 fps instead of 60). Now the pick goes back."""
    pw = power.Power(fake_hw)
    chip = {}
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: chip.update(tdp=w) or True)
    at = autotune.Autotune(fake_hw, pw, "/nonexistent")
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    pw.apply_profile("", {"tdp": 35, "cpu_max_pct": 85, "boost": False}, False)       # the pick: Custom 35 W
    at.start("1", {"tdp": 17, "ceiling": 35, "max_fps": 60.0, "cpu_max_pct": 90, "boost": True, "gpu_min_mhz": 1300},
             8, 35, False)
    p = fake_hw.paths
    assert chip["tdp"] == 17 and sysfs.read_int(p.cpu_max_perf_pct) == 90 and sysfs.read_int(p.cpu_no_turbo) == 0
    assert sysfs.read_int(p.gpu_min_freq) == 1300
    at.sampler.sample = lambda: autotune.Sample(t=time.monotonic(), frametime_ms=None, fps=None, gpu_mhz=1500,
                                                gpu_max_mhz=2400, cpu_pct=40, pkg_w=22.0, gpu_busy=99,
                                                source="presented", shown_fps=None)
    for _ in range(autotune.NO_FPS_S):
        at._step()
    st = at.status()
    assert st["no_frames"] and st["paused"] and st["tdp"] == 35 and chip["tdp"] == 35
    assert sysfs.read_int(p.cpu_max_perf_pct) == 85 and sysfs.read_int(p.cpu_no_turbo) == 1
    assert sysfs.read_int(p.gpu_min_freq) == 300
    at.session = None


def test_pausing_autotune_gives_back_what_autotune_changed_and_keeps_your_change(fake_hw, monkeypatch):
    """pause() only set a flag: after Autotune was switched off in a game, or after one manual change, Autotune's own
    TDP and levers stayed on the chip until the game ended (here 17 W of a 35 W pick, CPU 90 %, boost, a GPU floor).
    Now the pick goes back, the change made by hand stays, and what Autotune had found is still saved."""
    pw = power.Power(fake_hw)
    chip = {}
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: chip.update(tdp=w) or True)
    at = autotune.Autotune(fake_hw, pw, "/nonexistent")
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    pw.apply_profile("", {"tdp": 35, "cpu_max_pct": 85, "boost": False}, False)       # the pick: Custom 35 W
    at.start("1", {"tdp": 17, "ceiling": 35, "max_fps": 60.0, "cpu_max_pct": 90, "boost": True, "gpu_min_mhz": 1300},
             8, 35, False)
    p = fake_hw.paths
    assert chip["tdp"] == 17 and sysfs.read_int(p.cpu_max_perf_pct) == 90 and sysfs.read_int(p.cpu_no_turbo) == 0
    at.pause("manual GPU clocks")                                     # main.py: pause(), then the user's write
    pw.set_gpu_window(900, None)
    assert at.session.paused and chip["tdp"] == 35 and at.status()["tdp"] == 35
    assert sysfs.read_int(p.cpu_max_perf_pct) == 85 and sysfs.read_int(p.cpu_no_turbo) == 1
    assert sysfs.read_int(p.gpu_min_freq) == 900                      # yours, not the pick's 300
    at.pause("manual CPU ceiling")                                    # a second change by hand: the first one stays
    pw.set_cpu_max_pct(70)
    at.pause("autotune switched off")
    assert sysfs.read_int(p.gpu_min_freq) == 900 and sysfs.read_int(p.cpu_max_perf_pct) == 70 and chip["tdp"] == 35
    found = at.stop(revert=False)["learned"]
    assert found["tdp"] == 17 and found["cpu_max_pct"] == 90 and found["gpu_min_mhz"] == 1300


def test_switching_autotune_off_in_a_game_puts_the_pick_back_on_the_chip(plugin):
    p = plugin
    p.run(p.set_tdp(26))                                           # Custom 26 W
    p.run(p.game_started("7"))
    s = _lower(p)
    low = p.chip()
    assert s.ceiling_tdp == 26 and low < 26                        # 60 fps from 15 W: Autotune lowered the TDP
    pl2 = lambda: sysfs.read_int(p.power.p.rapl_pl2) // 1_000_000
    assert p.run(p.set_setting("autotune", False)) and s.paused
    assert p.chip() == 26 and pl2() == 28                          # your limit and PL1 + 2 W, at once
    for _ in range(10):
        p.autotune._step()
    assert p.chip() == 26
    assert p.run(p.get_power())["autotune_w"] is None


def test_autotune_stays_out_of_xemu_and_gives_back_the_limit_you_picked(fake_hw, monkeypatch, tmp_path):
    """xemu presents 60 fps whatever the emulated game does (16-18 game fps at 16 W, 2026-11-14) and slows the game
    under its own draw: no frame rate can steer a lower limit there. Found a few seconds in (an ES-DE game starts the
    emulator after its game-start event), the pick goes back and stays, also when 60 fps keep coming."""
    pw = power.Power(fake_hw)
    chip = {}
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: chip.update(tdp=w) or True)
    at = autotune.Autotune(fake_hw, pw, "/nonexistent")
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    proc = tmp_path / "proc"
    for pid, name in (("1", "systemd"), ("4242", "es-de")):
        _w(str(proc / pid / "comm"), name)
    monkeypatch.setattr(autotune, "PROC", str(proc))
    pw.apply_profile("", {"tdp": 30}, False)                          # the pick: Custom 30 W
    at.start("3119184808#xbox/Project Gotham Racing 2", {"tdp": 16, "ceiling": 30, "max_fps": 60.0}, 8, 30, False)
    assert chip["tdp"] == 16                                          # a saved result from before
    at.sampler.sample = lambda: autotune.Sample(t=time.monotonic(), frametime_ms=16.7, fps=60.0, gpu_mhz=1000,
                                                gpu_max_mhz=2400, cpu_pct=30, pkg_w=19.0, gpu_busy=40,
                                                source="presented", shown_fps=None)
    for _ in range(3):
        at._step()
    assert at.status()["own_pace"] is None                            # not started yet at 3 s
    _w(str(proc / "4300" / "comm"), "xemu")                           # ES-DE starts it
    for _ in range(7):
        at._step()
    st = at.status()
    assert st["own_pace"] == "xemu" and st["paused"] and not st["no_frames"] and chip["tdp"] == 30
    for _ in range(60):                                               # 60 fps keep coming: nothing changes
        at._step()
    assert chip["tdp"] == 30 and at.session.paused
    assert autotune.own_pace_emulator(str(tmp_path / "missing")) is None
    at.session = None


def _mango_rows(path, frames_ms, header=False):
    with open(path, "a") as f:
        if header:
            f.write("os,cpu,gpu\nLinux,Intel,Intel\nfps,frametime,cpu_load,elapsed\n")
        for ms in frames_ms:
            f.write(f"{1000 / ms:.1f},{ms},10,0\n")


def test_sampler_follows_a_live_mangoapp_log_without_touching_it(fake_hw, tmp_path, monkeypatch):
    """A game without its own MangoHud log (xemu's Flatpak, 2026-11-14: "no frame rate after 30 s"): mangoapp's
    per-frame log of the focused app. One that is already being written is only read, from where it stands."""
    sent = []
    monkeypatch.setattr(autotune, "mangoapp_command", lambda c: sent.append(c) or True)
    live = tmp_path / "mangoapp_2026-11-14_23-00-00.csv"
    _mango_rows(live, [33.3] * 50, header=True)                       # before this game: not its frames
    smp = autotune.Sampler(fake_hw, str(tmp_path / "mangohud"), home=str(tmp_path))
    smp.set_prefix(str(tmp_path / "igdext_xell.log"))
    assert smp.frametime() == (None, None)
    _mango_rows(live, [16.0, 17.0, 16.0, 17.0])                         # this game's frames: 4 in 66 ms
    ft, fps = smp.frametime()
    assert round(ft, 2) == 16.5 and round(fps, 1) == 60.6 and sent == []
    smp.set_prefix(None)
    smp.release()
    assert sent == []                                                   # not Autotune's: it stays on


def test_frame_stats_show_the_slow_frames_an_average_hides():
    """Midnight Club: Los Angeles on RPCS3 at a 15 W limit (Claw 8 EX, 2026-11-14): 29.7 of 30 fps, the median frame on
    pace, 6 % of the frames more than 10 % slow."""
    frames = [33.3] * 94 + [38.0] * 6
    median, p95, slow = autotune.frame_stats(frames)
    assert (median, p95, round(slow, 1)) == (33.3, 38.0, 6.0)
    assert round(1000 * len(frames) / sum(frames), 1) == 29.8                  # what the frame rate says of it
    # every frame slower: against the frames' own median nothing is slow, against the pace before all of them are
    assert autotune.frame_stats([40.0] * 30)[2] == 0.0
    assert autotune.frame_stats([40.0] * 30, pace_ms=33.3) == (40.0, 40.0, 100.0)
    assert autotune.frame_stats([16.7]) == (16.7, 16.7, 0.0) and autotune.frame_stats([]) is None


def test_a_sample_from_mangoapps_log_has_the_frame_times_behind_it(fake_hw, tmp_path, monkeypatch):
    monkeypatch.setattr(autotune, "mangoapp_command", lambda c: True)
    monkeypatch.setattr(autotune.shadowblip, "gpu_busy_percent", lambda: None)
    live = tmp_path / "mangoapp_2026-11-14_22-05-00.csv"
    _mango_rows(live, [33.3] * 5, header=True)
    smp = autotune.Sampler(fake_hw, str(tmp_path / "mangohud"), home=str(tmp_path))
    smp.set_prefix(None)
    assert smp.sample().frames is None                                  # nothing new in the log: no figures
    _mango_rows(live, [33.3] * 28 + [38.0] * 2)                         # a second at the edge of the limit
    a = smp.sample()
    assert (a.frames, a.ft_median, a.ft_p95, round(a.slow_pct, 1)) == (30, 33.3, 38.0, 6.7)
    assert smp.frames_ms == [33.3] * 28 + [38.0] * 2 and round(a.fps, 2) == 29.75
    _mango_rows(live, [40.0] * 25)                                      # the limit lowered: every frame slower
    b = smp.sample()
    assert (b.frames, b.ft_median, b.slow_pct, round(b.fps, 1)) == (25, 40.0, 100.0, 25.0)   # slow against the pace before
    _mango_rows(live, [40.0] * 60)
    assert smp.sample().slow_pct == 100.0                               # ... which was still the old one
    _mango_rows(live, [40.0] * 25)
    assert smp.sample().slow_pct == 0.0                                 # 60 frames on, 40 ms is the pace
    smp.set_prefix(None)                                                # the next game starts without this one's pace
    _mango_rows(live, [16.7] * 60)
    assert smp.sample().slow_pct == 0.0


def test_a_sample_from_the_games_own_mangohud_log_has_no_frame_times(fake_hw, tmp_path, monkeypatch):
    """The launch wrapper's MangoHud log has one row a second (an average): no frame behind it to count."""
    monkeypatch.setattr(autotune.shadowblip, "gpu_busy_percent", lambda: None)
    logs = tmp_path / "mangohud"
    logs.mkdir()
    (logs / "game_2026-11-14.csv").write_text("fps,frametime\n59.8,16.7\n60.1,16.6\n")
    smp = autotune.Sampler(fake_hw, str(logs), home=str(tmp_path))
    a = smp.sample()
    assert round(a.fps, 1) == 60.0 and (a.frames, a.ft_median, a.ft_p95, a.slow_pct) == (None, None, None, None)
    assert smp.frames_ms == []


def test_autotune_csv_gets_the_frame_time_columns_and_an_older_log_is_set_aside(fake_hw, tmp_path, monkeypatch):
    log = tmp_path / "autotune.csv"
    old = "time,appid,phase,fps,shown_fps,source,gpu_mhz,gpu_max_mhz,cpu_pct,pkg_w,tdp,cpu_max_pct,gpu_min_mhz,boost,bound,max_fps\n1.0,7,hold,60.0,60.0,presented,,,,,20,100,,1,balanced,60.0\n"
    log.write_text(old)
    pw = power.Power(fake_hw)
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: True)
    at = autotune.Autotune(fake_hw, pw, log_dir="/nonexistent", csv_log=str(log))
    at.session = autotune.Session(appid="1", floor_tdp=8, ceiling_tdp=20, settle=1, learned=autotune.Learned(tdp=20))
    monkeypatch.setattr(autotune, "own_pace_emulator", lambda proc=None: None)
    at.sampler.sample = lambda: autotune.Sample(t=5.0, frametime_ms=33.67, fps=29.7, gpu_mhz=None, gpu_max_mhz=None,
                                                cpu_pct=None, pkg_w=15.0, shown_fps=29.7, frames=30, ft_median=33.3,
                                                ft_p95=38.04, slow_pct=6.667)
    at._step()
    at.sampler.sample = lambda: autotune.Sample(t=6.0, frametime_ms=16.7, fps=60.0, gpu_mhz=None, gpu_max_mhz=None,
                                                cpu_pct=None, pkg_w=15.0, shown_fps=60.0)      # no frame times
    at._step()
    at.session = None
    assert (tmp_path / "autotune.csv.1").read_text() == old            # the earlier build's rows, whole
    rows = log.read_text().splitlines()
    assert rows[0] == ",".join(autotune.CSV_FIELDS) and rows[0].endswith(",max_fps,frames,ft_median,ft_p95,slow_pct")
    assert rows[1].endswith(",30,33.3,38.04,6.7") and rows[2].endswith(",,,,") and len(rows) == 3
    # the same columns the next time: the log goes on
    at2 = autotune.Autotune(fake_hw, pw, log_dir="/nonexistent", csv_log=str(log))
    at2.session = autotune.Session(appid="1", floor_tdp=8, ceiling_tdp=20, settle=1, learned=autotune.Learned(tdp=20))
    at2.sampler.sample = at.sampler.sample
    at2._step()
    at2.session = None
    assert len(log.read_text().splitlines()) == 4 and (tmp_path / "autotune.csv.1").read_text() == old


def test_sampler_turns_mangoapps_log_on_and_off_and_asks_again_when_it_stops(fake_hw, tmp_path, monkeypatch):
    sent, clock = [], {"t": 1000.0}
    monkeypatch.setattr(autotune, "mangoapp_command", lambda c: sent.append(c) or True)
    monkeypatch.setattr(autotune.time, "monotonic", lambda: clock["t"])
    smp = autotune.Sampler(fake_hw, str(tmp_path / "mangohud"), home=str(tmp_path))
    smp.set_prefix(str(tmp_path / "igdext_xell.log"))
    assert smp.frametime() == (None, None) and sent == ["logging=1"]    # none running: turned on
    log_file = tmp_path / "mangoapp_2026-11-15_00-10-00.csv"
    _mango_rows(log_file, [25.0, 25.0], header=True)
    clock["t"] += 1
    ft, fps = smp.frametime()
    assert ft == 25.0 and fps == 40.0                                   # its own log: read from the start
    clock["t"] += autotune.MANGOAPP_RELOG_S + 1                         # Steam reloaded mangoapp's config: no rows
    assert smp.frametime() == (None, None) and sent == ["logging=1", "logging=1"]
    smp.set_prefix(None)
    assert sent[-1] == "logging=1"                                      # a session ends inside the app: stays on
    smp.release()
    assert sent[-1] == "logging=0"                                      # the game ended: Autotune's log goes off


def test_sampler_keeps_its_mangoapp_log_through_a_game_switch(fake_hw, tmp_path, monkeypatch):
    """ES-DE starts a game inside the running app: a new session on the same mangoapp log. Turning the log off at
    the switch (2026-11-15, Super Mario Bros. and PGR2 from ES-DE) cut the frames for 20 s, and claw-framerec's."""
    sent, clock = [], {"t": 1000.0}
    monkeypatch.setattr(autotune, "mangoapp_command", lambda c: sent.append(c) or True)
    monkeypatch.setattr(autotune.time, "monotonic", lambda: clock["t"])
    smp = autotune.Sampler(fake_hw, str(tmp_path / "mangohud"), home=str(tmp_path))
    smp.set_prefix(str(tmp_path / "igdext_xell.log"))
    smp.frametime()
    log_file = tmp_path / "mangoapp_2026-11-15_03-08-26.csv"
    _mango_rows(log_file, [25.0, 25.0], header=True)
    clock["t"] += 1
    assert smp.frametime() == (25.0, 40.0)                              # ES-DE's menu
    _mango_rows(log_file, [50.0])                                       # its last frame before the switch
    smp.set_prefix(None)
    smp.set_prefix(str(tmp_path / "igdext_xell.log"))                   # the ES-DE game's session
    _mango_rows(log_file, [20.0, 20.0])
    clock["t"] += 1
    assert smp.frametime() == (20.0, 50.0) and sent == ["logging=1"]    # only the game's frames, the log never off


def test_mangoapp_log_goes_off_when_the_game_stops_not_at_an_es_de_switch(plugin, monkeypatch):
    p = plugin
    calls = []
    monkeypatch.setattr(p.autotune, "release_frames", lambda: calls.append("release"))
    esde = "3119184808"
    p.run(p.game_started(esde))
    assert _frontend_event(p, "start", "ps3", "/roms/ps3/Ratchet.iso", "Ratchet")
    assert calls == []                                                  # a game inside ES-DE: the log stays on
    p.run(p.game_stopped(esde))
    assert calls == ["release"]


def test_sampler_prefers_the_games_own_mangohud_log(fake_hw, tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(autotune, "mangoapp_command", lambda c: sent.append(c) or True)
    logs = tmp_path / "mangohud"
    logs.mkdir()
    smp = autotune.Sampler(fake_hw, str(logs), home=str(tmp_path))
    smp.set_prefix(str(tmp_path / "igdext_xell.log"))
    smp.frametime()
    assert sent == ["logging=1"]
    (logs / "game_2026-11-15_00-11-00.csv").write_text("os\nLinux\nfps,frametime\n60.0,16.6\n")
    smp._log_checked = -1e9
    assert smp.frametime() == (16.6, 60.0) and sent == ["logging=1", "logging=0"]


def test_game_list_tags_say_what_clawdecks_optiscaler_runs(plugin):
    p = plugin
    assert p._game_tags({"opti": "fsr4_xmx", "opti_fg": "dlssg", "fg": 3}) == [
        {"text": "FSR 4.1.2", "kind": "fsr"}, {"text": "3x FG", "kind": "fg"}]
    assert p._game_tags({"opti": "xess", "opti_fg": "off", "fg": 3}) == [{"text": "XeSS 2", "kind": "xess"}]
    assert p._game_tags({"opti": "fsr4_xmx_lite"}) == [{"text": "FSR 4.1.2 Lite", "kind": "fsr"}]
    assert p._game_tags({}) == []


def test_no_xmx_chip_gets_no_xmx_driver_and_no_fsr4(plugin):
    p = plugin
    p.caps = {"device": "MSI Claw A1M", "xmx": False}
    r = p.run(p.xess_install())
    assert not r["ok"] and "no XMX cores" in r["error"]
    assert p._no_xmx()
    p.caps = {"device": "ONEXPLAYER 3", "xmx": True, "hdr_brightness": False}
    assert not p._no_xmx()
    assert p.run(p.get_brightness()) == {"supported": False}


def test_fsr4_refused_after_a_launch_without_xmx(plugin, tmp_path, monkeypatch):
    """FH5 on a Claw 8 AI+ (r/MSIClaw 2026-11-07): FSR 4.1.2 drew a black screen because that launch ran without the
    XMX cores. While the game's last launch shows that, FSR 4.1.2 is refused (Per-game hides it too); XeSS 2 is not."""
    import asyncio
    from clawdeck import xess
    p = plugin
    p._install_lock = asyncio.Lock()
    game = tmp_path / "steam" / "FH5"
    _w(game / "ForzaHorizon5.exe", "x")
    monkeypatch.setattr(xess, "steam_install_dir", lambda h, a: str(game))
    monkeypatch.setattr(p, "_fsr4_xmx_files", lambda: {"dir": "x", "ready": True, "missing": []})
    for state in ("stale", "failed"):
        monkeypatch.setattr(xess, "xmx_proof", lambda *a, s=state: {"state": s})
        r = p.run(p.game_choose("1551360", "fsr4_xmx_lite", 0))
        assert not r["ok"] and "last launch ran without" in r["error"] and not (game / "OptiScaler.ini").exists()
    seen = []
    monkeypatch.setattr(xess, "xmx_proof", lambda *a: seen.append(1) or {"state": "ok"})
    r = p.run(p.game_choose("1551360", "fsr4_xmx", 0))
    assert seen and "last launch ran without" not in (r.get("error") or "")


def test_an_update_through_decky_keeps_everything(tmp_path, monkeypatch):
    """Decky's installer uninstalls the old ClawDeck before it unpacks the new one. After Setup -> Update's marker that
    uninstall reverts nothing (XMX driver, game setups, wrapper stay); without it, or once it is old, it reverts."""
    import asyncio
    m = _load_main(tmp_path, monkeypatch)
    p = m.Plugin.__new__(m.Plugin)
    reverted = []
    p.power = type("P", (), {"revert_all": lambda self: reverted.append(1) or 0})()
    p.settings = {}
    monkeypatch.setattr(m.edid, "EDID_DIR", str(tmp_path / "noedid"))
    monkeypatch.setattr(m.userexec, "chown_tree", lambda p, h: None)
    for mod, fn in ((m.xmxscope, "remove_scope"), (m.xess, "remove_for_uninstall"), (m.quietlog, "remove"), (m.telemetry, "apply")):
        monkeypatch.setattr(mod, fn, lambda *a: {"ok": True})
    assert asyncio.run(p.update_prepare())["ok"]
    asyncio.run(p._uninstall())
    assert not reverted and not m.selfupdate.update_pending(m.RUNTIME_DIR)     # used once
    asyncio.run(p._uninstall())
    assert reverted == [1]
    m.selfupdate.mark_update(m.RUNTIME_DIR)
    old = os.path.join(m.RUNTIME_DIR, m.selfupdate.MARKER)
    os.utime(old, (1, 1))                                                       # cancelled long ago
    asyncio.run(p._uninstall())
    assert reverted == [1, 1]


def test_wide_vrr_live_where_no_rpm_ostree_boot_on_bazzite_and_a_boot_loop_guard(plugin, monkeypatch):
    """The VRR range fix: Bazzite (rpm-ostree) keeps the tested boot method; SteamOS / CachyOS get the live one,
    applied again at every start, except after a start that didn't get a minute past it."""
    import time as _t
    from clawdeck import edid
    p = plugin
    calls = []
    st = {"method": None, "live_possible": True, "rpm_ostree": True, "pending": None}
    monkeypatch.setattr(edid, "status", lambda: dict(st))
    monkeypatch.setattr(edid, "live_enable", lambda: calls.append("live") or {"ok": True, "restart": False, "method": "live"})
    monkeypatch.setattr(edid, "live_disable", lambda: calls.append("live-off") or {"ok": True})
    monkeypatch.setattr(edid, "enable", lambda: calls.append("boot") or {"ok": True, "restart": True, "method": "boot"})
    monkeypatch.setattr(edid, "disable", lambda: calls.append("boot-off") or {"ok": True, "restart": True})
    # the boot method comes from the test, never from the machine running it (CI's Ubuntu has GRUB)
    backend = {"b": {"kind": "rpm-ostree"}}
    monkeypatch.setattr(edid, "boot_backend", lambda for_enable=True: backend["b"])
    monkeypatch.setattr(edid, "conf_enable", lambda b: calls.append("conf") or {"ok": True, "restart": True, "method": "boot"})
    monkeypatch.setattr(edid, "conf_disable", lambda b: calls.append("conf-off") or {"ok": True, "restart": True})
    r = p.run(p.set_wide_vrr(True))
    assert r["restart"] and calls == ["boot"] and not p.settings["wide_vrr_live"]          # Bazzite: unchanged
    st.update(method="boot")
    p.run(p.set_wide_vrr(False))
    assert calls[-1] == "boot-off"
    st.update(method=None, rpm_ostree=False)                                               # SteamOS: its GRUB
    backend["b"] = {"kind": "steamos-grub"}
    r = p.run(p.set_wide_vrr(True))
    assert r["restart"] and calls[-1] == "conf"
    st.update(method="boot", pending="on")
    p.run(p.set_wide_vrr(False))
    assert calls[-1] == "conf-off"
    st.update(method="boot", pending="on")                                                 # Install no longer
    monkeypatch.setattr(edid, "boot_backend", lambda for_enable=True: None if for_enable else {"kind": "steamos-grub"})
    p.run(p.set_wide_vrr(False))                                                           # possible: Remove still
    assert calls[-1] == "conf-off" and "boot-off" not in calls[2:]                         # never rpm-ostree here
    monkeypatch.setattr(edid, "boot_backend", lambda for_enable=True: backend["b"])
    st.update(method=None, pending=None)                                                   # no known bootloader
    backend["b"] = None
    r = p.run(p.set_wide_vrr(True))
    assert r["ok"] and not r["restart"] and calls[-1] == "live" and p.settings["wide_vrr_live"]
    sleeps = []
    monkeypatch.setattr(_t, "sleep", lambda s: sleeps.append(s))
    p._vrr_live_again()                                                                    # a good start
    assert calls[-1] == "live" and sleeps == [60] and not os.path.exists(os.path.join(m_runtime(p), p.VRR_GUARD))
    with open(os.path.join(m_runtime(p), p.VRR_GUARD), "w") as f:                          # a plugin reload in the
        f.write(open("/proc/sys/kernel/random/boot_id").read().strip())                    # same boot: no harm
    n = len(calls)
    p._vrr_live_again()
    assert len(calls) == n + 1 and p.settings["wide_vrr_live"]
    with open(os.path.join(m_runtime(p), p.VRR_GUARD), "w") as f:                          # an earlier boot that died
        f.write("another-boot")
    n = len(calls)
    p._vrr_live_again()
    assert len(calls) == n and not p.settings["wide_vrr_live"]                             # skipped, turned off
    st.update(method="live")
    p.run(p.set_wide_vrr(False))
    assert calls[-1] == "live-off"
    st.update(method=None, live_possible=False)
    r = p.run(p.set_wide_vrr(True))
    assert not r["ok"] and "boot setting" in r["error"]


def m_runtime(p):
    return p._vrr_live_again.__func__.__globals__["RUNTIME_DIR"]


def _target_tuner(fake_hw, monkeypatch, target, auto_fps=True, ceiling=25):
    at, s = _tuner(fake_hw, monkeypatch, ceiling=ceiling)
    clock = {"t": 1000.0}
    monkeypatch.setattr(autotune.time, "monotonic", lambda: clock["t"])
    at._start_target(s, {}, target, auto_fps)

    def run(n):
        for _ in range(n):
            clock["t"] += 1
            at._step()
    return at, s, run


def test_target_fps_fewest_watts_that_hold_it_and_fast_back_up_in_a_heavy_scene(fake_hw, monkeypatch):
    """DeckyTDPTuner's rules: down 1-2 W after 15 s steady at the target, +2 W at once when it misses."""
    at, s, run = _target_tuner(fake_hw, monkeypatch, 60)
    scene = {"fps_per_w": 4.0}
    _Game(at, lambda tdp, *_: min(90.0, scene["fps_per_w"] * tdp))
    assert s.learned.tdp == 17                                   # mid-range start (8..25)
    run(300)
    assert s.learned.tdp == 15 and s.phase == "target"          # 15 W gives 60; 14 W (56) would be under 97 %
    st = at.status()
    assert st["target"] == 60 and st["saves_w"] == 10
    scene["fps_per_w"] = 2.95                                    # a heavy scene: 44 fps at 15 W
    run(12)
    assert s.learned.tdp >= 19 and "under the 60 target" in s.last_action   # seconds, not minutes
    run(60)
    assert s.learned.tdp == 20                                   # 59 fps: back in the band
    at.session = None


def test_a_new_target_leaves_xemu_alone(fake_hw, monkeypatch, tmp_path):
    """set_target() ended every pause, also the one for xemu: Autotune governed the game again on xemu's 60 presented
    fps and took the TDP from the 30 W pick to the 8 W floor within two minutes."""
    pw = power.Power(fake_hw)
    chip = {}
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: chip.update(tdp=w) or True)
    at = autotune.Autotune(fake_hw, pw, "/nonexistent")
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    clock = {"t": 1000.0}
    monkeypatch.setattr(autotune.time, "monotonic", lambda: clock["t"])
    proc = tmp_path / "proc"
    for pid, name in (("1", "systemd"), ("4242", "es-de"), ("4300", "xemu")):
        _w(str(proc / pid / "comm"), name)
    monkeypatch.setattr(autotune, "PROC", str(proc))
    pw.apply_profile("", {"tdp": 30}, False)                          # the pick: Custom 30 W
    at.start("3119184808#xbox/Project Gotham Racing 2", None, 8, 30, False, target=60)
    at.sampler.sample = lambda: autotune.Sample(t=clock["t"], frametime_ms=16.7, fps=60.0, gpu_mhz=1000,
                                                gpu_max_mhz=2400, cpu_pct=30, pkg_w=19.0, gpu_busy=40,
                                                source="presented", shown_fps=None)

    def run(n):
        for _ in range(n):
            clock["t"] += 1
            at._step()
    run(10)
    assert at.status()["own_pace"] == "xemu" and at.session.paused and chip["tdp"] == 30
    at.set_target(30)                                                 # a new target in the panel
    run(120)
    st = at.status()
    assert st["own_pace"] == "xemu" and st["paused"] and chip["tdp"] == 30
    at.set_target(None)                                               # the target switched off: the same
    run(120)
    assert at.session.paused and chip["tdp"] == 30
    at.session = None


def test_target_fps_a_miss_right_after_lowering_makes_that_the_floor(fake_hw, monkeypatch):
    at, s, run = _target_tuner(fake_hw, monkeypatch, 60, ceiling=20)
    _Game(at, lambda tdp, *_: 62.0 if tdp >= 18 else 50.0)      # calm at 18 W, misses under it
    run(200)
    assert s.g_floor == 18 and s.learned.tdp == 18              # tried 17 once, missed, straight back to 18
    tried = len([1 for _ in range(100) if (run(1), s.learned.tdp)[1] == 17])
    assert tried == 0 and s.learned.tdp == 18                    # not again within the floor's 5 minutes
    at.session = None


def test_auto_fps_holds_the_closest_fps_it_can_reach_and_goes_back_up(fake_hw, monkeypatch):
    at, s, run = _target_tuner(fake_hw, monkeypatch, 60)
    cap = {"most": 2.0}
    _Game(at, lambda tdp, *_: cap["most"] * tdp)                 # 25 W gives 50 fps
    run(70)
    assert s.target == 47 and s.unreachable and "out of reach" in s.last_action
    run(200)
    assert s.learned.tdp == 23                                   # 46 fps holds 47 (97 %) with the fewest watts
    cap["most"] = 3.0                                            # a lighter area: 72 fps at 24 W
    run(40)
    assert s.target > 47 and "room again" in s.last_action
    at.session = None


def test_without_auto_fps_an_out_of_reach_target_stays_at_the_full_tdp(fake_hw, monkeypatch):
    at, s, run = _target_tuner(fake_hw, monkeypatch, 60, auto_fps=False)
    _Game(at, lambda tdp, *_: 2.0 * tdp)
    run(120)
    assert s.target == 60 and s.learned.tdp == 25 and s.unreachable and at.status()["best_fps"] == 50
    at.session = None


def test_target_counts_rendered_frames_with_frame_generation(fake_hw, monkeypatch):
    """No XeLL figure, ClawDeck's 2x frame generation on: 120 presented is 60 rendered."""
    at, s, run = _target_tuner(fake_hw, monkeypatch, 60)
    s.fg = 2
    _Game(at, lambda tdp, *_: min(180.0, 8.0 * tdp), source="presented")
    run(300)
    assert s.learned.tdp == 15 and s.multiplier == 2.0          # 120 presented = 60 rendered at 15 W
    at.session = None


def test_most_frames_mode_measures_rendered_frames_with_frame_generation(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch)
    s.fg = 2
    _Game(at, lambda tdp, *_: min(120.0, 6.0 * tdp), source="presented")
    for _ in range(3):
        at._step()
    assert s.learned.max_fps == 60                               # 120 presented / 2
    at.session = None


def test_autotune_target_setting_is_saved_clamped_and_reaches_the_running_session(plugin, monkeypatch):
    p = plugin
    got = []
    monkeypatch.setattr(p.autotune, "set_target", lambda t, a=True: got.append((t, a)))
    r = p.run(p.set_autotune_target(60, True))
    assert r["ok"] and r["target"] == 60 and p.settings["autotune_target"] == 60 and got[-1] == (60, True)
    assert p.run(p.set_autotune_target(5, False))["target"] == 20 and got[-1] == (20, False)   # 20 fps minimum
    r = p.run(p.set_autotune_target(0, True))
    assert r["target"] == 0 and got[-1] == (None, True) and "most frames" in r["summary"]
    started = {}
    monkeypatch.setattr(p.autotune, "start", lambda *a, **k: started.update(k))
    p.settings.update(autotune=True, autotune_target=45, autotune_auto_fps=False, autotune_plugged=True)
    p._start_game_power("123", True)
    assert started["target"] == 45 and started["auto_fps"] is False and started["fg"] == 0


def test_target_fps_gets_the_tdp_moving_and_locked_fast(fake_hw, monkeypatch):
    """From the start to the settled watts in about a minute, then no more changes while the game holds."""
    at, s, run = _target_tuner(fake_hw, monkeypatch, 60, ceiling=30)
    _Game(at, lambda tdp, *_: min(100.0, 5.0 * tdp))            # 12 W gives 60
    assert s.learned.tdp == 19                                   # mid-range start (8..30)
    t = 0
    while s.learned.tdp != 12 and t < 200:
        run(1)
        t += 1
    assert s.learned.tdp == 12 and t <= 60, t
    run(30)                                                      # one probe of 11 W, straight back: the floor
    changes = s.last_change_at
    run(240)
    assert s.learned.tdp == 12 and s.last_change_at == changes and s.g_floor == 12   # locked
    at.session = None


def test_target_fps_a_too_low_start_climbs_in_seconds(fake_hw, monkeypatch):
    at, s, run = _target_tuner(fake_hw, monkeypatch, 60, ceiling=30)
    _Game(at, lambda tdp, *_: min(100.0, 2.4 * tdp))            # needs 25 W for 60
    run(12)
    assert s.learned.tdp >= 25                                   # 19 W -> 25 W within ~12 s
    at.session = None


def test_debug_launch_arms_one_launch_and_never_touches_another_tools_optiscaler(plugin, tmp_path, monkeypatch):
    p = plugin
    rt = p.set_debug_launch.__func__.__globals__["RUNTIME_DIR"]
    game = tmp_path / "dbg-game"
    _w(game / "OptiScaler.ini", "[Log]\nLogToFile=auto\n")
    p.settings = {**(p.settings or {}), "opti_games": {"42": str(game)}}
    owner = {"owner": "other"}
    monkeypatch.setattr(p.set_debug_launch.__func__.__globals__["optiscaler"], "owner", lambda f: owner)
    r = p.run(p.set_debug_launch("42", True))
    assert r["ok"] and r["debug"]["armed"]
    assert open(os.path.join(rt, "launch", "42.debug")).read() == ""          # another tool's ini: left alone
    owner["owner"] = "clawdeck"
    p.run(p.set_debug_launch("42", True))
    assert open(os.path.join(rt, "launch", "42.debug")).read() == f"opti={game}\n"
    assert not p.run(p.set_debug_launch("42", False))["debug"]["armed"]
    assert not p.run(p.set_debug_launch("../x", True))["ok"]


def test_re4_has_no_dlss_so_nothing_is_installed(plugin, tmp_path, monkeypatch):
    """Per-game in RE4 (2023): the install is refused with the reason, nothing goes into the game folder, and
    "own" (Uninstall) still works, so a setup from an older beta can be taken out."""
    from clawdeck import xess
    p = plugin
    game = tmp_path / "steam" / "RESIDENT EVIL 4  BIOHAZARD RE4"
    _w(game / "re4.exe", "x")
    before = sorted(str(q.relative_to(game)) for q in game.rglob("*"))
    monkeypatch.setattr(xess, "steam_install_dir", lambda h, a: str(game))
    rec = p.run(p.game_recommend("2050650"))
    assert rec["pd_upscaler"] and rec["recommended"] is False and not rec["optiscaler"]
    for up in ("xess", "fsr4_xmx"):
        r = p.run(p.game_choose("2050650", up, 2))
        assert not r["ok"] and "no DLSS" in r["error"]
    r = p.run(p.game_apply_recommended("2050650"))
    assert not r["ok"] and "no DLSS" in r["error"]
    assert sorted(str(q.relative_to(game)) for q in game.rglob("*")) == before
    assert p.run(p.game_choose("2050650", "own", 0))["ok"]


def test_a_mangoapp_log_autotune_turned_on_is_deleted_when_the_game_ends(tmp_path, monkeypatch):
    """One row per frame is about 10 MB an hour in the user's home: the log Autotune asked for goes when the game
    ends, with its summary twin; a log another tool keeps on is left alone."""
    from clawdeck import autotune
    sent = []
    monkeypatch.setattr(autotune, "mangoapp_command", lambda c: sent.append(c) or True)
    s = autotune.Sampler.__new__(autotune.Sampler)
    s._mango_done, s._mango_pos, s._mango_asked = [], 0, -1e9
    ours = tmp_path / "mangoapp_2026-11-15_00-10-00.csv"
    ours.write_text("fps,frametime\n")
    s._mango, s._mango_own = str(ours), True
    s.release()
    assert sent == ["logging=0"] and not ours.exists()
    (tmp_path / "mangoapp_2026-11-15_00-10-00_summary.csv").write_text("x\n")   # mangoapp writes it after stopping
    s.release()
    assert not (tmp_path / "mangoapp_2026-11-15_00-10-00_summary.csv").exists() and s._mango_done == []
    theirs = tmp_path / "mangoapp_2026-11-15_01-00-00.csv"
    theirs.write_text("fps,frametime\n")
    s._mango, s._mango_own = str(theirs), False
    s.release()
    assert theirs.exists()
