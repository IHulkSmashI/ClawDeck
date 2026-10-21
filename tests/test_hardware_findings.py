"""Tests for what the first run on a real Claw 8 EX (Bazzite 44) showed: docs/HARDWARE-FINDINGS.md. Every fake
value below is copied from docs/surveys/claw8ex-bazzite-44.20261012.1.txt."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from clawdeck import autotune, battery, controller, hhd, hw, power, shadowblip, sysfs  # noqa: E402


def _w(path, value):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"{value}\n")


def _hw(paths, model_id="MS-1T91"):
    return hw.Hardware(product="Claw 8 EX AI+ CG3EM Launch Pack", model=hw.MODELS["MS-1T91"], known=True, kernel="t",
                       cpu_model="t", gpu_driver="xe", paths=paths, model_id=model_id)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    sysfs.set_dry_run(False)
    sysfs._originals.clear()
    monkeypatch.setattr(hhd, "available", lambda: False)


# 4. DMI: the EX's product_name is a marketing string, the board id is what matches
def test_board_name_matches_before_product_name():
    assert hw.match_model("MS-1T91", "Claw 8 EX AI+ CG3EM Launch Pack")[0] == "MS-1T91"
    assert hw.match_model("", "MS-1T52")[0] == "MS-1T52"            # older firmware: id in product_name
    assert hw.match_model("X", "Y") == ("", None)


def test_ex_gets_its_own_profiles_not_the_defaults():
    h = _hw(hw.Paths())
    assert h.model_key == "MS-1T91"
    assert hw.profiles_for(h.model_key) is hw.PROFILES["MS-1T91"]


# 5. Mesa: the ICD json names the library; version from the library's own string
def test_intel_vulkan_lib_from_icd_json_and_version(tmp_path, monkeypatch):
    lib = tmp_path / "lib64/libvulkan_intel.so"
    lib.parent.mkdir(parents=True)
    lib.write_bytes(b"\x7fELF...Mesa 26.f...\x00Mesa 26.2.4\x00...")
    icd = tmp_path / "icd.d"
    _w(icd / "intel_icd.x86_64.json", '{"ICD": {"library_path": "%s", "api_version": "1.4.354"}}' % lib)
    _w(icd / "intel_hasvk_icd.x86_64.json", '{"ICD": {"library_path": "/nope/libvulkan_intel_hasvk.so"}}')
    monkeypatch.setattr(hw, "ICD_DIRS", (str(icd),))
    monkeypatch.delenv("VK_DRIVER_FILES", raising=False)
    assert hw.intel_vulkan_lib() == str(lib)
    assert hw.mesa_version() == "26.2.4"          # not the stray "Mesa 26.f"


# 6. Battery: uAh + uV, no energy_*, no power_now, current_now not credible
@pytest.fixture
def ex_battery(tmp_path):
    b = tmp_path / "BAT1"
    for n, v in (("charge_now", 4952000), ("charge_full", 5176000), ("charge_full_design", 5000000),
                 ("voltage_now", 17472000), ("current_now", -65026000), ("capacity", 95), ("status", "Discharging"),
                 ("charge_control_end_threshold", 0)):
        _w(b / n, v)
    return _hw(hw.Paths(battery=str(b), charge_limit=str(b / "charge_control_end_threshold"))), b


def test_ex_battery_units(ex_battery):
    h, _ = ex_battery
    st = battery.Battery(h).status()
    assert st["energy_wh"] == pytest.approx(86.52, abs=0.01)       # 4.952 Ah x 17.472 V
    assert st["energy_full_wh"] == pytest.approx(90.43, abs=0.01)
    assert st["health_pct"] == 100.0                               # 103.5 % on a new pack, shown as 100
    assert st["power_w"] is None                                   # -65 A x 17 V is not believed; no second point yet
    assert st["charge_limit"] == 100                               # 0 = never set


def test_ex_battery_power_from_charge_change(ex_battery, monkeypatch):
    h, b = ex_battery
    bat = battery.Battery(h)
    t = [1000.0]
    monkeypatch.setattr(battery.time, "monotonic", lambda: t[0])
    assert bat.status()["power_w"] is None
    t[0] += 60
    _w(b / "charge_now", 4952000 - 10000)          # 10 mAh in 60 s = 0.6 A at 17.47 V
    assert bat.status()["power_w"] == pytest.approx(10.48, abs=0.01)


def test_plausible_current_now_is_used(ex_battery):
    h, b = ex_battery
    _w(b / "current_now", -800000)                 # 0.8 A
    assert battery.Battery(h).status()["power_w"] == pytest.approx(13.98, abs=0.01)


# 1. TDP through MSI firmware: no PL3 on the EX, values clamped to the attribute's own range
def test_firmware_tdp_without_pl3_and_clamped(tmp_path):
    base = tmp_path / "fw"
    for a, cur, lo, hi in (("ppt_pl1_spl", 0, 8, 35), ("ppt_pl2_sppt", 0, 9, 45)):
        _w(base / a / "current_value", cur)
        _w(base / a / "min_value", lo)
        _w(base / a / "max_value", hi)
    p = hw.Paths(msi_pl1=str(base / "ppt_pl1_spl/current_value"), msi_pl2=str(base / "ppt_pl2_sppt/current_value"))
    pw = power.Power(_hw(p))
    assert pw.set_tdp(20, plugged=False)
    assert sysfs.read_int(p.msi_pl1) == 20 and 21 <= sysfs.read_int(p.msi_pl2) <= 45
    _w(base / "ppt_pl1_spl/max_value", 18)         # firmware says less than the model table
    assert pw.set_tdp(30, plugged=True)
    assert sysfs.read_int(p.msi_pl1) == 18
    assert power.firmware_range(str(tmp_path / "none/current_value")) == (0, 1000)


# 2. GPU load from PowerStation, read over D-Bus
def test_busctl_property_parsing():
    assert shadowblip.parse_property("y 15\n") == 15
    assert shadowblip.parse_property("d 15.000000") == 15.0
    assert shadowblip.parse_property('s "MSI Claw 8 EX AI+ CG3EM"') == "MSI Claw 8 EX AI+ CG3EM"
    assert shadowblip.parse_property("b true") is True
    assert shadowblip.parse_property("") is None and shadowblip.parse_property("y x") is None


def test_gpu_busy_and_classify(monkeypatch):
    monkeypatch.setattr(shadowblip, "_busctl", lambda *a: "y 97")
    assert shadowblip.gpu_busy_percent() == 97
    busy = autotune.Sample(t=0, frametime_ms=20, fps=50, gpu_mhz=1500, gpu_max_mhz=2300, cpu_pct=40, pkg_w=20, gpu_busy=97)
    assert autotune.classify(busy) == "gpu"         # busy says GPU-bound although the clock isn't pinned
    idle = autotune.Sample(t=0, frametime_ms=20, fps=50, gpu_mhz=2300, gpu_max_mhz=2300, cpu_pct=90, pkg_w=20, gpu_busy=50)
    assert autotune.classify(idle) == "cpu"         # pinned clock but a half-idle GPU: CPU-bound


def test_sampler_stops_asking_after_a_miss(monkeypatch):
    calls = []
    monkeypatch.setattr(shadowblip, "gpu_busy_percent", lambda: calls.append(1))
    s = autotune.Sampler(_hw(hw.Paths()), "/nonexistent")
    assert s.gpu_busy() is None and s.gpu_busy() is None
    assert len(calls) == 1


# 1. Controller: InputPlumber's composite device instead of Handheld Daemon
def test_controller_reports_inputplumber_without_hhd(monkeypatch):
    out = {
        ("--list", "tree", shadowblip.INPUTPLUMBER): "/\n/org\n/org/shadowblip/InputPlumber\n"
            "/org/shadowblip/InputPlumber/CompositeDevice0\n/org/shadowblip/InputPlumber/devices/source/event4\n",
    }

    def fake(*args):
        if args[0] == "get-property":
            return 's "MSI Claw 8 EX AI+ CG3EM"' if args[-1] == "Name" else 's "OpenGamepadUI Default"'
        return out.get(args)
    monkeypatch.setattr(shadowblip, "_busctl", fake)
    monkeypatch.setattr(hhd, "get_settings", lambda: None)
    monkeypatch.setattr(hhd, "get_state", lambda: None)
    st = controller.Controller().settings()
    assert st["available"] is False
    assert st["inputplumber"] == [{"path": "/org/shadowblip/InputPlumber/CompositeDevice0",
                                   "name": "MSI Claw 8 EX AI+ CG3EM", "profile": "OpenGamepadUI Default"}]
