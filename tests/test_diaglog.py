"""Diagnostics log (Setup -> Diagnostics log): the 1 Hz rows, the controller's reports, rotation and the export."""
import asyncio
import csv
import gzip
import os
import struct
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clawdeck import autotune, diaglog, hw, power  # noqa: E402
from test_backend import _load_main, fake_hw  # noqa: E402,F401  (the fake sysfs tree and main.py under a stand-in Decky)


def _w(p, v):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(f"{v}\n")


def _tree(tmp_path):
    """A Claw 8 EX's files: xe freq0 with gtidle and throttle reasons, RAPL, x86_pkg_temp, BAT1, hwmon fans."""
    t = str(tmp_path)
    gt = f"{t}/card0/device/tile0/gt0"
    files = {f"{gt}/freq0/cur_freq": 1950, f"{gt}/freq0/act_freq": 1900, f"{gt}/gtidle/idle_residency_ms": 1000,
             f"{gt}/freq0/throttle/reasons": "", f"{t}/rapl/energy_uj": 1_000_000,
             f"{t}/rapl/constraint_0_power_limit_uw": 17_000_000, f"{t}/rapl/constraint_1_power_limit_uw": 19_000_000,
             f"{t}/pkg_temp": 61500, f"{t}/BAT1/status": "Discharging", f"{t}/BAT1/current_now": -64_602_000,
             f"{t}/BAT1/voltage_now": 14_668_000, f"{t}/BAT1/capacity": 71, f"{t}/cpu0_freq": 2_400_000,
             f"{t}/hwmon/name": "msi_wmi_platform", f"{t}/hwmon/fan1_input": 3100, f"{t}/hwmon/fan2_input": 2900,
             f"{t}/hwmon/pwm1_enable": 2, f"{t}/hwmon/pwm2_enable": 2}
    for p, v in files.items():
        _w(p, v)
    paths = hw.Paths(gpu_card=f"{t}/card0", gpu_cur_freq=f"{gt}/freq0/cur_freq", rapl_energy=f"{t}/rapl/energy_uj",
                     rapl_pl1=f"{t}/rapl/constraint_0_power_limit_uw", rapl_pl2=f"{t}/rapl/constraint_1_power_limit_uw",
                     pkg_temp=f"{t}/pkg_temp", battery=f"{t}/BAT1")
    return paths, gt


def _rows(p):
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_rows_power_gpu_battery_fans_autotune_and_top(tmp_path, monkeypatch):
    paths, gt = _tree(tmp_path)
    monkeypatch.setattr(diaglog, "CPU_FREQ", str(tmp_path / "cpu0_freq"))
    clock = iter([100.0, 101.0, 102.0])
    monkeypatch.setattr(diaglog.time, "monotonic", lambda: next(clock))
    procs = iter([{1: ("gamescope-wl", 100), 2: ("Game.exe", 1000)},
                  {1: ("gamescope-wl", 120), 2: ("Game.exe", 1300), 3: ("new", 5)}])
    monkeypatch.setattr(diaglog, "_procs", lambda: next(procs))
    monkeypatch.setattr(diaglog, "TOP_EVERY", 2)
    d = diaglog.DiagLog(str(tmp_path / "diag"), paths, str(tmp_path / "hwmon"), lambda: False)
    at = {"running": True, "phase": "hold", "tdp": 14, "last_action": "down 1 W: 30 fps held", "last_delta": "-1 W"}
    d.row({"game": "2420110", "at": at, "guard_w": None})
    _w(paths.rapl_energy, 10_000_000)                 # 9 J in 1 s
    _w(f"{gt}/gtidle/idle_residency_ms", 1250)       # idle 250 ms of 1000: 75 % busy
    _w(f"{gt}/freq0/throttle/reasons", "pl1 thermal")
    d.row({"game": "2420110", "at": at, "guard_w": 33})
    rows = _rows(tmp_path / "diag" / f"{time.strftime('%Y-%m-%d')}.csv")
    assert list(rows[0]) == diaglog.FIELDS
    assert rows[0]["pkg_w"] == "" and rows[0]["gpu_busy_pct"] == "" and rows[0]["top_cpu"] == ""   # first row
    assert (rows[0]["fan1_rpm"], rows[0]["fan2_rpm"]) == ("3100", "2900")       # fans: the first row, then every FAN_EVERY
    r = rows[1]
    assert (r["pkg_w"], r["pl1_w"], r["pl2_w"], r["pkg_c"], r["cpu_mhz"]) == ("9.0", "17", "19", "62", "2400")
    assert (r["gpu_busy_pct"], r["gpu_act_mhz"], r["gpu_cur_mhz"], r["gpu_throttle"]) == ("75", "1900", "1950", "pl1+thermal")
    # the current as the driver reports it: the EX's 16-bit error stays visible
    assert (r["bat_status"], r["bat_ma"], r["bat_mv"], r["bat_pct"], r["ac"]) == ("Discharging", "-64602", "14668", "71", "0")
    assert (r["fan1_rpm"], r["fan2_rpm"], r["game"], r["at_phase"], r["at_tdp_w"]) == ("", "", "2420110", "hold", "14")
    assert (r["at_action"], r["guard_w"]) == ("down 1 W: 30 fps held (-1 W)", "33")
    hz = os.sysconf("SC_CLK_TCK")
    assert r["top_cpu"].split(";")[0] == f"Game.exe:{300 * 100 / hz / 1:.0f}%"      # ticks over the 1 s since the last walk


def test_no_hardware_writes_empty_cells(tmp_path, monkeypatch):
    monkeypatch.setattr(diaglog, "CPU_FREQ", str(tmp_path / "none"))
    monkeypatch.setattr(diaglog, "_procs", lambda: {})
    d = diaglog.DiagLog(str(tmp_path / "diag"), hw.Paths(), None, lambda: None)
    d.row({})
    d.row({"at": {"running": False, "enabled": False}})
    rows = _rows(tmp_path / "diag" / f"{time.strftime('%Y-%m-%d')}.csv")
    assert all(v == "" for k, v in rows[1].items() if k != "time")


def test_decode_mode_report_and_ack():
    mode = bytes([0x10, 0, 0, 0x3c, 0x27, 0x04, 0x00]) + bytes(57)
    assert diaglog.decode_report(mode) == "10 00 00 3c 27 04 00 00 = mode desktop, M-keys macro"
    assert diaglog.decode_report(bytes([0x10, 0, 0, 0x3c, 0x06]) + bytes(59)).endswith("= ACK")
    assert diaglog.decode_report(bytes([0x10, 0, 0, 0x3c, 0x27, 0x09, 0x07]) + bytes(57)).endswith("mode 9, M-keys 7")


def test_find_controller_takes_the_config_hidraw_and_the_other_interfaces_event_nodes(tmp_path):
    root = tmp_path / "hid-msi"
    _w(f"{root}/0003:0DB0:1901.0001/gamepad_mode", "xinput")
    os.makedirs(f"{root}/0003:0DB0:1901.0001/hidraw/hidraw0")
    os.makedirs(f"{root}/0003:0DB0:1901.0002/hidraw/hidraw1")
    os.makedirs(f"{root}/0003:0DB0:1901.0002/input/input36/event5")
    os.makedirs(f"{root}/0003:0DB0:1901.0002/input/input37/event6")
    assert diaglog.find_controller(str(root), "/dev") == ("/dev/hidraw0", ["/dev/input/event5", "/dev/input/event6"])
    assert diaglog.find_controller(str(tmp_path / "none")) == (None, [])


def test_controller_reports_keys_and_moves_are_logged_from_the_event_loop(tmp_path, monkeypatch):
    """FIFOs stand in for the hidraw and event nodes: the same non-blocking reads and add_reader as on the Claw."""
    cfg, ev = str(tmp_path / "hidraw0"), str(tmp_path / "event6")
    os.mkfifo(cfg)
    os.mkfifo(ev)
    monkeypatch.setattr(diaglog, "find_controller", lambda: (cfg, [ev]))
    d = diaglog.DiagLog(str(tmp_path), hw.Paths(), None, lambda: None)
    e = struct.Struct("llHHi")

    async def run():
        d.start(asyncio.get_running_loop())
        wc, we = os.open(cfg, os.O_WRONLY), os.open(ev, os.O_WRONLY)
        os.write(wc, bytes([0x10, 0, 0, 0x3c, 0x27, 0x04, 0x00]) + bytes(57))
        os.write(we, e.pack(0, 0, 2, 0, 5) + e.pack(0, 0, 2, 1, -3) + e.pack(0, 0, 0, 0, 0)
                 + e.pack(0, 0, 1, 272, 1) + e.pack(0, 0, 0, 0, 0))
        await asyncio.sleep(0.05)
        d.tick()
        os.close(wc)                                  # the device goes away
        await asyncio.sleep(0.05)
        d.stop()
        os.close(we)
    asyncio.run(run())
    lines = open(tmp_path / f"{time.strftime('%Y-%m-%d')}-controller.txt").read().splitlines()
    text = [ln.split(" ", 1)[1] for ln in lines]
    assert text == [f"cfg {cfg} opened", "event6 " + ev + " opened",
                    "cfg 10 00 00 3c 27 04 00 00 = mode desktop, M-keys macro",
                    "event6 key 272 down", "event6 2 move events", "cfg closed"]
    assert d._fds == {}


def test_controller_found_again_after_it_went_away(tmp_path, monkeypatch):
    cfg = str(tmp_path / "hidraw0")
    found = {"n": 0}

    def find():
        found["n"] += 1
        return (cfg if os.path.exists(cfg) else None), []
    monkeypatch.setattr(diaglog, "find_controller", find)
    d = diaglog.DiagLog(str(tmp_path), hw.Paths(), None, lambda: None)

    async def run():
        d.start(asyncio.get_running_loop())
        os.mkfifo(cfg)
        for _ in range(diaglog.FIND_EVERY):
            d.tick()
        n = len(d._fds)
        d.stop()
        return n
    assert asyncio.run(run()) == 1 and found["n"] == 2     # at the start, then once after FIND_EVERY seconds


def test_rotation_gzips_earlier_days_and_keeps_days_and_size_in_bounds(tmp_path, monkeypatch):
    folder = tmp_path / "diag"
    folder.mkdir()
    today = time.strftime("%Y-%m-%d")
    days = [f"2026-10-{d:02d}" for d in range(1, 11)]
    for day in days:
        (folder / f"{day}.csv").write_text("x" * 100)
        (folder / f"{day}-controller.txt").write_text("y" * 10)
    (folder / f"{today}.csv").write_text("today")
    monkeypatch.setattr(diaglog, "KEEP_DAYS", 6)                 # today and the 5 latest earlier days
    d = diaglog.DiagLog(str(folder), hw.Paths(), None, lambda: None)
    d.rotate()
    left = sorted(os.listdir(folder))
    assert left[-1] == f"{today}.csv"                            # today stays as it is
    assert {n[:10] for n in left[:-1]} == set(days[-5:])
    assert all(n.endswith(".gz") for n in left[:-1])
    assert gzip.open(folder / f"{days[-1]}.csv.gz").read() == b"x" * 100
    monkeypatch.setattr(diaglog, "MAX_BYTES", 1)                 # over the cap: the oldest days go, never today
    d.rotate()
    assert os.listdir(folder) == [f"{today}.csv"]


def test_report_has_the_last_rows_without_the_header_and_the_controller_lines(tmp_path):
    folder = tmp_path / "diag"
    folder.mkdir()
    today = time.strftime("%Y-%m-%d")
    (folder / f"{today}.csv").write_text(",".join(diaglog.FIELDS) + "\n" + "".join(f"row{i}\n" for i in range(700)))
    (folder / f"{today}-controller.txt").write_text("12:00:00.000 cfg ... = mode desktop, M-keys macro\n")
    r = diaglog.report(str(folder))
    assert r["fields"] == diaglog.FIELDS and r["rows"][0] == "row100" and r["rows"][-1] == "row699" and len(r["rows"]) == 600
    assert r["controller"] == ["12:00:00.000 cfg ... = mode desktop, M-keys macro"]
    assert diaglog.report(str(tmp_path / "none")) is None
    assert diaglog.status(str(folder)) == {"folder": str(folder), "days": 1, "bytes": sum(
        os.path.getsize(folder / n) for n in os.listdir(folder))}


def test_switch_starts_the_loop_rows_come_and_it_ends_when_switched_off(tmp_path, fake_hw, monkeypatch):
    m = _load_main(tmp_path, monkeypatch)
    p = m.Plugin.__new__(m.Plugin)
    p.settings, p.running_key = {}, "2420110"
    p._save_settings = lambda: None
    pw = power.Power(fake_hw)
    p.autotune = autotune.Autotune(fake_hw, pw, "/nonexistent")
    p.guard = type("G", (), {"stepped_w": None})()
    p.diag = diaglog.DiagLog(str(tmp_path / "diag"), hw.Paths(), None, lambda: None)
    p._diag_task = None
    monkeypatch.setattr(diaglog, "find_controller", lambda: (None, []))
    rows = []

    def row(state):
        rows.append(state)
        if len(rows) == 3:
            p.settings["diag_log"] = False                # what set_setting("diag_log", False) leaves
    monkeypatch.setattr(p.diag, "row", row)
    real_sleep = asyncio.sleep

    async def fast(_s):
        await real_sleep(0)
    monkeypatch.setattr(m.asyncio, "sleep", fast)

    async def run():
        assert (await p.diag_status())["on"] is False
        p._ensure_diag()                                   # off: no task
        assert p._diag_task is None
        await p.set_setting("diag_log", True)
        assert (await p.diag_status())["on"] is True
        await asyncio.wait_for(p._diag_task, 5)
    asyncio.run(run())
    assert len(rows) == 3 and rows[0]["game"] == "2420110" and rows[0]["at"]["running"] is False
    assert p.diag._loop is None                           # stopped: the controller nodes are closed


def test_export_carries_the_diagnostics_log(tmp_path, fake_hw, monkeypatch):
    from clawdeck import health
    folder = tmp_path / "diag"
    folder.mkdir()
    (folder / f"{time.strftime('%Y-%m-%d')}.csv").write_text(",".join(diaglog.FIELDS) + "\nrow\n")
    for name in ("checks", "device_report", "game_reports"):
        monkeypatch.setattr(health, name, lambda *a, **k: None)
    monkeypatch.setattr(health.hw, "mesa_version", lambda: None)
    monkeypatch.setattr(health.hhd, "available", lambda: False)
    dest = tmp_path / "out" / "report.json"
    health.collect_logs(fake_hw, str(tmp_path), str(dest), None, str(folder))
    import json
    assert json.load(open(dest))["diag"]["rows"] == ["row"]
