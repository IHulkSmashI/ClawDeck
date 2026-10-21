"""Tests for the modules added after 0.1's core: HHD schema walking, controller, fans, display, launch environment,
injector lsfg mode, sleep parsing, xess state, Proton listing, benchmark summary, downloads safety, power revert of
HHD TDP, autotune pause, and the UI <-> backend wiring rule (every callable in src/api.ts exists in main.py)."""
import json
import os
import re
import sys
import zipfile

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

from clawdeck import (autotune, benchmark, controller, display, downloads, fans, hhd, hw, injector,  # noqa: E402
                      launchenv, power, profiles, proton, sleep, sysfs, xess)


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


# ---- controller ------------------------------------------------------------------------------------------------------
def test_controller_lists_claw_and_rgb_and_sets(monkeypatch):
    fake = FakeHHD(monkeypatch, {"controllers.claw.swap_guide": False, "rgb.handheld.mode.mode": "solid"})
    c = controller.Controller()
    st = c.settings()
    keys = {s["key"]: s for s in st["settings"]}
    assert st["available"]
    assert keys["controllers.claw.controller_mode.dualsense.gyro"]["group"] == "gyro"
    assert keys["rgb.handheld.mode.mode"]["group"] == "rgb" and keys["rgb.handheld.mode.mode"]["value"] == "solid"
    assert keys["controllers.claw.main_chords"]["advanced"]
    assert "controllers.claw.hidden" not in keys and not any(k.startswith("tdp.") for k in keys)
    assert c.set("controllers.claw.swap_guide", True)["ok"]
    assert fake.posts[-1] == {"controllers.claw.swap_guide": True}
    with pytest.raises(ValueError):
        c.set("tdp.msi.tdp.custom.tdp", 50)       # not a controller key: refused


# ---- fans ------------------------------------------------------------------------------------------------------------
def test_fan_curve_interpolation_and_presets(monkeypatch):
    assert fans.curve_at(fans.PRESETS["quiet"], 45) == 12
    assert fans.curve_at(fans.PRESETS["cool"], 100) == 100
    pts = fans.point_keys(SCHEMA)
    assert [p["temp"] for p in pts] == [40, 50, 90]
    vals = fans.preset_values("balanced", pts)
    assert vals == {"tdp.msi.fan.mode": "manual", "tdp.msi.fan.manual.st0": 0, "tdp.msi.fan.manual.st50": 40,
                    "tdp.msi.fan.manual.st88": 76}           # 75 rounded to HHD's step of 2
    assert fans.preset_values("auto", pts) == {"tdp.msi.fan.mode": "disabled"}
    fake = FakeHHD(monkeypatch, {"tdp.msi.fan.mode": "disabled"})
    f = fans.Fans(_hardware())
    assert f.status()["preset"] == "auto"
    assert f.apply_preset("cool")["ok"]
    assert f.status()["preset"] == "cool"
    assert fake.state["tdp.msi.fan.manual.st0"] == 30
    with pytest.raises(ValueError):
        f.apply_preset("turbo")


# ---- display ---------------------------------------------------------------------------------------------------------
def test_display_save_read_reset(tmp_path):
    d = display.Display(_hardware(), str(tmp_path))
    assert not d.read()["saved"]
    r = d.save({"vrr": True, "vrr_min": 10, "vrr_max": 200, "hdr": True, "hdr_nits": 5000})
    assert r["ok"]
    txt = open(d.conf).read()
    assert "ADAPTIVE_SYNC=1" in txt and "ENABLE_GAMESCOPE_HDR=1" in txt and "GAMESCOPE_HDR_NITS=1000" in txt
    assert "CUSTOM_REFRESH_RATES=30,31," in txt and txt.strip().split("\n")[3].endswith(",120")   # clamped to the EX's VRR range
    s = d.read()
    assert s["saved"] and s["settings"]["vrr"] and s["settings"]["vrr_min"] == 30 and s["settings"]["hdr_nits"] == 1000
    assert d.reset()["ok"] and not os.path.exists(d.conf)


def test_display_dry_run_writes_nothing(tmp_path):
    d = display.Display(_hardware(), str(tmp_path))
    assert d.save({"vrr": True}, dry_run=True)["dry_run"]
    assert not os.path.exists(d.conf)


def test_display_reports_competing_overrides(tmp_path):
    _w(tmp_path / ".config/environment.d/10-other.conf", "ADAPTIVE_SYNC=1")
    assert display.Display(_hardware(), str(tmp_path)).read()["other_overrides"]


# ---- launch environment / injector -----------------------------------------------------------------------------------
def test_launch_env_compose_and_write(tmp_path):
    env = launchenv.compose({"inject": "lsfg", "lsfg": {"multiplier": 9, "flow_scale": 0.1}, "hdr": True, "mesa": "26.1"})
    assert env == {"LSFGVK_ENV": "1", "LSFGVK_MULTIPLIER": "4", "LSFGVK_FLOW_SCALE": "0.25",
                   "LSFGVK_PERFORMANCE_MODE": "1", "DXVK_HDR": "1", "MESA": "26.1"}
    r = launchenv.write(str(tmp_path), "1234", {"inject": "optiscaler"})
    assert open(r["path"]).read().splitlines()[1] == "WINEDLLOVERRIDES=dxgi=n,b"
    assert launchenv.write(str(tmp_path), "1234", {"inject": "none"})["path"] is None
    assert not os.path.exists(os.path.join(tmp_path, "launch", "1234.env"))
    with pytest.raises(ValueError):
        launchenv.write(str(tmp_path), "../x", {"hdr": True})
    with pytest.raises(ValueError):
        launchenv.compose({"mesa": "1; rm -rf"})


def test_launch_wrapper_reads_env_file(tmp_path):
    import subprocess
    (tmp_path / "launch").mkdir()
    (tmp_path / "launch" / "42.env").write_text("# c\nWINEDLLOVERRIDES=dxgi=n,b\nDXVK_HDR=1\n")
    out = subprocess.run(["bash", os.path.join(ROOT, "tools", "clawdeck-launch.sh"), "env"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLAWDECK_RUNTIME": str(tmp_path),
                              "SteamAppId": "42", "WINEDLLOVERRIDES": "foo=n", "CLAWDECK_NO_MANGOHUD": "1"}).stdout
    assert "WINEDLLOVERRIDES=dxgi=n,b;foo=n" in out and "DXVK_HDR=1" in out


def test_profiles_launch_slot_and_export(tmp_path):
    p = profiles.Profiles(str(tmp_path / "p.json"))
    p.set_launch("7", {"inject": "lsfg", "hdr": True})
    p.set_launch("7", {"hdr": None})
    assert p.get_launch("7") == {"inject": "lsfg"}
    q = profiles.Profiles(str(tmp_path / "q.json"))
    q.import_json(p.export_json("7"))
    assert q.get_launch("7") == {"inject": "lsfg"} and q.games() == ["7"]


def test_lsfg_state(tmp_path):
    assert not injector.lsfg_state(str(tmp_path))["ready"]
    _w(tmp_path / ".local/share/vulkan/implicit_layer.d/VkLayer_LSFGVK_frame_generation.json", "{}")
    _w(tmp_path / ".local/share/Steam/steamapps/common/Lossless Scaling/Lossless.dll", "x")
    st = injector.lsfg_state(str(tmp_path))
    assert st["ready"] and st["lossless_dll"].endswith("Lossless.dll")


def test_injector_optiscaler_install_and_undo(tmp_path):
    assets = tmp_path / "assets"
    _w(assets / "optiscaler/OptiScaler.dll", "opti")
    # the shape of OptiScaler 0.9.4's own INI: comments, keys set to auto
    _w(assets / "optiscaler/OptiScaler.ini", "[Upscalers]\n; Select Upscaler for Dx12 games\nDx11Upscaler=auto\n"
       "Dx12Upscaler=auto\nVulkanUpscaler=auto\n\n[FrameGen]\nEnabled=auto\nFGInput=auto\nFGOutput=auto\n\n"
       "[XeSS]\nBuildPipelines=auto \n")
    game = tmp_path / "game"
    _w(game / "bin/Game.exe", "x" * 1000)
    _w(game / "bin/dxgi.dll", "original")
    inj = injector.Injector(str(assets))
    r = inj.install_optiscaler(str(game))
    assert open(game / "bin/dxgi.dll").read().strip() == "opti"
    ini = open(game / "bin/OptiScaler.ini").read()
    for kv in ("Dx12Upscaler=xess", "Dx11Upscaler=xess_12", "FGInput=upscaler", "FGOutput=xefg", "BuildPipelines=true"):
        assert ini.count(kv.split("=")[0] + "=") == 1 and kv in ini, kv
    assert "; Select Upscaler for Dx12 games" in ini
    assert inj.state(str(game))["mode"] == "optiscaler" and r["backed_up"]
    inj.uninstall(str(game))
    assert open(game / "bin/dxgi.dll").read().strip() == "original"
    assert inj.state(str(game))["mode"] == "none"


def test_steam_install_dir(tmp_path):
    steam = tmp_path / ".local/share/Steam"
    _w(steam / "steamapps/libraryfolders.vdf", f'"libraryfolders"\n{{\n "1"\n {{\n  "path"  "{tmp_path}/sd"\n }}\n}}')
    _w(tmp_path / "sd/steamapps/appmanifest_2054970.acf", '"AppState"\n{\n\t"appid"\t\t"2054970"\n\t"installdir"\t\t"Dragons Dogma 2"\n}')
    (tmp_path / "sd/steamapps/common/Dragons Dogma 2").mkdir(parents=True)
    assert injector.steam_install_dir(str(tmp_path), "2054970") == f"{tmp_path}/sd/steamapps/common/Dragons Dogma 2"
    assert injector.steam_install_dir(str(tmp_path), "1") is None
    assert injector.steam_install_dir(str(tmp_path), "../x") is None


def test_injector_stage(tmp_path):
    _w(tmp_path / "dl/OptiScaler_0.9/OptiScaler.dll", "o")
    _w(tmp_path / "dl/OptiScaler_0.9/OptiScaler.ini", "i")
    _w(tmp_path / "sdk/bin/libxess.dll", "x")
    _w(tmp_path / "sdk/bin/readme.txt", "r")
    inj = injector.Injector(str(tmp_path / "assets"))
    assert inj.stage("optiscaler", str(tmp_path / "dl"))["copied"] == ["OptiScaler.dll", "OptiScaler.ini"]
    assert inj.stage("xess", str(tmp_path / "sdk"))["copied"] == ["libxess.dll"]
    assert inj.assets_ready() == {"optiscaler": True, "xess": True}


# ---- sleep -----------------------------------------------------------------------------------------------------------
def test_sleep_parsers():
    assert sleep.selected_mem_sleep("[s2idle] deep") == "s2idle"
    assert sleep.selected_mem_sleep(None) is None
    w = sleep.wake_sources("Device\tS-state\t  Status   Sysfs node\nXHCI\t  S3\t*enabled   pci:0000:00:14.0\nLID0\t  S4\t*disabled\n")
    assert w[0] == {"device": "XHCI", "state": "enabled", "sysfs": "pci:0000:00:14.0"} and w[1]["state"] == "disabled"
    assert {c["id"] for c in sleep.checks()} == {"sleep_mode", "sleep_failures", "s0ix"}


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


# ---- proton ----------------------------------------------------------------------------------------------------------
def test_proton_listing(tmp_path):
    ct = tmp_path / ".local/share/Steam/compatibilitytools.d"
    for v in ("GE-Proton10-3", "GE-Proton11-6"):
        _w(ct / v / "compatibilitytool.vdf", f'"compatibilitytools"\n{{\n "compat_tools"\n {{\n  "{v}"\n  {{\n   "display_name" "{v}"\n  }}\n }}\n}}')
    _w(tmp_path / ".local/share/Steam/steamapps/common/Proton 10.0/proton", "")
    _w(tmp_path / ".local/share/Steam/steamapps/common/Proton - Experimental/proton", "")
    tools = proton.installed(str(tmp_path))
    assert [t["name"] for t in tools] == ["GE-Proton11-6", "GE-Proton10-3", "proton_experimental", "proton_10"]


# ---- benchmark -------------------------------------------------------------------------------------------------------
def test_benchmark_summary_and_storage(tmp_path):
    s = benchmark.summarise([60] * 99 + [30], [20.0] * 100)
    assert s == {"samples": 100, "avg_fps": 59.7, "low1_fps": 30.0, "avg_w": 20.0, "fps_per_w": 2.99}
    assert benchmark.summarise([], [])["samples"] == 0
    b = benchmark.Benchmark(_hardware(), str(tmp_path / "mh"), str(tmp_path / "b.json"))
    b.run = {"appid": "1", "name": "G", "label": "battery", "settings": {"tdp": 20}, "started": 0, "duration_s": 60,
             "fps": [50, 60], "w": [18, 22]}
    r = b.stop()
    assert r["avg_fps"] == 55.0 and b.results()[0]["label"] == "battery"
    assert b.export_csv().splitlines()[0].startswith("time,model,name")


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
    # real asset names seen 2026-10-21 match the globs in versions.json
    v = downloads.load_versions(os.path.join(ROOT, "assets", "versions.json"))
    for key, name in (("optiscaler", "Optiscaler_0.9.4-final.20260718._MM.7z"), ("xess_sdk", "XeSS_SDK_3.0.2.zip"),
                      ("xess_xmx_linux", "xess-xmx-linux-20261018.zip")):
        assert downloads.pick_asset({"assets": [{"name": "v1.zip"}, {"name": name}]}, v[key]["asset_glob"])["name"] == name


def test_versions_file_is_valid():
    v = downloads.load_versions(os.path.join(ROOT, "assets", "versions.json"))
    for k in ("optiscaler", "xess_sdk", "xess_xmx_linux"):
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
    names = set(re.findall(r'callable<[^>]*>\("([a-z_]+)"\)', api))
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
