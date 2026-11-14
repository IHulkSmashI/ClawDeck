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
        # Quiet has no temperatures of its own: MSI's stay
        assert [int(open(f"{h}/pwm{n}_auto_point{i}_temp").read()) for i in range(1, 7)] == FACTORY_T
    sysfs.revert_all()                                   # closing a game must not undo the fan choice
    assert open(f"{h}/pwm1_enable").read().strip() == "1"
    assert fan.apply(h, "auto")["ok"]
    assert open(f"{h}/pwm1_enable").read().strip() == "2" and open(f"{h}/pwm2_enable").read().strip() == "2"


def _restore_like_the_driver(monkeypatch):
    """enable=2 on either fan makes the real driver put back the boot-time tables of both fans."""
    real = sysfs.write

    def write(path, value, persist=False):
        ok = real(path, value, persist=persist)
        if str(path).endswith("_enable") and int(value) == 2:
            h = os.path.dirname(str(path))
            for n in (1, 2):
                for i in range(6):
                    with open(f"{h}/pwm{n}_auto_point{i + 1}_temp", "w") as f:
                        f.write(f"{FACTORY_T[i]}\n")
                    with open(f"{h}/pwm{n}_auto_point{i + 1}_pwm", "w") as f:
                        f.write(f"{FACTORY_P[i]}\n")
        return ok
    monkeypatch.setattr(fan.sysfs, "write", write)


def test_a_preset_after_extreme_is_back_on_msis_temperatures(tmp_path, monkeypatch):
    root, h = _hwmon(tmp_path)
    _restore_like_the_driver(monkeypatch)
    for p in ("extreme", "quiet", "cooling", "extreme", "full"):
        r = fan.apply(h, p)
        assert r["ok"], (p, r)
        temps = fan.PRESET_TEMPS.get(p, FACTORY_T)
        for f in r["read"]["fans"]:                        # both fans: the second fan's restore must not undo the first
            assert f["duties"] == fan.PRESETS[p] and f["temps"] == temps, (p, f)


def test_extreme_writes_its_own_temperatures_on_both_fans_in_order(tmp_path, monkeypatch):
    root, h = _hwmon(tmp_path)
    seen, real = [], sysfs.write

    def write(path, value, persist=False):
        ok = real(path, value, persist=persist)
        if str(path).endswith("_temp") and "pwm1_" in str(path):
            seen.append([int(open(f"{h}/pwm1_auto_point{i}_temp").read()) for i in range(1, 7)])
        return ok
    monkeypatch.setattr(fan.sysfs, "write", write)
    assert fan.apply(h, "extreme")["ok"]
    for n in (1, 2):
        assert [int(open(f"{h}/pwm{n}_auto_point{i}_temp").read()) for i in range(1, 7)] == fan.PRESET_TEMPS["extreme"]
    assert seen and all(t == sorted(t) for t in seen)    # never out of order between two writes


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
