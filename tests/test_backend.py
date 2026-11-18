"""Backend tests against a fake sysfs tree (no hardware needed): detection, clamping, revert, profiles,
autotune control logic. Run: python3 -m pytest -q tests"""
import csv
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
    """A game whose fps follows the power it gets: fps(tdp, cpu_max_pct, gpu_min_mhz, boost), one sample a look."""
    def __init__(self, at, fps, **smp):
        self.at, self.fps, self.smp, self.t = at, fps, smp, time.monotonic()
        at.sampler.sample = self.sample
        at.sampler.frames_ms = []                  # no per-frame log: that look's frames at their average

    def sample(self):
        s = self.at.session
        self.t += s.look_s
        l = s.learned
        f = self.fps(l.tdp, l.cpu_max_pct, l.gpu_min_mhz, l.boost)
        base = dict(gpu_mhz=1500, gpu_max_mhz=2400, cpu_pct=40, pkg_w=float(l.tdp), gpu_busy=70, source="presented", shown_fps=f)
        base.update(self.smp)
        return autotune.Sample(t=self.t, frametime_ms=1000.0 / f if f else None, fps=f, **base)

    def run(self, seconds):
        end = self.t + seconds
        while self.t < end - 1e-9 and self.at.session is not None:
            self.at._step()
        return self


def _tuner(fake_hw, monkeypatch, ceiling=20, target=None, **learned):
    pw = power.Power(fake_hw)
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: True)
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    monkeypatch.setattr(autotune, "own_pace_emulator", lambda proc=None: None)
    at = autotune.Autotune(fake_hw, pw, log_dir="/nonexistent", csv_log=None)
    at.start("1", {"ceiling": ceiling, "target": target, **learned} if learned else None, 8, ceiling, False, target=target)
    return at, at.session


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


def test_a_new_floor_is_in_effect_at_once(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, tdp=20)
    at.set_floor(10)
    assert s.floor_tdp == 10 and s.learned.tdp >= 10 and s.cfg.floor == 10
    at.session = None


def test_a_raised_floor_ends_a_second_try_under_it(fake_hw, monkeypatch):
    from test_governor import _into_retest
    g, held = _into_retest()
    at, s = _tuner(fake_hw, monkeypatch, ceiling=30, target=60)
    s.gov, s.cfg, s.learned.tdp = g.st, g.cfg, g.limit
    assert s.gov.mode == "probe" and s.gov.retest and s.learned.tdp == held
    at.set_floor(held + 1)
    g.limit = s.learned.tdp
    g.run(0.2)
    assert s.gov.mode == "hold" and not s.gov.retest
    g.run(15)
    at.session = None
    assert not any("own dip" in n for _, n in g.notes), g.notes[-3:]


def test_autotune_starts_where_the_game_held_under_the_same_limit_and_target_else_mid_range(fake_hw, monkeypatch):
    pw = power.Power(fake_hw)
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: True)
    at = autotune.Autotune(fake_hw, pw, "/nonexistent")
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    saved = {"tdp": 14, "max_fps": 60.0, "fps": 58.0, "ceiling": 20, "cpu_max_pct": 90, "confidence": 3}   # + an old key
    at.start("1", saved, 8, 20, False)
    assert at.session.learned.tdp == 14                         # an earlier build's result: the limit it ended with
    at.start("1", {**saved, "start": 16}, 8, 20, False)
    assert at.session.learned.tdp == 16                         # where it held, as saved now
    at.start("1", {**saved, "start": 16}, 8, 26, False)         # another limit: nothing known, mid-range
    assert at.session.learned.tdp == 17 and at.session.learned.ceiling == 26
    at.start("1", {**saved, "start": 16}, 8, 20, False, target=60)   # another goal: the same
    assert at.session.learned.tdp == 14 and at.session.user_target == 60
    at.start("1", {**saved, "start": 16}, 8, 20, False, at_limit=True)   # picked in the game just now: as picked
    assert at.session.learned.tdp == 20
    at.start("1", None, 8, 30, False)
    assert at.session.learned.tdp == 19
    assert (at.session.user_target, at.session.auto_fps) == (120, True)   # no target set: the screen's highest refresh rate
    at.start("1", None, 8, 30, False, auto_fps=False)
    assert at.session.auto_fps is True                          # ... which Auto FPS turns into the most frames the limit gives
    at.session = None


def test_autotune_pause_stops_changes(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, tdp=20)
    g = _Game(at, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(30)   # 60 fps from 15 W up
    t = s.learned.tdp
    assert 15 <= t < 20                                         # it works down from where it started
    at.pause()
    assert s.learned.tdp == 20                                  # the limit is yours again, not where Autotune was
    g.run(30)
    assert s.learned.tdp == 20 and at.status()["saves_w"] is None
    at.session = None


def test_autotune_a_refused_tdp_is_not_learned(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, tdp=20)
    monkeypatch.setattr(at.power, "set_tdp", lambda w, plugged=None: False)   # e.g. HHD's TDP controls not enabled
    _Game(at, lambda *_: 60.0).run(30)
    assert s.learned.tdp == 20 and s.gov.limit == 20            # the governor goes on from what is on the chip
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
    for _ in range(3):
        check(75, 12, tdp, set_w, cap, limit=30)
    assert g.clock_pct == 90 and caps[-1] == 90
    for _ in range(3):
        check(75, 12, tdp, set_w, cap, limit=30)
    assert g.clock_pct is None and caps[-1] is None and g.status()["line"] is None


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
    g = _Game(at, lambda tdp, *_: 2.0 * tdp).run(15)       # every watt buys frames: it stays within a watt of 25 W
    assert s.learned.max_fps == pytest.approx(50, abs=0.5) and s.learned.tdp >= 24
    assert at.thermal_step(23) and s.learned.tdp == 23 and s.thermal_cap == 23
    assert at.status()["thermal_cap"] == 23 and s.last_action == "thermal: stepped to 23 W"
    for _ in range(60):
        at._step()
        assert s.learned.tdp <= 23                         # never back above the step, whatever it is doing
    at.session = None


def test_a_cpu_clock_cap_from_the_guard_starts_the_search_over(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, tdp=20)
    _Game(at, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(40)
    assert s.gov.fail
    at.thermal_clock(80)
    assert s.clock_pct == 80 and not s.gov.fail and s.last_action == "thermal: CPU clock capped at 80 %"
    s.gov.lo, s.gov.hi, s.gov.ratio, s.gov.ref, s.gov.seen_hi, s.gov.fail[12] = 50.0, 50.0, 1.0, 50.0, 50.0, [1e12, 1, None, False, 0.0]
    at.thermal_clock(None)
    assert s.clock_pct is None and s.last_action == "thermal: CPU clock uncapped"
    assert (s.gov.lo, s.gov.ratio, s.gov.ref, s.gov.seen_hi, s.gov.fail) == (None, None, None, None, {})   # all of the cap's
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


def test_autotune_governs_on_an_8s_window_and_gives_the_firmwares_back_when_it_stops(fake_hw, monkeypatch, tmp_path):
    t = _with_tau(fake_hw, tmp_path)
    at, s = _tuner(fake_hw, monkeypatch)
    assert sysfs.read_int(t) == 8_000_000 and s.short_tau              # from the start: a step down has to bind
    g = _Game(at, lambda tdp, *_: min(60.0, 3.0 * tdp))
    for _ in range(200):
        at._step()
        assert sysfs.read_int(t) == 8_000_000                        # the whole session, no switching
    assert at.status()["pl1_window_s"] == 8.0
    at.stop()                                                        # game closed: everything reverts
    assert sysfs.read_int(t) == TAU_FW


def test_autotune_gives_the_window_back_when_paused_and_stops_asking_if_refused(fake_hw, monkeypatch, tmp_path):
    t = _with_tau(fake_hw, tmp_path)
    at, s = _tuner(fake_hw, monkeypatch)
    g = _Game(at, lambda *_: 50.0).run(2)
    assert sysfs.read_int(t) == 8_000_000
    at.pause("CPU ceiling by hand")
    at._step()
    assert sysfs.read_int(t) == TAU_FW and not s.short_tau           # the user has the controls
    at.session = None
    pw = power.Power(fake_hw)
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: True)
    tries = []
    monkeypatch.setattr(pw, "set_pl1_window", lambda us: tries.append(us) or False)
    at = autotune.Autotune(fake_hw, pw, log_dir="/nonexistent", csv_log=None)
    at.start("1", None, 8, 20, False)
    _Game(at, lambda *_: 50.0).run(5)
    assert tries == [8_000_000] and at.session.tau_refused and sysfs.read_int(t) == TAU_FW
    at.session = None


def test_a_pl2_set_by_hand_is_the_picks_not_the_governed_limits(fake_hw, tmp_path, monkeypatch):
    root = tmp_path / "rapl"
    _w(str(root / "constraint_0_power_limit_uw"), 25_000_000)
    _w(str(root / "constraint_1_power_limit_uw"), 37_000_000)
    fake_hw.paths.rapl_pl1, fake_hw.paths.rapl_pl2 = str(root / "constraint_0_power_limit_uw"), str(root / "constraint_1_power_limit_uw")
    monkeypatch.setattr(power, "thermald_active", lambda: False)
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    pw = power.Power(fake_hw)
    pw.pl2_w = 45
    pw.set_tdp(30, False)
    assert sysfs.read_int(fake_hw.paths.rapl_pl2) == 45_000_000      # the pick: your PL2
    at = autotune.Autotune(fake_hw, pw, log_dir="/nonexistent", csv_log=None)
    at.start("1", None, 8, 30, False)
    assert sysfs.read_int(fake_hw.paths.rapl_pl1) == 19_000_000 and sysfs.read_int(fake_hw.paths.rapl_pl2) == 21_000_000
    at.pause("manual change")
    at._give_back(at.session)
    assert sysfs.read_int(fake_hw.paths.rapl_pl1) == 30_000_000 and sysfs.read_int(fake_hw.paths.rapl_pl2) == 45_000_000
    at.session = None


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
    """Autotune runs for a while (steps: seconds) in a game that gets 60 fps from 15 W up (4 fps per watt below)."""
    s = p.autotune.session
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(steps)
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
    assert p.chip() == 10 and p.autotune.session.appid == esde and p.autotune.session.ceiling_tdp == 12   # mid-range
    assert _frontend_event(p, "start", "ps3", "/roms/ps3/Demon's Souls.iso", "Demon's Souls")
    assert p.running_key == demons and p.running_appid == esde and p.autotune.session.appid == demons
    assert p.chip() == 10 and p.autotune.session.ceiling_tdp == 12     # never played: mid-range under ES-DE's pick
    p.run(p.set_tdp(18))                                               # a pick in this game is this game's
    assert p.profiles.own(demons, False)["tdp"] == 18 and p.profiles.own(esde, False)["tdp"] == 12
    slot = p.run(p.power_slot(esde))
    assert slot["title"] == "Demon's Souls" and slot["tdp"] == 18
    assert p.run(p.get_autotune())["game"] == esde                     # Steam's app: the panel doesn't report it again
    assert _frontend_event(p, "end", "ps3", "/roms/ps3/Demon's Souls.iso", "Demon's Souls")
    assert p.running_key == esde and p.chip() == 10 and p.autotune.session.appid == esde
    p.profiles.set(esde, False, {"learned": {"tdp": 9, "ceiling": 12, "max_fps": 200}})   # Autotune's result in ES-DE's menus
    assert _frontend_event(p, "start", "ps3", "/roms/ps3/Ratchet.iso", "Ratchet")
    assert p.running_key == ratchet and p.chip() == 10                 # a new game: under ES-DE's pick, not its learned 9 W
    assert _frontend_event(p, "start", "ps3", "/roms/ps3/Ratchet.iso", "Ratchet") is None   # the same game: no restart
    p.run(p.game_stopped(esde))
    assert p.running_key is None and p.running_title is None


def test_what_autotune_learned_is_never_saved_or_shown_as_your_pick(plugin):
    """Your pick (`base`, `tdp`: the slider, a profile, Use default) and what Autotune learned (`learned`) are two
    records in a game's slot, and only your actions write the first. A game that held 8 W for minutes keeps no pick:
    the slider shows your 30 W, and the next start works under 30 W."""
    p = plugin
    p.settings["autotune_target"] = 60
    p.run(p.set_tdp(30))                                               # your pick for every game
    p.run(p.game_started("42"))
    s = p.autotune.session
    s.learned.tdp, s.learned.start, s.settled = 8, 8, True             # 60 fps at 8 W, for minutes
    p.run(p.game_stopped("42"))
    own = p.profiles.own("42", False)
    assert own["learned"]["start"] == 8 and "tdp" not in own and not own.get("base")
    slot = p.run(p.power_slot("42"))
    assert slot["tdp"] is None and slot["base"] is None                # no "Saved for this game"
    assert slot["pick"]["tdp"] == 30 and slot["pick"]["from"] != "game"
    p.run(p.game_started("42"))
    assert p.autotune.session.ceiling_tdp == 30 and p.autotune.session.learned.start == 8   # learning, under your limit


def test_what_a_frontends_menus_hold_is_never_the_limit_of_its_games(plugin):
    """ES-DE's menus hold 60 fps at 8 W, and a game ES-DE starts without a pick of its own starts from ES-DE's pick.
    What held is learning under the game's own key and never a pick: ES-DE's slot gets no TDP, and its games work
    under your limit."""
    p = plugin
    esde = "3119184808"
    p.settings["autotune_target"] = 60
    p.run(p.set_tdp(30))
    p.run(p.game_started(esde))
    s = p.autotune.session
    s.learned.tdp, s.learned.start, s.settled = 8, 8, True             # the menus: 60 fps at 8 W, for minutes
    assert _frontend_event(p, "start", "xbox", "/roms/xbox/PGR2.iso", "PGR2")
    own = p.profiles.own(esde, False)
    assert own["learned"]["start"] == 8 and "tdp" not in own and not own.get("base")    # learning, not a pick
    s = p.autotune.session
    assert s.ceiling_tdp == 30 and s.learned.start is None             # your limit, and nothing of the menus
    assert _frontend_event(p, "end", "xbox", "/roms/xbox/PGR2.iso", "PGR2")
    p.autotune.session.learned.start, p.autotune.session.settled = 8, True
    p.run(p.game_stopped(esde))                                        # ES-DE closed from its menus
    own = p.profiles.own(esde, False)
    assert "tdp" not in own and not own.get("base")
    p.run(p.game_started(esde))
    assert _frontend_event(p, "start", "xbox", "/roms/xbox/Halo.iso", "Halo")
    assert p.autotune.session.ceiling_tdp == 30


def test_a_frontend_games_own_settings_count_without_a_power_choice_of_its_own(plugin):
    """A game started by a frontend whose own slot holds settings but no power choice ("autotune": false for this
    game): those settings are the game's, not the frontend's (Autotune ran in it)."""
    p = plugin
    esde, game = "3119184808", "3119184808#ps3/Demon's Souls"
    p.profiles.set(esde, False, {"base": "custom", "tdp": 12})
    p.profiles.set(game, False, {"autotune": False, "cpu_max_pct": 70})
    p.run(p.game_started(esde))
    assert _frontend_event(p, "start", "ps3", "/roms/ps3/Demon's Souls.iso", "Demon's Souls")
    assert p._profile_source(game, False) == game and p.autotune.session is None


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
    assert p.chip() == 14 and p.autotune.session.ceiling_tdp == 20     # no history: mid-range, under your 20 W
    s = _lower(p, 120)
    assert s.learned.tdp < 20 and p.chip() == s.learned.tdp       # Autotune uses less than your 20 W
    p.run(p.game_stopped("42"))
    assert p.chip() == 20                                          # your limit, not the firmware's 25 W
    assert sysfs.read_int(p.hw.paths.rapl_pl1) == 20_000_000
    assert p.profiles.own("42", False)["learned"]["start"] < 20    # where it held is kept for the next start
    p.run(p._unload())                                             # unloading still reverts everything
    assert sysfs.read_int(p.hw.paths.rapl_pl1) == FW_PL1 and p.thermald["active"]


def test_never_picked_on_this_power_source_leaves_the_firmware_and_thermald(plugin):
    p = plugin
    p.run(p.game_started("42"))                                    # default for battery: Battery 12 W
    assert p.chip() == 10 and not p.thermald["active"]
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


def test_autotunes_own_maximum_is_the_limit_where_autotune_governs_and_the_tdp_setting_where_not(plugin):
    """Autotune off: the TDP setting is the limit. Autotune on: a target under Autotune's own maximum (none set: the
    TDP setting). It is never saved as the TDP setting, and a change in a running game starts the game over."""
    p = plugin
    assert p.run(p.set_tdp(26))["ok"] and p.chip() == 26               # the TDP setting, in the menus
    st = p.run(p.get_power())
    assert st["autotune_max"] == 26 and st["autotune_governs"]         # none set: the TDP setting
    r = p.run(p.set_autotune_max(20))
    assert r["ok"] and r["max_w"] == 20 and p.settings["autotune_max"] == 20
    assert p.chip() == 20                                              # the menus too: the TDP setting does nothing
    p.run(p.game_started("7"))
    s, st = p.autotune.session, p.run(p.get_power())
    assert s.ceiling_tdp == 20 and p.chip() <= 20 and st["limit_w"] == 20 and st["autotune_max"] == 20
    assert st["autotune_governs"]
    r = p.run(p.set_autotune_max(24))                                  # moved in the game
    s = p.autotune.session
    assert r["ok"] and s.ceiling_tdp == 24 and p.chip() == 24 and not s.paused
    assert p.settings["custom_tdp"] == 26 and p.profiles.own("7", False).get("tdp") != 24
    assert p.run(p.set_autotune_max(99))["max_w"] == 35 and p.autotune.session.ceiling_tdp == 35   # the model's range
    p.run(p.set_autotune_max(24))
    assert p.run(p.set_setting("autotune", False)) and p.chip() == 26  # Autotune off: the TDP setting
    assert not p.run(p.get_power())["autotune_governs"]
    p.run(p.game_stopped("7"))
    assert p.chip() == 26 and p.run(p.set_setting("autotune", True)) and p.chip() == 24   # the menus follow the switch
    p.settings.update(autotune=True, autotune_plugged=False)           # on the charger Autotune stays off (Advanced)
    p.ac["plugged"] = True
    p.run(p.game_started("8"))
    st = p.run(p.get_power())
    assert p.autotune.session is None and not st["autotune_governs"]
    assert p.chip() == p.run(p.power_slot("8"))["pick"]["tdp"] != 24
    p.run(p.game_stopped("8"))
    p.ac["plugged"] = False
    assert p.run(p.set_autotune_max(None))["max_w"] is None and p.settings["autotune_max"] is None
    assert p.run(p.get_power())["autotune_max"] == 26
    p.run(p.game_started("7"))
    assert p.autotune.session.ceiling_tdp == 26                        # the TDP setting again
    p.run(p.game_stopped("7"))



def test_the_guard_in_the_menus_steps_back_up_to_autotunes_maximum_not_the_tdp_setting(plugin, tmp_path):
    """No game, Autotune on: its maximum is the limit in the menus. After a step down the temperature guard gives the
    watts back up to that maximum, not up to the TDP setting; with Autotune off, up to the TDP setting."""
    p = plugin
    t = tmp_path / "pkg_temp"
    p.guard = thermal.Guard(str(t), "x86_pkg_temp")
    for on, setting, own, top in ((True, 26, 20, 20), (True, 18, 24, 24), (False, 26, 20, 26)):
        p.run(p.set_setting("autotune", on))
        assert p.run(p.set_tdp(setting))["ok"]
        p.run(p.set_autotune_max(own))
        assert p.chip() == top
        seen = []
        for c in [97] + [70] * 30:
            _w(t, c * 1000)
            p.run(p._guard_check())
            seen.append(p.chip())
        assert min(seen) < top and max(seen) <= top and seen[-1] == top, (on, setting, own, seen)


def test_a_game_with_autotune_off_for_it_keeps_the_tdp_setting_and_a_movable_slider(plugin):
    p = plugin
    p.run(p.set_tdp(26))
    p.run(p.set_autotune_max(20))
    p.profiles.set("9", False, {"autotune": False})
    p.run(p.game_started("9"))
    assert p.autotune.session is None and p.chip() == 26 and not p.run(p.get_power())["autotune_governs"]
    p.run(p.set_autotune_max(18))                                      # not this game's limit: nothing starts over
    assert p.autotune.session is None and p.chip() == 26
    p.run(p.game_stopped("9"))


def test_a_pick_in_a_game_is_the_new_limit_at_once_and_comes_back_at_the_next_start(plugin):
    p = plugin
    p.run(p.game_started("7"))
    s = _lower(p)
    assert s.ceiling_tdp == 12 and p.chip() == s.learned.tdp
    p.autotune.pause("manual CPU ceiling")
    r = p.run(p.apply_profile("balanced", "7"))
    s = p.autotune.session
    assert r["ok"] and p.chip() == 17 and r["limit_w"] == 17 and r["autotune_w"] == 17
    assert s.ceiling_tdp == 17 and s.learned.tdp == 17 and not s.paused   # picked in the game: on the chip as picked
    assert s.learned.max_fps is None                               # measured again from the new limit
    r = p.run(p.set_tdp(26))                                       # the slider, in the game
    assert r["ok"] and p.chip() == 26 and p.autotune.session.ceiling_tdp == 26
    assert p.profiles.own("7", False)["base"] == "custom" and p.profiles.own("7", False)["tdp"] == 26
    p.run(p.game_stopped("7"))
    assert p.chip() == 26                                          # the last pick on battery
    p.run(p.apply_profile("saver"))                                # a different pick for all games, in the menus
    assert p.chip() == 10
    p.run(p.game_started("7"))
    assert p.chip() == 17 and p.autotune.session.ceiling_tdp == 26  # the game's own pick; mid-range under it
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
    assert r["changed"] and p.chip() == 22 and p.autotune.session.ceiling_tdp == 35 and p.autotune.plugged
    assert p.profiles.own("42", False).get("learned")              # battery's learning kept in its own slot
    p.run(p.game_stopped("42"))
    assert p.chip() == 35                                          # exit on the charger: the plugged-in pick
    p.ac["plugged"] = False
    r = p.run(p.power_source_changed(False))
    assert r["changed"] and p.chip() == 20                         # unplugged with no game: the battery pick
    r = p.run(p.resumed())
    assert r["reapplied"] and p.chip() == 20


def test_autotune_saves_only_a_finished_result(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch)
    _Game(at, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(5)
    r = at.stop(revert=False)
    assert r["settled"] is False and "still learning" in r["summary"] and r["learned"]["start"] is None
    at, s = _tuner(fake_hw, monkeypatch)
    _Game(at, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(300)       # 60 fps from 15 W up
    r = at.stop(revert=False)
    assert r["settled"] and r["learned"]["start"] in (15, 16)
    assert r["summary"].startswith(f"Autotune held 60 fps with {r['learned']['start']} W of your 20 W") and "saves" in r["summary"]


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


def test_autotune_thermal_release_lifts_the_cap_and_raises_nothing_by_itself(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, ceiling=25, tdp=25)
    assert at.thermal_step(19) and s.thermal_cap == 19 and s.learned.tdp == 19
    assert at.thermal_release(21) and s.thermal_cap == 21 and s.learned.tdp == 19
    assert at.thermal_release(25) and s.thermal_cap is None and s.learned.tdp == 19
    assert not at.thermal_release(25)
    # ... but what the watts it got back give is looked at: a game that needs them gets them
    _Game(at, lambda tdp, *_: 2.0 * tdp).run(20)
    assert s.learned.tdp >= 24
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
    g = _Game(at, lambda *_: None)                                   # no mangoapp row, no MangoHud log, no XeLL log
    assert s.learned.tdp == 14                                       # mid-range, until something is known
    g.run(autotune.NO_FPS_S - 1)
    assert not s.paused and not at.status()["no_frames"]
    g.run(2)
    st = at.status()
    assert s.paused and st["no_frames"] and s.learned.tdp == 20      # your limit is back on the chip
    at.session = None


def test_autotune_takes_over_when_the_first_frames_come_late(fake_hw, monkeypatch):
    """Order of the Sinking Star shows its first frame 54 s after launch: autotune gave up at 30 s for the whole
    session. Giving up puts your pick back and waits; frames after that hand it back to autotune. A manual change
    still pauses it for good."""
    at, s = _tuner(fake_hw, monkeypatch)
    g = _Game(at, lambda *_: None).run(autotune.NO_FPS_S + 20)
    assert s.paused and s.no_frames and s.learned.tdp == 20
    g.fps = lambda tdp, *_: min(60.0, 4.0 * tdp)
    at._step()
    assert not s.paused and not s.no_frames and not at.status()["no_frames"]
    g.run(30)
    assert s.learned.tdp < 20                                    # it governs from your limit down
    at.pause("manual CPU ceiling")
    s.no_frames = True                                           # even if it had given up before
    at.pause("again")
    at._step()
    assert s.paused and not s.no_frames                          # yours stays yours
    at.session = None


def test_autotune_without_a_frame_rate_gives_back_the_limit_you_picked(fake_hw, monkeypatch):
    """What start() applied stayed on the chip after autotune gave up on a game without a frame rate: a start under
    your limit (here the 17 W the game held last time; Enshrouded, 2026-11-12: 22 W of a 35 W pick, 30-40 fps instead
    of 60). Now the pick goes back. The CPU and GPU levers are the pick's from start to end: Autotune doesn't set them."""
    pw = power.Power(fake_hw)
    chip = {}
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: chip.update(tdp=w) or True)
    at = autotune.Autotune(fake_hw, pw, "/nonexistent")
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    pw.apply_profile("", {"tdp": 35, "cpu_max_pct": 85, "boost": False}, False)       # the pick: Custom 35 W
    at.start("1", {"tdp": 17, "ceiling": 35, "max_fps": 60.0, "cpu_max_pct": 90, "boost": True, "gpu_min_mhz": 1300},
             8, 35, False)                                                            # saved by an earlier build
    p = fake_hw.paths
    assert chip["tdp"] == 17 and sysfs.read_int(p.cpu_max_perf_pct) == 85 and sysfs.read_int(p.cpu_no_turbo) == 1
    assert sysfs.read_int(p.gpu_min_freq) == 300
    _Game(at, lambda *_: None, gpu_busy=99).run(autotune.NO_FPS_S + 1)
    st = at.status()
    assert st["no_frames"] and st["paused"] and st["tdp"] == 35 and chip["tdp"] == 35
    assert sysfs.read_int(p.cpu_max_perf_pct) == 85 and sysfs.read_int(p.cpu_no_turbo) == 1
    at.session = None


def test_pausing_autotune_gives_back_what_autotune_changed_and_keeps_your_change(fake_hw, monkeypatch):
    """After Autotune was switched off in a game, or after one manual change, its own TDP stayed on the chip until the
    game ended. Now the pick goes back and the change made by hand stays. The governor never moves the CPU and GPU
    levers: they stay as the pick set them, whatever an older build saved for the game."""
    pw = power.Power(fake_hw)
    chip = {}
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: chip.update(tdp=w) or True)
    at = autotune.Autotune(fake_hw, pw, "/nonexistent")
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    pw.apply_profile("", {"tdp": 35, "cpu_max_pct": 85, "boost": False}, False)       # the pick: Custom 35 W
    at.start("1", {"tdp": 17, "ceiling": 35, "max_fps": 60.0, "cpu_max_pct": 90, "boost": True, "gpu_min_mhz": 1300},
             8, 35, False)                                            # what an older build saved for the game
    p = fake_hw.paths
    assert chip["tdp"] < 35 and sysfs.read_int(p.cpu_max_perf_pct) == 85 and sysfs.read_int(p.cpu_no_turbo) == 1
    at.pause("manual GPU clocks")                                     # main.py: pause(), then the user's write
    pw.set_gpu_window(900, None)
    assert at.session.paused and chip["tdp"] == 35 and at.status()["tdp"] == 35 and not pw.governed
    assert sysfs.read_int(p.gpu_min_freq) == 900                      # yours
    at.pause("manual CPU ceiling")                                    # a second change by hand: the first one stays
    pw.set_cpu_max_pct(70)
    at.pause("autotune switched off")
    assert sysfs.read_int(p.gpu_min_freq) == 900 and sysfs.read_int(p.cpu_max_perf_pct) == 70 and chip["tdp"] == 35
    at.session = None


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



def test_switching_autotune_off_in_a_hot_game_keeps_the_guards_step_and_the_guard_gives_back_the_tdp_setting(plugin):
    """Autotune off in a game: the TDP setting is the limit again, under the temperature guard's step, whatever
    Autotune's own maximum was and also after a manual change paused Autotune; when the guard allows the whole limit
    again, the TDP setting comes back, not Autotune's maximum."""
    p = plugin
    for appid, setting, own, step, paused in (("7", 26, None, 12, False), ("8", 26, 20, 12, False),
                                              ("9", 26, 30, 12, False), ("10", 22, 30, 24, False),
                                              ("11", 26, 20, 12, True)):
        p.run(p.set_setting("autotune", True))
        assert p.run(p.set_tdp(setting))["ok"]
        p.run(p.set_autotune_max(own))
        p.run(p.game_started(appid))
        assert p.autotune.thermal_step(step) and p.chip() <= step
        if paused:
            p.autotune.pause("manual change")
        assert p.run(p.set_setting("autotune", False))
        assert p.chip() == min(setting, step) and p.autotune.held_w() == min(setting, step)
        p.autotune.thermal_release(setting)
        assert p.chip() == setting
        p.run(p.game_stopped(appid))


def test_a_tdp_pick_in_a_game_autotune_was_switched_on_in_is_the_limit(plugin):
    """Autotune switched on while a game runs does not govern that game, and the panel shows the TDP slider as the
    limit: a pick there is the limit at once, also with a maximum of Autotune's own. The next start is governed."""
    p = plugin
    for appid, own in (("7", None), ("8", 20), ("9", 30)):
        p.run(p.set_setting("autotune", False))
        assert p.run(p.set_tdp(26))["ok"]
        p.run(p.set_autotune_max(own))
        p.run(p.game_started(appid))
        assert p.chip() == 26 and p.autotune.session is None
        p.run(p.set_setting("autotune", True))
        assert not p.run(p.get_power())["autotune_governs"] and p.chip() == 26
        assert p.run(p.set_tdp(28))["ok"]
        assert p.chip() == 28 and p.autotune.session is None and not p.run(p.get_power())["autotune_governs"]
        p.run(p.set_autotune_max(18))                                  # not this game's limit: nothing starts over
        assert p.chip() == 28 and p.autotune.session is None
        p.run(p.game_stopped(appid))
        p.run(p.game_started(appid))
        assert p.autotune.session is not None and p.autotune.session.ceiling_tdp == 18
        p.run(p.game_stopped(appid))


def test_switching_autotune_off_in_a_game_keeps_the_start_level_where_the_game_held(plugin):
    """Autotune's maximum 25 W, TDP setting 12 W; the game holds at 20 W (60 fps from 20 W). Autotune switched off in
    the game, then the game ends: the start level saved for its next start is 20 W, where it held. It was the TDP
    setting the session was handed at the switch (12 W), and the next start lost frames from there."""
    p = plugin
    p.run(p.set_setting("autotune", True))
    assert p.run(p.set_tdp(12))["ok"]
    p.run(p.set_autotune_max(25))
    p.run(p.game_started("7"))
    s = p.autotune.session
    _Game(p.autotune, lambda tdp, *_: min(60.0, 3.0 * tdp)).run(240)
    held = max(s.gov.held_s, key=s.gov.held_s.get)
    assert held == 20, s.gov.held_s
    p.run(p.set_setting("autotune", False))
    p.run(p.game_stopped("7"))
    saved = p.profiles.own("7", False).get("learned") or {}
    assert saved.get("ceiling") == 25 and saved.get("start") == held, saved


@pytest.mark.parametrize("state", ["switched on in the game", "unplugged, not yet reported"])
@pytest.mark.parametrize("setting, own", [(26, 15), (14, 25)])
def test_the_guard_in_a_game_autotune_does_not_govern_steps_back_up_to_the_limit_on_the_chip(plugin, tmp_path, setting,
                                                                                             own, state):
    """No session: Autotune switched on during the game, or the game on the charger with "Autotune on the charger"
    off and the charger pulled before Steam reports it. The guard gives the watts back up to the TDP setting on the
    chip, not to Autotune's maximum; the TDP slider is not greyed."""
    p = plugin
    t = tmp_path / "pkg_temp"
    p.guard = thermal.Guard(str(t), "x86_pkg_temp")
    p.settings["autotune"] = state != "switched on in the game"
    p.autotune.enabled = p.settings["autotune"]
    p.settings["autotune_plugged"] = False
    for plugged in (False, True):
        p.ac["plugged"] = plugged
        assert p.run(p.set_tdp(setting))["ok"]
    p.ac["plugged"] = state != "switched on in the game"
    p.run(p.set_autotune_max(own))
    p.run(p.game_started("7"))
    if state == "switched on in the game":
        p.run(p.set_setting("autotune", True))
    p.ac["plugged"] = False
    assert p.autotune.session is None and p.chip() == setting
    assert not p.run(p.get_power())["autotune_governs"]
    seen = []
    for c in [97] + [70] * 30:
        _w(t, c * 1000)
        p.run(p._guard_check())
        seen.append(p.chip())
    assert min(seen) < setting and max(seen) <= setting and seen[-1] == setting, seen


def test_switching_autotune_off_in_a_hot_game_it_does_not_govern_keeps_the_guards_step(plugin, tmp_path):
    """No session (on the charger, "Autotune on the charger" off): the TDP setting 26 W is the game's limit, and the
    guard stepped it down. Autotune switched off: the limit stays under the guard's step."""
    p = plugin
    p.ac["plugged"] = True
    p.settings["autotune_plugged"] = False
    t = tmp_path / "pkg_temp"
    p.guard = thermal.Guard(str(t), "x86_pkg_temp")
    p.run(p.set_setting("autotune", True))
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.set_autotune_max(15))
    p.run(p.game_started("7"))
    _w(t, 97 * 1000)
    p.run(p._guard_check())
    stepped = p.chip()
    assert p.autotune.session is None and stepped < 26
    p.run(p.set_setting("autotune", False))
    assert p.chip() == stepped


def test_a_new_target_in_a_game_autotune_was_switched_back_on_in_does_not_govern_it(plugin):
    """TDP setting 26 W, Autotune's maximum 15 W. In a game: Autotune switched off, then on again (the game is not
    governed until its next start), then a new target. The session stays paused at the TDP setting; it governed
    again, with 26 W as its ceiling."""
    p = plugin
    p.run(p.set_setting("autotune", True))
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.set_autotune_max(15))
    p.run(p.game_started("7"))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 2.0 * tdp)).run(20)
    p.run(p.set_setting("autotune", False))
    p.run(p.set_setting("autotune", True))
    p.run(p.set_autotune_target(60, True))
    s = p.autotune.session
    seen = []
    for _ in range(100):
        p.autotune._step()
        seen.append(p.chip())
    assert s.paused and set(seen) == {26}, (s.paused, sorted(set(seen)))


def test_an_esde_game_started_after_autotune_was_switched_on_is_governed(plugin):
    """ES-DE launched with Autotune off; Autotune switched on in ES-DE's menus (not governed: no frame log there);
    then a game from ES-DE. It starts after the switch: governed. It never was, until ES-DE itself was started again."""
    p = plugin
    p.settings["autotune"] = False
    p.autotune.enabled = False
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.game_started("100"))
    p.run(p.set_setting("autotune", True))
    assert p.autotune.session is None
    _frontend_event(p, "start", "ps2", "/roms/ps2/a.iso", "Game A")
    assert p.autotune.session is not None and p.run(p.get_power())["autotune_governs"]


def test_autotune_on_the_charger_switched_on_in_a_game_on_the_charger_governs_it_at_once(plugin):
    """On the charger, "Autotune on the charger" off at the start, switched on in the game (launched with Autotune on:
    it has its frame log). Autotune governs it at once under its maximum (15 W), and the TDP slider is greyed; no
    session ran, the slider was free, and a pick put 15 W on the chip and started one."""
    p = plugin
    p.ac["plugged"] = True
    p.settings["autotune_plugged"] = False
    p.run(p.set_setting("autotune", True))
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.set_autotune_max(15))
    p.run(p.game_started("7"))
    assert p.autotune.session is None and p.chip() == 26
    p.run(p.set_setting("autotune_plugged", True))
    assert p.autotune.session is not None and p.chip() <= 15 and p.run(p.get_power())["autotune_governs"], p.chip()


@pytest.mark.parametrize("first", ["on", "off"])
def test_the_charger_switch_in_a_game_leaves_autotune_on_battery_as_it_was(plugin, first):
    """The charger switch changes what Autotune does on the charger only. A game launched with Autotune on, governed
    on battery, plugged in, the charger switch switched (`first`), unplugged: governed on battery again. It stayed at
    the TDP setting for the rest of the game ("turned on during this game")."""
    p = plugin
    p.ac["plugged"] = False
    p.settings["autotune_plugged"] = first == "off"
    p.run(p.set_setting("autotune", True))
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.game_started("7"))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(30)
    p.ac["plugged"] = True
    p.run(p.power_source_changed(True))
    p.run(p.set_setting("autotune_plugged", first == "on"))
    p.ac["plugged"] = False
    p.run(p.power_source_changed(False))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(60)
    s = p.autotune.session
    assert s is not None and not s.paused and p.chip() < 26, (s is not None and s.paused, p.chip())


def test_the_charger_switch_off_keeps_a_game_autotune_was_switched_on_in_ungoverned(plugin):
    """Autotune switched on during the game (no frame log), on the charger; "Autotune on the charger" switched off,
    then unplugged: the game stays ungoverned on battery, as before the charger switch."""
    p = plugin
    p.ac["plugged"] = True
    p.settings["autotune"] = False
    p.autotune.enabled = False
    p.settings["autotune_plugged"] = True
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.game_started("7"))
    p.run(p.set_setting("autotune", True))
    p.run(p.set_setting("autotune_plugged", False))
    p.ac["plugged"] = False
    p.run(p.power_source_changed(False))
    assert p.autotune.session is None and not p.run(p.get_power())["autotune_governs"], p.chip()


def test_a_new_target_after_the_charger_switch_off_does_not_govern_on_the_charger(plugin):
    """On the charger, a governed game; "Autotune on the charger" switched off (the TDP setting at once), then a new
    target: the TDP setting stays on the chip."""
    p = plugin
    p.ac["plugged"] = True
    p.run(p.set_setting("autotune", True))
    p.run(p.set_setting("autotune_plugged", True))
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.game_started("7"))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(30)
    p.run(p.set_setting("autotune_plugged", False))
    p.run(p.set_autotune_target(45, True))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(60)
    assert p.chip() == 26 and not p.run(p.get_power())["autotune_governs"], p.chip()


def test_a_game_autotune_was_switched_off_and_on_in_says_why_it_is_not_governed(plugin):
    """Autotune switched off and on in a game: the session stays paused and the game is not governed until its next
    start. The panel gets the reason; it showed "paused: pick a profile or a target", and neither hands it back."""
    p = plugin
    p.run(p.set_setting("autotune", True))
    p.run(p.game_started("7"))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(30)
    p.run(p.set_setting("autotune", False))
    p.run(p.set_setting("autotune", True))
    at = p.run(p.get_autotune())
    assert at.get("paused") and at.get("why") == "turned on during this game: restart the game and it starts", at.get("why")


def test_the_charger_switch_on_keeps_a_game_autotune_was_switched_on_in_ungoverned(plugin):
    """On the charger, "Autotune on the charger" off; Autotune switched on during the game (no frame log), then the
    charger switch on: the game stays at the TDP setting."""
    p = plugin
    p.ac["plugged"] = True
    p.settings["autotune"] = False
    p.autotune.enabled = False
    p.settings["autotune_plugged"] = False
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.game_started("7"))
    p.run(p.set_setting("autotune", True))
    p.run(p.set_setting("autotune_plugged", True))
    assert p.autotune.session is None and p.chip() == 26 and not p.run(p.get_power())["autotune_governs"]


def test_a_manual_lever_in_a_governed_game_keeps_the_paused_line(plugin):
    """A manual lever pauses Autotune in a game it governs: the panel shows the paused line (no reason given)."""
    p = plugin
    p.run(p.set_setting("autotune", True))
    p.run(p.game_started("7"))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(30)
    p.run(p.set_cpu_turbo(False))
    at = p.run(p.get_autotune())
    assert at.get("paused") and "why" not in at, at.get("why")


@pytest.mark.parametrize("kept", ["the CPU boost", "the guard's step"])
def test_the_charger_switch_off_in_a_governed_game_keeps_the_users_levers_and_the_guards_step(plugin, tmp_path, kept):
    """On the charger, a governed game; "Autotune on the charger" switched off: the user's CPU boost (off) stays off,
    and on a package at 97 °C the guard's step (13 W) stays, as for the Autotune switch. The switch restarted the
    game's power: the boost came back on, and the TDP setting (26 W) went on the hot chip."""
    p = plugin
    t = tmp_path / "pkg_temp"
    p.guard = thermal.Guard(str(t), "x86_pkg_temp")
    p.ac["plugged"] = True
    p.run(p.set_setting("autotune", True))
    p.run(p.set_setting("autotune_plugged", True))
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.game_started("7"))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(30)
    if kept == "the CPU boost":
        p.run(p.set_cpu_turbo(False))
    else:
        _w(t, 97 * 1000)
        p.run(p._guard_check())
    step = p.chip()
    p.run(p.set_setting("autotune_plugged", False))
    assert (p.power.turbo() is False) if kept == "the CPU boost" else p.chip() <= step, (p.power.turbo(), step, p.chip())


@pytest.mark.parametrize("paused", [False, True])
def test_the_charger_switch_set_to_the_value_it_has_changes_nothing(plugin, paused):
    """On the charger, a governed game (paused by the user's CPU boost, or not): the panel sends "Autotune on the
    charger" on again (a second copy of the panel). The session goes on as it was; it started over, with the boost
    reverted."""
    p = plugin
    p.ac["plugged"] = True
    p.run(p.set_setting("autotune", True))
    p.run(p.set_setting("autotune_plugged", True))
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.game_started("7"))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(60)
    if paused:
        p.run(p.set_cpu_turbo(False))
    s = p.autotune.session
    p.run(p.set_setting("autotune_plugged", True))
    assert p.autotune.session is s and s.paused == paused and (p.power.turbo() is False or not paused)


@pytest.mark.parametrize("state", ["governed", "switched on in the game", "switched off in the game"])
def test_the_autotune_switch_set_to_the_value_it_has_changes_nothing(plugin, state):
    """The panel sends the Autotune switch again with the value it has, then a new target and a resume from sleep: a
    governed game takes the target and is governed again, a game Autotune was switched on in stays at the TDP setting,
    one it was switched off in keeps the limit set since."""
    p = plugin
    p.ac["plugged"] = False
    p.settings["autotune"] = p.autotune.enabled = state != "switched on in the game"
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.game_started("7"))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(60)
    if state != "governed":
        p.run(p.set_setting("autotune", state == "switched on in the game"))
    if state == "switched off in the game":
        assert p.run(p.set_tdp(20))["ok"]
    p.run(p.set_setting("autotune", state != "switched off in the game"))
    if state == "switched off in the game":
        assert p.chip() == 20, p.chip()
        return
    p.run(p.set_autotune_target(45, True))
    p.run(p.resumed())
    s = p.autotune.session
    if state == "governed":
        _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(60)
        assert s is not None and not s.paused and s.user_target == 45 and p.chip() < 26, p.chip()
    else:
        assert s is None and p.chip() == 26 and not p.run(p.get_power())["autotune_governs"], p.chip()


@pytest.mark.parametrize("plugged", [False, True])
def test_the_charger_switch_a_new_target_and_the_slider_follow_the_games_power_source_until_steam_reports_a_change(
        plugin, plugged):
    """A governed game; the charger goes in or comes out, and before Steam reports it "Autotune on the charger" is
    switched off and a new target set: the game on the charger is paused at the TDP setting, the game on battery stays
    governed, takes the target and has the TDP slider greyed out."""
    p = plugin
    p.ac["plugged"] = plugged
    p.run(p.set_setting("autotune", True))
    p.run(p.set_setting("autotune_plugged", True))
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.game_started("7"))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(30)
    p.ac["plugged"] = not plugged
    p.run(p.set_setting("autotune_plugged", False))
    s = p.autotune.session
    paused = s.paused
    p.run(p.set_autotune_target(45, True))
    governs = p.run(p.get_power())["autotune_governs"]
    assert paused == s.paused == plugged != governs and (s.user_target == 45) != plugged, (paused, s.paused, governs)


def test_a_paused_game_plugged_in_before_steam_reports_it_keeps_the_paused_line(plugin):
    """Governed on battery and paused by the user's CPU boost; the charger goes in ("Autotune on the charger" off) and
    Steam has not reported it yet: the panel keeps the paused line. It got a reason for a game with no session."""
    p = plugin
    p.ac["plugged"] = False
    p.settings["autotune_plugged"] = False
    p.run(p.set_setting("autotune", True))
    p.run(p.game_started("7"))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(30)
    p.run(p.set_cpu_turbo(False))
    p.ac["plugged"] = True
    at = p.run(p.get_autotune())
    assert at.get("paused") and "why" not in at, at.get("why")


@pytest.mark.parametrize("own", [None, 15])
def test_autotune_on_the_charger_switched_off_in_a_governed_game_gives_the_tdp_setting_back(plugin, own):
    """On the charger, the game governed (the limit lowered). "Autotune on the charger" switched off: the TDP setting
    is the limit at once, as for Autotune switched off; Autotune went on lowering it with the slider not greyed."""
    p = plugin
    p.ac["plugged"] = True
    p.run(p.set_setting("autotune", True))
    p.run(p.set_setting("autotune_plugged", True))
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.set_autotune_max(own))
    p.run(p.game_started("7"))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(30)
    p.run(p.set_setting("autotune_plugged", False))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(60)
    assert p.chip() == 26 and not p.run(p.get_power())["autotune_governs"], p.chip()


@pytest.mark.parametrize("value", [False, True])
def test_autotune_on_the_charger_switched_in_a_game_on_battery_leaves_the_game_governed(plugin, value):
    """On battery the charger's switch is not Autotune's: switched either way in a governed game, Autotune goes on
    governing it (not paused, the limit under the TDP setting) and the TDP slider stays greyed."""
    p = plugin
    p.ac["plugged"] = False
    p.run(p.set_setting("autotune", True))
    p.run(p.set_setting("autotune_plugged", not value))
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.game_started("7"))
    s = p.autotune.session
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(30)
    p.run(p.set_setting("autotune_plugged", value))
    _Game(p.autotune, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(60)
    assert not s.paused and p.chip() < 26 and p.run(p.get_power())["autotune_governs"], (s.paused, p.chip())


def test_the_panel_gets_the_screens_highest_refresh_rate_for_the_target_list(plugin):
    """Also after another target is picked: the target when none is set stays in the panel's list."""
    p = plugin
    p.run(p.set_autotune_target(40, True))
    assert p.run(p.get_power())["autotune_screen_target"] == max(p.hw.model.get("panel_hz") or [60]) != 40


def test_an_esde_game_started_again_after_the_switch_is_governed(plugin):
    """Autotune switched on while a game from ES-DE ran; the game ends and ES-DE starts it again: the new run is
    governed, as any game ES-DE starts after the switch. It stayed at the TDP setting for good."""
    p = plugin
    p.settings["autotune"] = False
    p.autotune.enabled = False
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.game_started("100"))
    _frontend_event(p, "start", "ps2", "/roms/ps2/a.iso", "Game A")
    p.run(p.set_setting("autotune", True))
    assert p.autotune.session is None
    _frontend_event(p, "end", "ps2", "/roms/ps2/a.iso", "Game A")
    _frontend_event(p, "start", "ps2", "/roms/ps2/a.iso", "Game A")
    assert p.autotune.session is not None and p.run(p.get_power())["autotune_governs"]


@pytest.mark.parametrize("during", ["menus", "game"])
def test_esde_menus_after_its_game_are_not_governed_when_autotune_came_on_in_esde(plugin, during):
    """ES-DE launched with Autotune off (its menus keep the TDP setting, 26 W); Autotune switched on in ES-DE's menus
    or in one of its games, Autotune's maximum 15 W. After that game ends ES-DE's menus are as before: not governed,
    the TDP setting on the chip. They were governed under 15 W."""
    p = plugin
    p.settings["autotune"] = False
    p.autotune.enabled = False
    assert p.run(p.set_tdp(26))["ok"]
    p.run(p.game_started("100"))
    if during == "menus":
        p.run(p.set_setting("autotune", True))
    _frontend_event(p, "start", "ps2", "/roms/ps2/a.iso", "Game A")
    if during == "game":
        p.run(p.set_setting("autotune", True))
    p.run(p.set_autotune_max(15))
    _frontend_event(p, "end", "ps2", "/roms/ps2/a.iso", "Game A")
    assert p.running_key == "100"
    assert p.autotune.session is None and p.chip() == 26, (p.autotune.session is not None, p.chip())


def test_xemu_from_an_appimage_is_found_though_its_process_is_called_apprun(tmp_path):
    def running(comm, exe=None):
        root = tmp_path / f"proc{len(os.listdir(tmp_path))}"
        for pid, c, e in (("1", "systemd", "/usr/lib/systemd/systemd"), ("4000", comm, exe)):
            _w(str(root / pid / "comm"), c)
            if e:
                os.symlink(e, str(root / pid / "exe"))
        return autotune.own_pace_emulator(str(root))
    assert running("AppRun", "/tmp/.mount_xemu-pekbPgK/usr/bin/xemu") == "xemu"      # as measured on the EX
    assert running("AppRun", "/tmp/.mount_Cemu-2xYz12/usr/bin/Cemu") is None         # another program's AppImage
    assert running("bash", "/usr/bin/bash") is None
    assert running("xemu") == "xemu"                                                # as before: the name (no exe to read)


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
    assert chip["tdp"] == 16                                          # where it ended last time
    g = _Game(at, lambda *_: 60.0, gpu_mhz=1000, cpu_pct=30, pkg_w=19.0, gpu_busy=40).run(3.2)
    assert at.status()["own_pace"] is None                            # not started yet at 3 s
    _w(str(proc / "4300" / "comm"), "xemu")                           # ES-DE starts it
    g.run(7)
    st = at.status()
    assert st["own_pace"] == "xemu" and st["paused"] and not st["no_frames"] and chip["tdp"] == 30
    g.run(60)                                                         # 60 fps keep coming: nothing changes
    assert chip["tdp"] == 30 and at.session.paused
    assert autotune.own_pace_emulator(str(tmp_path / "missing")) is None
    at.session = None


@pytest.mark.parametrize("after_s", [35, 100, 400])
@pytest.mark.parametrize("frames_before", [60.0, None])
def test_xemu_started_at_any_time_in_a_session_gets_the_limit_you_picked(fake_hw, monkeypatch, tmp_path, after_s,
                                                                         frames_before):
    """xemu was looked for 3, 10 and 30 s into a session only. Started later (by a launcher that shows no frame for
    40 s, or from a frontend whose games share its session), it was governed on its 60 shown frames, down to 8 W."""
    pw = power.Power(fake_hw)
    chip, notes = {}, []
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: chip.update(tdp=w) or True)
    at = autotune.Autotune(fake_hw, pw, "/nonexistent")
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    monkeypatch.setattr(at, "_note", lambda s, text: notes.append(text))
    proc = tmp_path / "proc"
    for pid, name in (("1", "systemd"), ("4242", "es-de")):
        _w(str(proc / pid / "comm"), name)
    monkeypatch.setattr(autotune, "PROC", str(proc))
    pw.apply_profile("", {"tdp": 30}, False)                          # the pick: Custom 30 W
    at.start("3119184808#xbox/Project Gotham Racing 2", None, 8, 30, False)
    frames = {"fps": frames_before}
    g = _Game(at, lambda *_: frames["fps"], gpu_mhz=1000, cpu_pct=30, pkg_w=19.0, gpu_busy=40).run(after_s)
    assert at.status()["own_pace"] is None and (chip["tdp"] < 30 if frames_before else at.session.no_frames)
    _w(str(proc / "4300" / "comm"), "xemu")                           # now the emulator starts and shows its 60 fps
    frames["fps"], n = 60.0, len(notes)
    g.run(autotune.OWN_PACE_CHECK_S + 1)
    st = at.status()
    assert st["own_pace"] == "xemu" and st["paused"] and chip["tdp"] == 30 and not pw.governed
    assert not [x for x in notes[n:] if "takes over" in x]            # its frames are never taken for a game's
    g.run(120)
    assert chip["tdp"] == 30 and not pw.governed
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


def test_autotune_csv_says_why_the_limit_moved(fake_hw, tmp_path, monkeypatch):
    """A row of the log has the goal and, in "note", what was decided since the row before: each change of the limit
    with its watts and reason, and a pause. Without it a run on a device shows that the limit moved and not why."""
    at, s = _tuner(fake_hw, monkeypatch, ceiling=30, target=60)
    at.csv_log = str(tmp_path / "autotune.csv")
    g = _Game(at, lambda *_: 60.0)                               # 60 fps at any limit: down to the floor
    g.run(120)
    at.pause("manual CPU boost")
    g.run(3)
    at.session = None
    rows = [dict(zip(autotune.CSV_FIELDS, r)) for r in csv.reader(open(at.csv_log))][1:]
    notes = [r["note"] for r in rows if r["note"]]
    moved = [n for n in notes if " W: " in n]
    assert moved and all(n.split(" W: ")[0].lstrip("+-").isdigit() for n in moved)       # "-2 W: <why>"
    assert any(r["goal"] == "57.0" for r in rows)                                       # 60 fps less 5 %
    assert notes[-1].startswith("paused: manual CPU boost") and rows[-1]["goal"] == ""
    assert any(n.startswith("holding at") for n in notes)                               # a decision without a write
    assert len(notes) < len(rows) / 2                                                    # a note once, not every row
    assert s.notes == []


def test_a_row_of_autotune_csv_has_every_frame_since_the_row_before(fake_hw, tmp_path, monkeypatch):
    """Autotune looks twice a second, ten times while it watches a write, and logs once a second: a row had the frames
    of its one look (58 of about 118 while holding, 10 while watching), so half the frames or more were in no row."""
    at, s = _tuner(fake_hw, monkeypatch, ceiling=30, target=60)
    at.csv_log = str(tmp_path / "autotune.csv")
    g = _Game(at, lambda tdp, *_: min(60.0, 4.0 * tdp))                            # 60 fps from 15 W: steps down from 19
    whole, given = g.sample, []

    def with_frames():
        smp = whole()
        at.sampler.frames_ms = [1000.0 / smp.fps] * round(smp.fps * s.look_s)      # this look's, as mangoapp's log has them
        given.append(len(at.sampler.frames_ms))
        return smp
    at.sampler.sample = with_frames
    g.run(40)
    at.session = None
    rows = [dict(zip(autotune.CSV_FIELDS, r)) for r in csv.reader(open(at.csv_log))][1:]
    frames = [int(r["frames"]) for r in rows]
    assert {r["phase"] for r in rows} >= {"hold", "probe"}                         # both look periods were in it
    assert sum(frames) >= sum(given) - 60 and min(frames[1:]) >= 40               # all of them, a second's worth a row
    assert all(abs(float(r["fps"]) - 60.0) < 8 for r in rows[1:] if r["phase"] == "hold")


def test_a_rows_slow_frames_are_slow_against_the_pace_before_the_row(fake_hw, tmp_path, monkeypatch):
    """A second in which every frame takes 25 ms after seconds of 16.7 ms: all of its frames are slow (against the
    row's own median none would be, and a frame rate that fell would show no slow frames at all)."""
    at, s = _tuner(fake_hw, monkeypatch, ceiling=30, target=60)
    at.csv_log = str(tmp_path / "autotune.csv")
    g = _Game(at, lambda *_: 60.0)
    whole, ms = g.sample, [1000.0 / 60]

    def with_frames():
        smp = whole()
        at.sampler.frames_ms = [ms[0]] * round(s.look_s * 1000.0 / ms[0])
        return smp
    at.sampler.sample = with_frames
    g.run(3)
    ms[0] = 25.0
    g.run(1.5)
    at.session = None
    rows = [dict(zip(autotune.CSV_FIELDS, r)) for r in csv.reader(open(at.csv_log))][1:]
    assert {r["slow_pct"] for r in rows if r["ft_p95"] == "16.67"} == {"0.0"}
    assert [r["slow_pct"] for r in rows if r["ft_median"] == "25.0"][0] == "100.0"


def test_autotune_csv_gets_the_frame_time_columns_and_an_older_log_is_set_aside(fake_hw, tmp_path, monkeypatch):
    log = tmp_path / "autotune.csv"
    old = "time,appid,phase,fps,shown_fps,source,gpu_mhz,gpu_max_mhz,cpu_pct,pkg_w,tdp,cpu_max_pct,gpu_min_mhz,boost,bound,max_fps\n1.0,7,hold,60.0,60.0,presented,,,,,20,100,,1,balanced,60.0\n"
    log.write_text(old)
    at, _s = _tuner(fake_hw, monkeypatch)
    pw, at.csv_log = at.power, str(log)
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
    assert rows[0] == ",".join(autotune.CSV_FIELDS)
    assert rows[0].endswith(",max_fps,frames,ft_median,ft_p95,slow_pct,goal,mult,look_s,note")
    cols = [dict(zip(autotune.CSV_FIELDS, r)) for r in csv.reader(rows[1:])]
    assert [cols[0][k] for k in ("frames", "ft_median", "ft_p95", "slow_pct")] == ["30", "33.3", "38.04", "6.7"]
    assert [cols[1][k] for k in ("frames", "ft_median", "ft_p95", "slow_pct")] == ["", "", "", ""] and len(rows) == 3
    # the same columns the next time: the log goes on
    at2, _s = _tuner(fake_hw, monkeypatch)
    at2.csv_log = str(log)
    at2.sampler.sample = lambda: autotune.Sample(t=7.5, frametime_ms=16.7, fps=60.0, gpu_mhz=None, gpu_max_mhz=None,
                                                 cpu_pct=None, pkg_w=15.0, shown_fps=60.0)
    at2._step()
    at2.session = None
    assert len(log.read_text().splitlines()) == 4 and (tmp_path / "autotune.csv.1").read_text() == old


def test_a_look_without_a_new_mangoapp_row_has_no_frames_not_the_other_logs_average(fake_hw, tmp_path, monkeypatch):
    """A game with the launch wrapper has both logs. One look without a new mangoapp row (a 0.6 s stall) fell through
    to the MangoHud log and got the average of every second it had written so far as "this look's frames": 42.8 fps
    in a 60 fps game, and the full limit for it (review, 2026-11-15)."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(autotune.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(autotune, "mangoapp_command", lambda c: True)
    logs = tmp_path / "mangohud"
    logs.mkdir()
    own = logs / "game_2026-11-15_14-00-00.csv"
    own.write_text("os\nLinux\nfps,frametime\n" + "30.0,33.3\n" * 40)          # 40 s of a slow start, never read
    live = tmp_path / "mangoapp_2026-11-15_14-00-01.csv"
    _mango_rows(live, [16.7] * 3, header=True)
    smp = autotune.Sampler(fake_hw, str(logs), home=str(tmp_path))
    smp.set_prefix(None)
    smp.frametime()                                                       # finds both logs
    _mango_rows(live, [16.7] * 30)
    clock["t"] += 0.5
    assert smp.frametime() == (pytest.approx(16.7), pytest.approx(59.9, abs=0.1))
    clock["t"] += 0.5
    assert smp.frametime() == (None, None) and smp.frames_ms == []       # a stall: no frames, not 30 fps
    with open(own, "a") as f:                                             # the own log goes on meanwhile (a slow second)
        f.write("30.0,33.3\n")
    _mango_rows(live, [16.7] * 30)
    clock["t"] += 0.5
    assert smp.frametime()[1] == pytest.approx(59.9, abs=0.1)
    with open(own, "a") as f:
        f.write("30.0,33.3\n")
    clock["t"] += 0.5
    smp._log_checked = -1e9
    assert smp.frametime() == (None, None)                                # the next stall: still not the other log's rows
    clock["t"] += autotune.MANGOAPP_RELOG_S + 1                           # mangoapp gone for good: the other log,
    smp._log_checked = -1e9                                               # from here on only (not the two slow
    assert smp.frametime() == (None, None)                                # seconds it wrote meanwhile)
    with open(own, "a") as f:
        f.write("60.0,16.7\n")
    clock["t"] += 1.0
    assert smp.frametime() == (16.7, 60.0)


def test_the_frame_generation_multiplier_is_a_whole_number_that_changes_rarely(fake_hw, monkeypatch):
    """XeLL writes its figure once per 100 rendered frames. Shown fps of this look over that line stood still between
    two lines, times every frame: the governor saw a constant rate and no loss at all (review, 2026-11-15)."""
    at, s = _tuner(fake_hw, monkeypatch, tdp=20)
    seen = []

    def look(t, shown_fps, xell):
        at.sampler._xell = xell
        at.sampler.frames_ms = [1000.0 / shown_fps] * int(shown_fps / 2)
        smp = autotune.Sample(t=t, frametime_ms=1000.0 / xell[1], fps=xell[1], gpu_mhz=None, gpu_max_mhz=None, cpu_pct=None,
                              pkg_w=20.0, shown_fps=shown_fps, source="rendered")
        seen.append(at._multiplier(s, smp))
    for i in range(8):                                   # 2x frame generation: 120 shown, 60 rendered, a line each 1.5 s
        look(1.0 + i * 0.5, 120.0, (1.0 + (i // 3) * 1.5, 60.0))
    assert seen[-1] == 2.0
    for i in range(3):                                   # the game slows down between two lines: 90 shown, the line says 60
        look(5.0 + i * 0.5, 90.0, (4.0, 60.0))
    assert seen[-3:] == [2.0, 2.0, 2.0]                  # 90 / 2 = 45 rendered: the governor sees the loss
    for i in range(4):                                   # one line that fits its shown frames badly (170 over 60 is 3x):
        look(6.5 + i * 0.5, 170.0, (5.5 + (i // 3) * 1.5, 60.0))
    assert seen[-1] == 2.0 and round(s.mult_hist[-1]) == 3   # the median of the last five, not the last
    before = list(s.mult_hist)
    at.sampler._xell, at.sampler.frames_ms = (7.2, 60.0), [1000.0 / 300.0] * 4   # a line 4 shown frames after the last:
    assert at._multiplier(s, autotune.Sample(t=8.5, frametime_ms=16.7, fps=60.0, gpu_mhz=None, gpu_max_mhz=None, cpu_pct=None,
                                             pkg_w=20.0, shown_fps=300.0, source="rendered")) == 2.0
    assert list(s.mult_hist) == before                   # too few frames for a quotient (300 over 60 would be 5x)
    at.sampler._xell = None
    s.fg = 3                                             # no XeLL: ClawDeck's own multiplier for the game
    assert at._multiplier(s, autotune.Sample(t=9.0, frametime_ms=16.7, fps=60.0, gpu_mhz=None, gpu_max_mhz=None, cpu_pct=None,
                                             pkg_w=20.0, shown_fps=60.0)) == 3.0
    at.session = None


def test_a_new_target_leaves_xemu_alone_and_hands_control_back_after_a_change_by_hand(fake_hw, monkeypatch, tmp_path):
    t = _with_tau(fake_hw, tmp_path)
    at, s = _tuner(fake_hw, monkeypatch, tdp=14)
    s.own_pace, s.paused = "xemu", True
    at._give_back(s)
    at._tau(s, False)
    at.enabled = True
    at.set_target(60)
    assert s.paused and s.learned.tdp == 20 and s.user_target == 60       # the target is kept for when it governs
    assert sysfs.read_int(t) == TAU_FW and not s.short_tau                # ... and the chip's window stays the firmware's
    _Game(at, lambda *_: 60.0).run(30)
    assert s.learned.tdp == 20 and not at.power.governed                  # your limit, for as long as xemu runs
    at.session = None
    for why, on, back in (("manual CPU ceiling", True, True), ("manual GPU clocks", True, True),
                          ("autotune switched off", False, False), ("autotune switched off", True, True)):
        at, s = _tuner(fake_hw, monkeypatch, tdp=14)
        at.pause(why)
        assert s.paused and s.learned.tdp == 20 and not at.power.governed
        at.enabled = on                                                    # the Autotune switch (off, or on again)
        at.set_target(30)
        assert s.paused is not back and at.power.governed is back and s.user_target == 30, (why, on)
        assert s.learned.tdp == 20                                         # from the limit in use
        at.session = None


@pytest.mark.parametrize("why, off_and_on", [("manual CPU ceiling", False), ("autotune switched off", True)])
@pytest.mark.parametrize("frames_again", [False, True])
def test_a_new_target_after_a_give_up_for_no_frames_governs_only_when_frames_came(fake_hw, monkeypatch, why, off_and_on,
                                                                                  frames_again):
    """The change by hand comes after the give-up and takes "no frames" off the session (frames later must not undo
    it): a new target then handed control back to a game without a frame rate."""
    at, s = _tuner(fake_hw, monkeypatch, ceiling=20)
    at.enabled = True
    g = _Game(at, lambda *_: None).run(autotune.NO_FPS_S + 5)
    assert s.paused and s.no_frames and not at.power.governed
    at.enabled = not off_and_on
    at.pause(why)
    at.enabled = True
    if frames_again:
        g.fps = lambda *_: 60.0
        g.run(2)
        assert s.paused and not at.power.governed                          # yours stays yours
    at.set_target(30)
    assert s.paused is not frames_again and at.power.governed is frames_again
    at.session = None


def test_the_guard_is_told_the_limit_autotune_holds_and_its_release_reaches_a_paused_game(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, ceiling=30, tdp=17)
    g = _Game(at, lambda tdp, *_: 2.0 * tdp).run(4)
    s.gov.mode, s.gov.prev, s.learned.tdp = "check", 17, 30               # its second at the full limit
    assert at.held_w() == 17                                             # ... is not what it holds (a guard check then
    s.gov.mode = "hold"                                                  # took a real overload for a hot spot)
    s.learned.tdp = 17
    assert at.held_w() == 17
    at.pause("manual change")
    assert s.learned.tdp == 30
    assert at.thermal_step(24) and s.learned.tdp == 24
    assert at.thermal_release(26) and s.learned.tdp == 26                # nobody governs: the watts go on the chip
    assert at.thermal_release(30) and s.learned.tdp == 30 and s.thermal_cap is None
    at.session = None


def test_a_refused_write_is_tried_again_later_not_ten_times_a_second(fake_hw, monkeypatch, caplog):
    at, s = _tuner(fake_hw, monkeypatch, tdp=16)
    tries = []
    monkeypatch.setattr(at.power, "set_tdp", lambda w, plugged=None: tries.append(w) and False)   # e.g. HHD's TDP controls off
    g = _Game(at, lambda *_: 40.0)                                           # under the target: it wants the full limit
    s.user_target, s.cfg.target = 60, 60.0
    with caplog.at_level("WARNING", logger="clawdeck.autotune"):
        g.run(20)
    assert s.learned.tdp == 16 and s.gov.limit == 16 and s.look_s == 1.0     # resting, a look a second
    assert s.gov.mode == "hold" and not s.gov.retest                         # ... with no write of the governor's watched
    assert len([r for r in caplog.records if "not applied" in r.getMessage()]) == 1
    assert len(tries) <= 2                                                   # once, and not again within REFUSED_S
    g.run(autotune.REFUSED_S + 5)
    assert 2 <= len(tries) <= 4                                              # ... and again after it
    at.session = None


def test_a_refused_write_ends_the_watch_it_came_in(fake_hw, monkeypatch):
    at, s = _tuner(fake_hw, monkeypatch, ceiling=30, target=60)
    g = _Game(at, lambda tdp, *_: min(60.0, 4.0 * tdp)).run(6)                # 60 fps from 15 W
    assert s.gov.mode == "probe" and s.gov.before                             # a step down is being watched
    monkeypatch.setattr(at.power, "set_tdp", lambda w, plugged=None: False)
    g.run(8)                                                                  # it holds; the next step is refused
    assert s.refused and s.gov.mode == "hold" and s.gov.before is None
    at.session = None


def test_a_log_with_a_row_a_second_gives_the_governor_whole_seconds_of_frames(fake_hw, monkeypatch):
    """Only the game's own MangoHud log (mangoapp silent): a row a second, two looks a second. Each row was handed on
    as half a second of frames and the look in between as none: a frame rate with holes, never "steady", so Autotune
    never stepped down on such a log (19 W for a game that needs 8; review, 2026-11-15)."""
    at, s = _tuner(fake_hw, monkeypatch, ceiling=30, target=60)
    g = _Game(at, lambda tdp, *_: min(60.0, 7.5 * tdp))                       # 60 fps from 8 W
    whole, look = g.sample, {"n": 0}

    def every_other():
        smp = whole()
        look["n"] += 1
        if look["n"] % 2:                                                     # no new row in this look
            smp.fps = smp.shown_fps = smp.frametime_ms = None
        return smp
    at.sampler.sample = every_other
    start = s.learned.tdp
    g.run(4)
    assert len(s.gov.recent) >= 0.9 * 60 * 3 and not s.no_frames              # about 60 frames a second arrived
    g.run(60)
    assert s.learned.tdp <= 10 < start and not s.paused
    at.session = None



def test_a_pl2_change_while_autotune_governs_keeps_the_session_and_what_it_learned(plugin):
    p = plugin
    p.run(p.set_tdp(26))
    p.run(p.game_started("7"))
    s = _lower(p)
    was = (s.learned.tdp, p.chip(), dict(s.gov.held_s))
    assert s.learned.tdp < 26
    for args in ((True, 45), (True, 30), (False, None), (True, 45)):   # a restart at the full limit dropped it all
        r = p.run(p.set_pl2(*args))
        assert r["ok"] and p.autotune.session is s and not s.paused and p.power.governed, args
        assert (s.learned.tdp, p.chip(), dict(s.gov.held_s)) == was, args


@pytest.mark.parametrize("pct, turbo_off, gpu_min", [(100, 0, 300), (70, 1, 800), (55, 0, 1300)])
def test_the_levers_in_the_log_are_what_the_chip_has_not_what_an_old_record_said(fake_hw, monkeypatch, pct, turbo_off, gpu_min):
    sysfs.write(fake_hw.paths.cpu_max_perf_pct, pct, persist=True)
    sysfs.write(fake_hw.paths.cpu_no_turbo, turbo_off, persist=True)
    sysfs.write(fake_hw.paths.gpu_min_freq, gpu_min, persist=True)
    for old in ({"cpu_max_pct": 85, "gpu_min_mhz": 1000, "boost": False}, {"cpu_max_pct": 100, "gpu_min_mhz": None, "boost": True}):
        at, s = _tuner(fake_hw, monkeypatch, tdp=14, **old)
        l = s.learned
        assert (l.cpu_max_pct, l.gpu_min_mhz, l.boost) == (pct, gpu_min, not turbo_off), old
        assert sysfs.read_int(fake_hw.paths.cpu_max_perf_pct) == pct and sysfs.read_int(fake_hw.paths.gpu_min_freq) == gpu_min
        at.session = None


def test_set_pl2_says_what_the_chip_has_while_autotune_governs(plugin):
    p = plugin
    p.run(p.set_tdp(26))
    p.run(p.game_started("7"))
    _lower(p)
    r = p.run(p.set_pl2(True, 45))
    pl1 = sysfs.read_int(p.power.p.rapl_pl1) // 1_000_000
    assert r["ok"] and p.power.governed and r["read_w"] == pl1 + 2
    assert r["summary"] == f"PL2 {pl1 + 2} W (PL1 + 2 W while Autotune governs this game; your 45 W applies when it doesn't)"


def test_what_the_guard_and_the_panel_set_reaches_the_governor(fake_hw, monkeypatch):
    """The wiring between the plugin and governor.step: the guard's cap and its clock cap are in every observation, a
    target set in the panel is in the configuration, the guard's step ends a watch, no write goes over the cap, a
    change of the frame-generation multiplier starts the governor's memory over, and stop() ends "governed"."""
    at, s = _tuner(fake_hw, monkeypatch, ceiling=30, target=60)
    chip = {}
    monkeypatch.setattr(at.power, "set_tdp", lambda w, plugged=None: chip.update(tdp=w) or True)
    seen, real = [], autotune.governor.step
    monkeypatch.setattr(autotune.governor, "step", lambda st, obs, cfg: seen.append((obs, cfg)) or real(st, obs, cfg))
    g = _Game(at, lambda tdp, *_: min(60.0, 4.0 * tdp))                       # 60 fps from 15 W
    g.run(6)
    assert s.gov.mode == "probe"                                              # a step down is being watched
    assert at.thermal_step(17) and s.gov.mode == "hold" and chip["tdp"] <= 17  # the guard's step ends the watch
    assert s.gov.before is None                                               # ... with nothing of it left behind
    g.run(1)
    assert seen[-1][0].cap_w == 17 and seen[-1][0].thermal is False
    assert not at._tdp(s.learned, 25) or chip["tdp"] <= 17                    # nothing over the cap
    assert chip["tdp"] <= 17
    at.thermal_clock(80)
    g.run(1)
    assert seen[-1][0].thermal is True
    at.set_target(45, False)
    g.run(1)
    assert (seen[-1][1].target, seen[-1][1].auto_fps) == (45.0, False)
    s.gov.fail[11], s.gov.lo, s.gov.hi, s.gov.ratio = [1e12, 1, None, False, 0.0], 55.0, 56.0, 0.97
    s.fg = 2                                                                  # frame generation switched on in the game
    g.run(1)
    assert seen[-1][0].multiplier == 2.0 and s.gov.fail == {} and s.gov.lo is None and s.gov.ratio is None
    assert at.power.governed
    at.stop(revert=False)
    assert not at.power.governed


def test_a_manual_cpu_ceiling_pauses_autotune_and_the_guard_gets_the_limit_it_holds(plugin, tmp_path):
    p = plugin
    p.run(p.set_tdp(30))
    p.run(p.game_started("7"))
    s = _lower(p)
    held = p.chip()
    assert held < 30
    # Autotune looks at the full limit for a second: the guard must judge the limit it holds, not that second's 30 W
    s.gov.mode, s.gov.prev = "check", held
    p.autotune._tdp(s.learned, 30)
    t = tmp_path / "pkg_temp"
    p.guard = thermal.Guard(str(t), "x86_pkg_temp")
    _w(t, 97000)
    assert p.run(p._guard_check()) == held - 2                                # 2 W under what it holds (not 28 W)
    r = p.run(p.set_cpu_max_pct(80))
    assert r["ok"] and s.paused


def test_frames_that_stop_for_good_give_the_limit_you_picked_back(fake_hw, monkeypatch):
    """The frame log stops in the middle of a game (mangoapp's logging switched off and not coming back): the governor
    is blind. After NO_FPS_S without a frame the pick goes back, as for a game that never showed a frame; frames
    coming back hand the game to Autotune again."""
    pw = power.Power(fake_hw)
    chip = {}
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: chip.update(tdp=w) or True)
    monkeypatch.setattr(autotune.asyncio, "get_event_loop", lambda: type("L", (), {"create_task": lambda self, c: c.close()})())
    monkeypatch.setattr(autotune, "own_pace_emulator", lambda proc=None: None)
    at = autotune.Autotune(fake_hw, pw, log_dir="/nonexistent", csv_log=None)
    at.start("1", None, 8, 30, False, target=60)
    s = at.session
    g = _Game(at, lambda tdp, *_: min(60.0, 4.0 * tdp))                       # 60 fps from 15 W
    g.run(90)
    low = chip["tdp"]
    assert low <= 17 and not s.paused
    frames, g.fps = g.fps, lambda *_: None                                    # the log stops
    g.run(autotune.NO_FPS_S - 3)
    assert chip["tdp"] == low and not s.paused                                # a long loading screen is not it yet
    g.run(6)
    st = at.status()
    assert chip["tdp"] == 30 and st["paused"] and st["no_frames"] and not pw.governed
    g.fps = frames                                                            # frames again
    g.run(60)
    assert not s.paused and not s.no_frames and chip["tdp"] < 30
    at.session = None


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


def test_sampler_reads_the_games_own_mangohud_log_only_while_mangoapp_gives_nothing(fake_hw, tmp_path, monkeypatch):
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
    assert smp.frametime() == (16.6, 60.0) and smp.frames_ms == [] and sent == ["logging=1"]   # mangoapp's stays on
    _mango_rows(tmp_path / "mangoapp_2026-11-15_00-11-02.csv", [20.0] * 5, header=True)
    smp._mango_checked = -1e9
    ft, fps = smp.frametime()
    assert (ft, fps) == (20.0, 50.0) and smp.frames_ms == [20.0] * 5                           # every frame, from now on


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


def test_autotune_target_setting_is_saved_clamped_and_reaches_the_running_session(plugin, monkeypatch):
    p = plugin
    got = []
    monkeypatch.setattr(p.autotune, "set_target", lambda t, a=True: got.append((t, a)))
    r = p.run(p.set_autotune_target(60, True))
    assert r["ok"] and r["target"] == 60 and p.settings["autotune_target"] == 60 and got[-1] == (60, True)
    assert p.run(p.set_autotune_target(5, False))["target"] == 20 and got[-1] == (20, False)   # 20 fps minimum
    r = p.run(p.set_autotune_target(0, False))                  # no target: the screen's highest refresh rate, Auto FPS on
    assert r["target"] == 120 and r["auto_fps"] is True and p.settings["autotune_target"] == 120 and got[-1] == (120, True)
    started = {}
    monkeypatch.setattr(p.autotune, "start", lambda *a, **k: started.update(k))
    p.settings.update(autotune=True, autotune_target=45, autotune_auto_fps=False, autotune_plugged=True)
    p._start_game_power("123", True)
    assert started["target"] == 45 and started["auto_fps"] is False and started["fg"] == 0


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


def test_a_frontend_games_limit_does_not_change_because_autotune_saved_learning(plugin):
    """F: ES-DE's own pick is 30 W; the last pick on battery (another game) is 10 W. A game ES-DE starts, never picked,
    works under ES-DE's 30 W. After one session Autotune saved `learned` under the game's key; the next session must
    still work under ES-DE's 30 W: learning is not a pick."""
    p = plugin
    esde, game = "3119184808", "3119184808#ps2/GT4"
    p.run(p.game_started("42"))
    p.run(p.set_tdp(10))                       # a pick in another game: last_profile_battery = custom 10 W
    p.run(p.game_stopped("42"))
    p.profiles.set(esde, False, {"base": "custom", "tdp": 30})     # ES-DE's own pick
    p.run(p.game_started(esde))
    assert _frontend_event(p, "start", "ps2", "/roms/ps2/GT4.iso", "GT4")
    s = p.autotune.session
    assert s.appid == game and s.ceiling_tdp == 30                 # first session: ES-DE's pick
    s.learned.start, s.settled = 20, True                          # a session that held 20 W
    assert _frontend_event(p, "end", "ps2", "/roms/ps2/GT4.iso", "GT4")
    own = p.profiles.own(game, False)
    assert "learned" in own and not own.get("base")                # learning only, no pick
    assert _frontend_event(p, "start", "ps2", "/roms/ps2/GT4.iso", "GT4")
    s = p.autotune.session
    assert s.ceiling_tdp == 30, f"the ceiling moved to {s.ceiling_tdp} W because Autotune saved learning"
    assert s.cfg.start_w == 20 and s.learned.tdp == 20             # ... and it starts where the game held last time


def test_the_toast_says_nothing_was_saved_when_it_was(plugin):
    """An emulator that draws the whole limit at every limit, holding a reachable 30 fps target under the limit: the
    governor never needs the full limit, so its reference (`max_fps`) stays None. The session settles and
    _save_learned writes `learned`; the toast (summary) says "nothing new saved"."""
    p = plugin
    p.settings["autotune_target"] = 30
    p.run(p.set_tdp(30))
    p.run(p.game_started("77"))
    _Game(p.autotune, lambda tdp, *_: 30.0 if tdp >= 14 else 2.1 * tdp).run(150)
    r = p.run(p.game_stopped("77"))
    own = p.profiles.own("77", False)
    assert not ("learned" in own and "nothing new saved" in r["learned"]["summary"]), "toast contradicts the save"


def test_a_guard_step_whose_write_fails_leaves_no_cap_the_guard_cannot_lift(fake_hw, monkeypatch, tmp_path):
    """The guard steps (95 °C); the TDP write fails once. thermal_step returns False, so the guard records no step,
    but Autotune keeps thermal_cap. The package cools; the guard, with nothing recorded, never calls thermal_release:
    the governor works under the cap for the rest of the session."""
    at, s = _tuner(fake_hw, monkeypatch, ceiling=30, target=60)
    _Game(at, lambda tdp, *_: min(60.0, 3.0 * tdp)).run(30)          # needs 20 W
    temp = tmp_path / "temp"
    temp.write_text("97000\n")
    g = thermal.Guard(str(temp), "pkg")
    ok = {"n": 0}
    real = at.power.set_tdp

    def flaky(w, plugged=None):
        ok["n"] += 1
        return False if ok["n"] == 1 else real(w, plugged)
    monkeypatch.setattr(at.power, "set_tdp", flaky)
    g.check(at.held_w(), 8, at.thermal_step, limit_w=30, raise_tdp=at.thermal_release)
    temp.write_text("60000\n")                                       # cooled down, for minutes
    for _ in range(20):
        g.check(at.held_w(), 8, at.thermal_step, limit_w=30, raise_tdp=at.thermal_release)
    assert s.thermal_cap is None, "a cap nobody will lift"


@pytest.mark.parametrize("bad", [{"start": "14"}, {"start": None, "tdp": "x"}, {"start": [14]}, {"start": 14.7}, {"start": -3}, {"start": 1e9}, {"start": True}])
def test_a_hand_edited_or_foreign_record_does_not_stop_the_game_from_starting(plugin, bad):
    """profiles.json is a user file ("shareable as JSON"). A learned record with a start that is not a number must not
    keep the game's power and Autotune from starting."""
    p = plugin
    p.run(p.set_tdp(30))
    p.profiles.set("55", False, {"learned": {"ceiling": 30, **bad}})
    try:
        p.run(p.game_started("55"))
    except Exception as e:                       # noqa: BLE001
        pytest.fail(f"{bad}: game_started raised {type(e).__name__}: {e}; session={p.autotune.session is not None}, "
                    f"governed={p.power.governed}, loop={'gov None' if p.autotune.session and p.autotune.session.gov is None else 'ok'}")
    s = p.autotune.session
    assert 8 <= s.learned.tdp <= 30 and isinstance(s.learned.tdp, int)


def test_xemu_already_running_gets_no_write_of_autotunes_own(plugin, tmp_path, monkeypatch):
    """R6: xemu, no write. A session that starts while xemu already runs (resume, a new power source, the frontend
    restarting a session) writes Autotune's start level and the 8 s window, and only the own-pace check 3 s later puts
    the pick back: 3 s of xemu at mid-range after every resume."""
    p = plugin
    proc = tmp_path / "proc"
    (proc / "4242").mkdir(parents=True)
    (proc / "4242" / "comm").write_text("xemu\n")
    monkeypatch.setattr(autotune, "PROC", str(proc))
    p.run(p.set_tdp(30))
    p.run(p.game_started("3119184808"))
    assert p.chip() == 30, "Autotune's start level is on the chip of a game it must stay out of"


def test_a_pause_of_half_a_minute_does_not_throw_away_what_the_session_held(plugin, monkeypatch):
    """A game holds 14 W for two minutes; the player pauses it (no frames, e.g. a paused game that stops presenting)
    for 31 s; frames come back for 40 s; the game is closed. The session held 14 W for 2 minutes: the saved start
    should say so. The give-up replaces the governor (its held_s) and restarts at the full limit."""
    p = plugin
    p.settings["autotune_target"] = 30
    p.run(p.set_tdp(30))
    p.run(p.game_started("88"))
    fps = {"on": True}
    g = _Game(p.autotune, lambda tdp, *_: (30.0 if tdp >= 14 else 2.1 * tdp) if fps["on"] else None)
    import time as _t
    monkeypatch.setattr(autotune, "time", type("C", (), {"monotonic": staticmethod(lambda: g.t), "time": staticmethod(_t.time)}))
    g.run(150)
    fps["on"] = False
    g.run(31)
    fps["on"] = True
    g.run(40)
    p.run(p.game_stopped("88"))
    own = p.profiles.own("88", False).get("learned", {})
    assert own.get("start") == 14, f"saved start {own.get('start')} (tdp {own.get('tdp')})"


def test_a_mangoapp_gap_of_seconds_is_not_filled_from_the_other_log(fake_hw, tmp_path, monkeypatch):
    """mangoapp gives rows, then none for 5 s (under MANGOAPP_RELOG_S) while the game's own MangoHud log writes a row a
    second. The original gives no frames for those looks; without the "between two rows" return the own log's rows
    come back as frames from the second silent look on."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(autotune.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(autotune, "mangoapp_command", lambda c: True)
    logs = tmp_path / "mangohud"
    logs.mkdir()
    own = logs / "game_2026-11-15_14-00-00.csv"
    own.write_text("os\nLinux\nfps,frametime\n")
    live = tmp_path / "mangoapp_2026-11-15_14-00-01.csv"
    _mango_rows(live, [16.7] * 3, header=True)
    smp = autotune.Sampler(fake_hw, str(logs), home=str(tmp_path))
    smp.set_prefix(None)
    smp.frametime()
    _mango_rows(live, [16.7] * 30)
    clock["t"] += 0.5
    assert smp.frametime()[1] == pytest.approx(59.9, abs=0.1)
    got = []
    for i in range(10):                                   # 5 s: mangoapp silent, the own log a row a second
        if i % 2 == 0:
            with open(own, "a") as f:
                f.write("60.0,16.7\n")
        clock["t"] += 0.5
        smp._log_checked = -1e9
        got.append(smp.frametime())
    assert all(x == (None, None) for x in got), got
