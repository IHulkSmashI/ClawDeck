"""Tests: HHD schema walking, the launch environment, xess state and install, downloads safety, power revert of HHD
TDP, autotune pause, and the UI <-> backend wiring rule (every callable in src/api.ts exists in main.py)."""
import os
import re
import sys
import zipfile

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

from clawdeck import (autotune, downloads, hhd, hw, health,  # noqa: E402
                      launchenv, power, profiles, sysfs, xess)


def _w(path, value):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"{value}\n")


# a cut-down copy of what HHD's /api/v1/settings returns for a Claw (structure from hhd/device/claw/controllers.yml,
# hhd/plugins/rgb/settings.yml, adjustor/drivers/msi/settings.yml)
SCHEMA = {
    "controllers": {"claw": {"type": "container", "children": {
        "controller_mode": {"type": "mode", "title": "Emulation Mode", "default": "dualsense", "modes": {
            "dualsense": {"type": "container", "title": "Dualsense", "children": {
                "gyro": {"type": "bool", "title": "Gyro", "default": True},
                "gyro_scaling": {"type": "int", "title": "Gyro scale", "min": 1, "max": 100, "default": 40}}},
            "xbox": {"type": "container", "title": "Xbox", "children": {}},
        }},
        "swap_guide": {"type": "bool", "title": "Swap guide", "default": False},
        "main_chords": {"type": "multiple", "title": "Chords", "options": {"disabled": "Off", "qam": "QAM"},
                        "default": "disabled", "tags": ["advanced"]},
        "hidden": {"type": "bool", "title": "x", "tags": ["hide"]},
        "note": {"type": "display", "title": "info"},
    }}},
    "rgb": {"handheld": {"type": "container", "children": {
        "mode": {"type": "mode", "title": "RGB Mode", "modes": {
            "disabled": {"type": "container", "title": "Off"},
            "solid": {"type": "container", "title": "Solid", "children": {
                "hue": {"type": "int", "min": 0, "max": 359, "title": "Hue"}}}}}}}},
    "tdp": {"msi": {"type": "container", "children": {
        "fan": {"type": "mode", "title": "Custom Fan Curve", "default": "disabled", "modes": {
            "disabled": {"type": "container", "title": "Disabled"},
            "manual": {"type": "container", "title": "Manual", "children": {
                "st0": {"type": "int", "title": "40C", "min": 0, "max": 100, "step": 2},
                "st50": {"type": "int", "title": "50C", "min": 0, "max": 100, "step": 2},
                "st88": {"type": "int", "title": "90C", "min": 0, "max": 100, "step": 2},
                "reset": {"type": "action", "title": "Restore"}}}}}}}},
}


class FakeHHD:
    def __init__(self, monkeypatch, state=None):
        self.state = dict(state or {})
        self.posts = []
        monkeypatch.setattr(hhd, "get_settings", lambda: SCHEMA)
        monkeypatch.setattr(hhd, "get_state", lambda flat=True: dict(self.state))
        monkeypatch.setattr(hhd, "available", lambda: True)

        def _set(values):
            self.posts.append(dict(values))
            self.state.update(values)
            return dict(self.state)
        monkeypatch.setattr(hhd, "set_values", _set)


@pytest.fixture(autouse=True)
def _clean_sysfs():
    sysfs.set_dry_run(False)
    sysfs._originals.clear()
    yield
    sysfs.set_dry_run(False)
    sysfs._originals.clear()


def _hardware(paths=None, product="MS-1T91"):
    return hw.Hardware(product=product, model=hw.MODELS[product], known=True, kernel="test", cpu_model="test",
                       gpu_driver="xe", paths=paths or hw.Paths())


# ---- HHD schema ------------------------------------------------------------------------------------------------------
def test_describe_flattens_modes_and_skips_non_settings():
    d = {x["key"]: x for x in hhd.describe(SCHEMA)}
    assert d["controllers.claw.controller_mode.mode"]["options"][0]["value"] == "dualsense"
    assert d["controllers.claw.controller_mode.dualsense.gyro"]["mode_of"] == {
        "key": "controllers.claw.controller_mode.mode", "mode": "dualsense"}
    assert d["tdp.msi.fan.manual.st50"]["max"] == 100
    assert "controllers.claw.note" not in d and "tdp.msi.fan.manual.reset" not in d


def test_validate():
    d = {x["key"]: x for x in hhd.describe(SCHEMA)}
    assert hhd.validate(d["controllers.claw.swap_guide"], 1) is True
    assert hhd.validate(d["rgb.handheld.mode.solid.hue"], "120") == 120
    with pytest.raises(ValueError):
        hhd.validate(d["rgb.handheld.mode.solid.hue"], 400)
    with pytest.raises(ValueError):
        hhd.validate(d["rgb.handheld.mode.mode"], "disco")


def test_hhd_set_values_dry_run_sends_nothing(monkeypatch):
    sent = []
    monkeypatch.setattr(hhd, "_request", lambda m, u, b=None: sent.append((m, u, b)) or {"a": {"b": 1}})
    sysfs.set_dry_run(True)
    st = hhd.set_values({"a.b": 2})
    assert st["a.b"] == 2 and all(m == "GET" for m, _, _ in sent)


# ---- launch environment -----------------------------------------------------------------------------------------------
def test_launch_env_compose_and_write(tmp_path):
    env = launchenv.compose({"mesa": "26.1", "xell": True})
    assert env == {"MESA": "26.1", **xess.XELL_ENV}
    off = launchenv.compose({"xmx": False})
    assert off["VKD3D_DISABLE_EXTENSIONS"] == "" and "VK_DRIVER_FILES" in off      # the stock drivers for this game
    assert launchenv.compose({"xmx": True, "xell": False}) == {}
    r = launchenv.write(str(tmp_path), "1234", {"xell": True})
    assert open(r["path"]).read().splitlines()[1] == "IGDEXT_TRACE=1"
    assert launchenv.write(str(tmp_path), "1234", {"xell": False})["path"] is None
    assert not os.path.exists(os.path.join(tmp_path, "launch", "1234.env"))
    with pytest.raises(ValueError):
        launchenv.write(str(tmp_path), "../x", {"xell": True})
    with pytest.raises(ValueError):
        launchenv.compose({"mesa": "1; rm -rf"})


def test_launch_wrapper_reads_env_file(tmp_path):
    import subprocess
    (tmp_path / "launch").mkdir()
    (tmp_path / "launch" / "42.env").write_text("# c\nWINEDLLOVERRIDES=dxgi=n,b\nMESA=26.1\n")
    out = subprocess.run(["bash", os.path.join(ROOT, "tools", "clawdeck-launch.sh"), "env"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLAWDECK_RUNTIME": str(tmp_path),
                              "SteamAppId": "42", "WINEDLLOVERRIDES": "foo=n", "CLAWDECK_NO_MANGOHUD": "1"}).stdout
    assert "WINEDLLOVERRIDES=dxgi=n,b;foo=n" in out and "MESA=26.1" in out   # the user's own overrides are kept


def test_profiles_launch_slot_and_export(tmp_path):
    p = profiles.Profiles(str(tmp_path / "p.json"))
    p.set_launch("7", {"mesa": "26.1", "xell": True})
    p.set_launch("7", {"xell": None})
    assert p.get_launch("7") == {"mesa": "26.1"}
    q = profiles.Profiles(str(tmp_path / "q.json"))
    q.import_json(p.export_json("7"))
    assert q.get_launch("7") == {"mesa": "26.1"} and q.games() == ["7"]


# ---- xess ------------------------------------------------------------------------------------------------------------
def test_xess_state_from_package_layout(tmp_path):
    home = tmp_path
    pkg = home / ".local/share/clawdeck/xess-xmx-linux-1.4"
    _w(pkg / "lib/mesa-26.1.2/libvulkan_intel.so", "x")
    _w(pkg / "lib/mesa-26.3.0-rc1/libvulkan_intel.so", "x")
    _w(pkg / "lib/active", "26.3.0-rc1")
    _w(home / ".config/environment.d/50-xess-xmx.conf",
       f"VK_DRIVER_FILES={pkg}/run/intel_icd.json:/usr/share/vulkan/icd.d/intel_hasvk_icd.i686.json\n")
    _w(home / ".cache/xess-xmx/driver-status", "ok 2026-10-21 12:00:00 /x Mesa 26.3.0")
    st = xess.state(str(home))
    assert st["installed"] and st["version"] == "1.4" and st["default_driver"] == "26.3.0-rc1"
    assert st["drivers"] == ["26.1.2", "26.3.0-rc1"] and st["status"]["state"] == "ok"
    assert xess.parse_status("FAILED 2026 x: no device")["state"] == "failed"
    assert not xess.state(str(tmp_path / "nobody"))["installed"]
    with pytest.raises(ValueError):
        xess.switch_driver(str(home), "26.1; reboot")


def test_xess_install_dry_run(tmp_path):
    r = xess.install(str(tmp_path), {"repo": "a/b", "tag": "pending-verification"}, dry_run=True)
    assert r["dry_run"] and not os.path.exists(tmp_path / ".local")


# ---- downloads -------------------------------------------------------------------------------------------------------
def test_downloads_refuses_unpinned_and_unsafe(tmp_path):
    with pytest.raises(PermissionError):
        downloads.fetch({"repo": "a/b", "tag": "pending-verification"}, str(tmp_path))
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../evil.sh", "x")
    with pytest.raises(ValueError):
        downloads.extract(str(bad), str(tmp_path / "out"))
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("pkg/install.sh", "echo")
    assert os.path.exists(os.path.join(downloads.extract(str(good), str(tmp_path / "o2")), "pkg/install.sh"))
    assert downloads.pick_asset({"assets": [{"name": "x.txt"}, {"name": "xess-xmx-linux-20261018.zip"}]},
                                "xess-xmx-linux-*.zip")["name"] == "xess-xmx-linux-20261018.zip"
    # the real asset name seen 2026-10-21 matches the glob in versions.json
    v = downloads.load_versions(os.path.join(ROOT, "assets", "versions.json"))
    assert downloads.pick_asset({"assets": [{"name": "v1.zip"}, {"name": "xess-xmx-linux-20261018.zip"}]}, v["xess_xmx_linux"]["asset_glob"])["name"] == "xess-xmx-linux-20261018.zip"


def test_versions_file_is_valid():
    v = downloads.load_versions(os.path.join(ROOT, "assets", "versions.json"))
    for k in ("xess_xmx_linux",):
        assert "repo" in v[k] and "asset_glob" in v[k]


# ---- power + autotune ------------------------------------------------------------------------------------------------
def test_power_reverts_hhd_tdp(monkeypatch):
    fake = FakeHHD(monkeypatch, {"tdp.msi.tdp.mode": "balanced", "tdp.msi.tdp.custom.tdp": 30,
                                 "tdp.msi.tdp.custom.boost": True, "hhd.settings.tdp_ready": True})
    monkeypatch.setattr(hhd, "get_tdp", lambda: {"mode": fake.state["tdp.msi.tdp.mode"],
                                                 "custom_tdp": fake.state["tdp.msi.tdp.custom.tdp"], "boost": True})
    monkeypatch.setattr(hhd, "tdp_ready", lambda: True)
    monkeypatch.setattr(hhd, "set_tdp", lambda w, boost=True: fake.state.update(
        {"tdp.msi.tdp.mode": "custom", "tdp.msi.tdp.custom.tdp": w}) or True)
    pw = power.Power(_hardware())
    assert pw.set_tdp(18, plugged=False)
    assert pw.set_tdp(22, plugged=False)
    assert fake.state["tdp.msi.tdp.mode"] == "custom"
    pw.revert_all()
    assert fake.state["tdp.msi.tdp.mode"] == "balanced" and fake.state["tdp.msi.tdp.custom.tdp"] == 30


def test_autotune_pause_stops_learning(monkeypatch):
    pw = power.Power(_hardware())
    calls = []
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: calls.append(w) or True)
    at = autotune.Autotune(_hardware(), pw, log_dir="/nonexistent")
    at.session = autotune.Session(appid="1", target_fps=60, floor_tdp=8, ceiling_tdp=35,
                                  learned=autotune.Learned(tdp=30, cpu_max_pct=100, gpu_min_mhz=None), settle=1)
    good = autotune.Sample(t=0, frametime_ms=10.0, fps=100, gpu_mhz=1000, gpu_max_mhz=2400, cpu_pct=40, pkg_w=20)
    monkeypatch.setattr(at.sampler, "sample", lambda: good)
    at._step()
    assert at.session.learned.tdp == 29
    at.pause()
    at._step(); at._step()
    assert at.session.learned.tdp == 29 and calls == [29]
    at.session = None


def test_autotune_start_clamps_saved_tdp(monkeypatch):
    import asyncio
    pw = power.Power(_hardware())
    monkeypatch.setattr(pw, "set_tdp", lambda w, plugged=None: True)
    at = autotune.Autotune(_hardware(), pw, log_dir="/nonexistent")

    async def go():
        at.start("1", 60, {"tdp": 35, "cpu_max_pct": 90, "gpu_min_mhz": None, "junk": 1}, 8, 25, plugged=False)
        t = at.session.learned.tdp
        at.stop()
        return t
    assert asyncio.run(go()) == 25


# ---- wiring rule: every UI call has a backend method ----------------------------------------------------------------
def test_every_callable_has_a_backend_method():
    api = open(os.path.join(ROOT, "src", "api.ts")).read()
    names = set(re.findall(r'callable<.*>\("([a-z_]+)"\)', api))   # one per line; types may nest <>
    main = open(os.path.join(ROOT, "main.py")).read()
    methods = set(re.findall(r"async def ([a-z_]+)\(self", main))
    assert names, "no callables found"
    assert names <= methods, f"UI calls without a backend: {sorted(names - methods)}"


# ---- tools -----------------------------------------------------------------------------------------------------------
def test_soak_verdict():
    import importlib.util
    spec = importlib.util.spec_from_file_location("soak", os.path.join(ROOT, "tools", "soak.py"))
    soak = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(soak)
    assert soak.slope([0, 1, 2, 3], [20, 21, 22, 23]) == 1.0
    v = soak.verdict(0.2, 25, 0.1, 4)
    assert v[0].endswith("ok") and v[1].endswith("ok") and v[2].endswith("flat") and len(v) == 3
    assert "OVER" in soak.verdict(0.9, 40, 2, 1)[0] and "leak" in soak.verdict(0.9, 40, 2, 1)[2]


def test_power_profile_script_dry_run():
    import subprocess
    r = subprocess.run(["bash", os.path.join(ROOT, "tools", "power-profile.sh"), "battery", "--dry-run"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "== battery" in r.stdout
    assert subprocess.run(["bash", os.path.join(ROOT, "tools", "power-profile.sh"), "turbo"], capture_output=True).returncode == 1


# ---- current Bazzite: no HHD, MSI firmware limits, InputPlumber ------------------------------------------------------
def _msi_fw(tmp_path):
    base = tmp_path / "fw"
    for a, v in (("ppt_pl1_spl", 20), ("ppt_pl2_sppt", 30), ("ppt_pl3_fppt", 40)):
        _w(base / a / "current_value", v)
    return hw.Paths(msi_pl1=str(base / "ppt_pl1_spl/current_value"), msi_pl2=str(base / "ppt_pl2_sppt/current_value"),
                    msi_pl3=str(base / "ppt_pl3_fppt/current_value"))


def test_tdp_through_msi_firmware_when_no_hhd(tmp_path, monkeypatch):
    monkeypatch.setattr(hhd, "available", lambda: False)
    p = _msi_fw(tmp_path)
    pw = power.Power(_hardware(p))
    assert pw.tdp_backend() == "msi-firmware"
    assert pw.set_tdp(15, plugged=False)
    # EX: 35 W ceiling, 45 W boost -> boost scales 15 * 45/35 = 19 W; PL3 never above the boost limit
    assert [sysfs.read_int(x) for x in (p.msi_pl1, p.msi_pl2, p.msi_pl3)] == [15, 19, 19]
    assert pw.set_tdp(99, plugged=True)                         # clamped to the plugged ceiling
    assert [sysfs.read_int(x) for x in (p.msi_pl1, p.msi_pl2, p.msi_pl3)] == [35, 45, 45]
    pw.revert_all()
    assert [sysfs.read_int(x) for x in (p.msi_pl1, p.msi_pl2, p.msi_pl3)] == [20, 30, 40]


def test_msi_firmware_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(hhd, "available", lambda: False)
    p = _msi_fw(tmp_path)
    sysfs.set_dry_run(True)
    assert power.Power(_hardware(p)).set_tdp(25, plugged=True)
    assert sysfs.read_int(p.msi_pl1) == 20


def test_stack_detection(monkeypatch):
    from clawdeck import stack
    monkeypatch.setattr(stack, "_active", lambda units: {"inputplumber.service": True, "powerstation.service": True})
    monkeypatch.setattr(stack, "_hhd_unit_active", lambda: False)
    monkeypatch.setattr(stack.os.path, "exists", lambda p: False)
    s = stack.detect()
    assert s["controller"] == "inputplumber" and s["tdp_owner"] == "steam" and not s["conflict"]
    monkeypatch.setattr(stack, "_hhd_unit_active", lambda: True)
    assert stack.detect()["conflict"]


def test_health_never_offers_hhd_next_to_inputplumber(monkeypatch, tmp_path):
    from clawdeck import stack
    monkeypatch.setattr(stack, "detect", lambda: {"hhd": False, "inputplumber": True, "powerstation": True,
                                                  "controller": "inputplumber", "tdp_owner": "steam", "conflict": False})
    monkeypatch.setattr(hhd, "available", lambda: False)
    checks = {c["id"]: c for c in health.checks(_hardware(_msi_fw(tmp_path)), str(tmp_path))}
    assert checks["controller"]["ok"] and checks["controller"]["fix"] is None
    assert checks["tdp_owner"]["ok"] and "Steam" in checks["tdp_owner"]["detail"]
    assert not any(c.get("fix") == "enable_hhd" for c in checks.values())


def test_chown_tree_handles_a_single_file(tmp_path, monkeypatch):
    from clawdeck import userexec
    f = tmp_path / "report.json"
    f.write_text("{}")
    calls = []
    monkeypatch.setattr(userexec, "user_of", lambda home: type("U", (), {"pw_uid": 1000, "pw_gid": 1000})())
    monkeypatch.setattr(userexec.os, "geteuid", lambda: 0)
    monkeypatch.setattr(userexec.os, "lchown", lambda p, u, g: calls.append(p))
    userexec.chown_tree(str(f), str(tmp_path))
    assert calls == [str(f)]
    calls.clear()
    (tmp_path / "d").mkdir(); (tmp_path / "d" / "x").write_text("1")
    userexec.chown_tree(str(tmp_path / "d"), str(tmp_path))
    assert sorted(calls) == sorted([str(tmp_path / "d"), str(tmp_path / "d" / "x")])


# ---- scope reset (2026-10-21): XeSS from the fork, XeLL per game, the one-line health summary ---------------------
def test_xess_install_falls_back_to_upstream_when_the_fork_has_no_release(tmp_path, monkeypatch):
    import urllib.error
    asked = []

    def fake_fetch(entry, workdir, allow_unpinned=False):
        asked.append(entry["repo"])
        if entry["repo"].startswith("IHulkSmashI/"):
            raise urllib.error.HTTPError("https://api.github.com/x", 404, "Not Found", {}, None)
        pkg = tmp_path / ".local/share/clawdeck/xess-xmx-linux-20261018"
        _w(pkg / "install.sh", "echo hi"); _w(pkg / "uninstall.sh", "")
        return {"tag": "20261018", "archive": str(tmp_path / "a.zip"), "dir": str(pkg), "repo": entry["repo"]}
    monkeypatch.setattr(downloads, "fetch", fake_fetch)
    monkeypatch.setattr(xess.userexec, "run", lambda *a, **k: {"rc": 0, "out": "done"})
    entry = {"repo": "HHHHanasak1/xess-xmx-linux", "fork": "IHulkSmashI/xess-xmx-linux", "use_fork": True, "tag": "pending-verification"}
    r = xess.install(str(tmp_path), entry, allow_unpinned=True)
    assert r["ok"] and asked == ["IHulkSmashI/xess-xmx-linux", "HHHHanasak1/xess-xmx-linux"]
    assert "no release yet" in r["note"] and r["repo"] == "HHHHanasak1/xess-xmx-linux"
    assert xess.source_of(r["package_dir"]) == {"repo": "HHHHanasak1/xess-xmx-linux", "tag": "20261018"}
    # any other failure names the step and the real reason, never a bare "file not found"
    monkeypatch.setattr(downloads, "fetch", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no asset matching x in y")))
    assert xess.install(str(tmp_path), entry, allow_unpinned=True)["error"] == "download: FileNotFoundError: no asset matching x in y"
    monkeypatch.setattr(downloads, "fetch", lambda *a, **k: (_ for _ in ()).throw(urllib.error.HTTPError("https://u", 403, "rate limited", {}, None)))
    assert xess.install(str(tmp_path), entry, allow_unpinned=True)["error"] == "download: https://u answered 403 rate limited"


def test_xess_install_reports_the_script_failure(tmp_path, monkeypatch):
    pkg = tmp_path / ".local/share/clawdeck/xess-xmx-linux-1"
    _w(pkg / "install.sh", ""); _w(pkg / "uninstall.sh", "")
    monkeypatch.setattr(downloads, "fetch", lambda e, w, a=False: {"tag": "1", "archive": "a.zip", "dir": str(pkg), "repo": e["repo"]})
    monkeypatch.setattr(xess.userexec, "run", lambda *a, **k: {"rc": 1, "out": "== fetching\nmissing igdext64.dll - run this from the unpacked package\n"})
    r = xess.install(str(tmp_path), {"repo": "a/b", "tag": "1"})
    assert not r["ok"] and r["error"].startswith("install.sh exited 1: == fetching · missing igdext64.dll")


def test_xess_state_knows_a_fork_build_and_game_xell(tmp_path):
    home = tmp_path
    pkg = home / ".local/share/clawdeck/xess-xmx-linux-fork1"
    _w(pkg / "lib/mesa-26.3.0-rc1/libvulkan_intel.so", "x"); _w(pkg / "lib/active", "26.3.0-rc1")
    _w(pkg / "tools/xell-probe.sh", "")
    _w(pkg / xess.SOURCE_FILE, '{"repo": "IHulkSmashI/xess-xmx-linux", "tag": "fork1"}')
    _w(home / ".config/environment.d/50-xess-xmx.conf", f"VK_DRIVER_FILES={pkg}/run/intel_icd.json\n")
    st = xess.state(str(home))
    assert st["xell_capable"] and st["source"]["repo"].startswith("IHulkSmashI") and st["version"] == "fork1"
    pfx = home / xess.COMPATDATA / "42/pfx/drive_c"
    assert xess.game_xell(str(home), "42", False)["mode"] == "off"
    assert xess.game_xell(str(home), "42", True)["mode"] == "none"
    _w(pfx / "igdext_xell.log", "xellSetSleepMode ctx=1 game: minimumIntervalUs=0 lowLatency=1 boost=0")
    assert xess.game_xell(str(home), "42", True)["mode"] == "cross-vendor"
    _w(pfx / "igdext_trace.log", "XELLPROBE LatencyReductionExt(version 1, enabled 1, timings 1, slots 8)\nXELLPROBE RenderSubmitStart(frame 3)")
    assert xess.game_xell(str(home), "42", True)["mode"] == "driver"


def test_health_summary_one_line():
    checks = [{"id": "a", "title": "A", "ok": True}, {"id": "b", "title": "XeSS XMX driver", "ok": False},
              {"id": "c", "title": "optional thing", "ok": False, "optional": True}]
    s = health.summary(checks)
    assert s == {"ok": False, "total": 3, "problems": 1, "line": "1 to fix: XeSS XMX driver"}
    assert health.summary([checks[0]])["line"] == "All checks pass"


def test_launch_wrapper_rotates_xell_logs_for_one_launch(tmp_path):
    import subprocess
    (tmp_path / "launch").mkdir()
    (tmp_path / "launch" / "7.env").write_text("IGDEXT_XELL_PROBE=1\n")
    pfx = tmp_path / "compat/pfx/drive_c"
    _w(pfx / "igdext_trace.log", "old")
    subprocess.run(["bash", os.path.join(ROOT, "tools", "clawdeck-launch.sh"), "true"], check=True,
                   env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLAWDECK_RUNTIME": str(tmp_path), "SteamAppId": "7",
                        "STEAM_COMPAT_DATA_PATH": str(tmp_path / "compat"), "CLAWDECK_NO_MANGOHUD": "1"})
    assert not (pfx / "igdext_trace.log").exists() and (pfx / "igdext_trace.log.prev").read_text().strip() == "old"
