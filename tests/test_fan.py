"""Fan presets and the charge limit against a fake msi_wmi_platform hwmon (the Claw 8 EX's layout)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

from clawdeck import fan, sysfs  # noqa: E402

FACTORY_T = [47, 50, 57, 64, 71, 78]
FACTORY_P = [153, 163, 173, 188, 204, 214]


def _hwmon(tmp_path):
    root = tmp_path / "hwmon"
    (root / "hwmon3").mkdir(parents=True)
    (root / "hwmon3" / "name").write_text("acpi_fan\n")
    h = root / "hwmon7"
    h.mkdir()
    (h / "name").write_text("msi_wmi_platform\n")
    for n in (1, 2):
        (h / f"fan{n}_input").write_text("3428\n")
        (h / f"pwm{n}_enable").write_text("2\n")
        for i in range(6):
            (h / f"pwm{n}_auto_point{i + 1}_temp").write_text(f"{FACTORY_T[i]}\n")
            (h / f"pwm{n}_auto_point{i + 1}_pwm").write_text(f"{FACTORY_P[i]}\n")
    return str(root), str(h)


def test_finds_the_msi_hwmon_only(tmp_path):
    root, h = _hwmon(tmp_path)
    assert fan.find_hwmon(root) == h
    assert fan.find_hwmon(str(tmp_path / "none")) is None


def test_snap_and_scale():
    assert [fan.snap(x) for x in (0, 10, 19, 20, 39, 40, 75, 120)] == [0, 0, 0, 40, 40, 40, 75, 100]
    assert fan.to_pwm(100) == 255 and fan.to_pwm(40) == 102 and fan.to_pwm(0) == 0
    assert fan.to_pct(fan.to_pwm(75)) == 75


def test_presets_write_both_fans_then_enable_and_survive_revert(tmp_path):
    root, h = _hwmon(tmp_path)
    r = fan.apply(h, "quiet")
    assert r["ok"], r
    for n in (1, 2):
        assert [int(open(f"{h}/pwm{n}_auto_point{i}_pwm").read()) for i in range(1, 7)] == [fan.to_pwm(d) for d in fan.PRESETS["quiet"]]
        assert open(f"{h}/pwm{n}_enable").read().strip() == "1"
        # temperatures are the EC's axis: never written
        assert [int(open(f"{h}/pwm{n}_auto_point{i}_temp").read()) for i in range(1, 7)] == FACTORY_T
    sysfs.revert_all()                                   # closing a game must not undo the fan choice
    assert open(f"{h}/pwm1_enable").read().strip() == "1"
    assert fan.apply(h, "auto")["ok"]
    assert open(f"{h}/pwm1_enable").read().strip() == "2" and open(f"{h}/pwm2_enable").read().strip() == "2"


def test_custom_snaps_and_needs_six(tmp_path):
    root, h = _hwmon(tmp_path)
    r = fan.apply(h, "custom", [0, 25, 50, 60, 80, 130])
    assert r["ok"]
    assert [fan.to_pct(int(open(f"{h}/pwm1_auto_point{i}_pwm").read())) for i in range(1, 7)] == [0, 40, 50, 60, 80, 100]
    assert not fan.apply(h, "custom", [50, 50])["ok"]
    assert not fan.apply(h, "turbo")["ok"]
    assert not fan.apply(None, "quiet")["ok"]


def test_charge_limit_and_off_restores_the_original(tmp_path):
    p = tmp_path / "charge_control_end_threshold"
    p.write_text("0\n")
    r = fan.set_charge(str(p), 80, 0)
    assert r["ok"] and p.read_text().strip() == "80"
    assert fan.set_charge(str(p), 30, 0)["wanted"] == 50          # never below 50 %
    sysfs.revert_all()
    assert p.read_text().strip() == "50"                          # persists past revert
    assert fan.set_charge(str(p), None, 0)["ok"] and p.read_text().strip() == "0"
    assert not fan.set_charge(None, 80, 0)["ok"]


def test_kde_charge_limit_written_and_removed(tmp_path, monkeypatch):
    from clawdeck import fan
    monkeypatch.setattr(fan.userexec, "chown_tree", lambda p, h: None)
    rc = tmp_path / ".config" / "powerdevilrc"
    rc.parent.mkdir(parents=True)
    rc.write_text("[General]\nfoo=1\n")
    assert fan.kde_charge_limit(str(tmp_path), 80)
    t = rc.read_text()
    assert "BatteryChargeStopThreshold=80" in t and "foo=1" in t
    assert fan.kde_charge_limit(str(tmp_path), None)
    assert "BatteryChargeStopThreshold" not in rc.read_text()
