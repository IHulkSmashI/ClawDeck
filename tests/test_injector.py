"""The per-game injector: game scan, recommendation, OptiScaler install the way its setup_linux.sh does (plus
OptiPatcher / REFramework / per-game fixes like OptiClick), uninstall, launch overrides, compatibility list and
OptiPatcher parsing, and the always-latest updater."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from clawdeck import compat, gamescan, injector, launchenv, updates  # noqa: E402


def _w(path, value="x"):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"{value}\n")


INI = ("[Upscalers]\n; Select Upscaler for Dx12 games\nDx11Upscaler=auto\nDx12Upscaler=auto\nVulkanUpscaler=auto\n\n"
       "[FrameGen]\nEnabled=auto\nFGInput=auto\nFGOutput=auto\n\n[XeFG]\nInterpolationCount=auto\n\n"
       "[XeSS]\nBuildPipelines=auto \n\n[Spoofing]\nDxgi=auto\n\n[Plugins]\nPath=auto\nLoadAsiPlugins=auto\n\n"
       "[Hotfix]\nRestoreComputeSignature=auto\n")


@pytest.fixture
def release(tmp_path):
    """The shape of the OptiScaler 0.9.4 release, unpacked by updates.py into assets/optiscaler."""
    a = tmp_path / "assets"
    o = a / "optiscaler"
    for f in ("OptiScaler.dll", "libxess.dll", "libxess_fg.dll", "libxell.dll", "fakenvapi.dll", "amd_fidelityfx_dx12.dll",
              "setup_linux.sh", "setup_windows.bat", "D3D12_Optiscaler/D3D12Core.dll", "Licenses/XeSS_LICENSE.txt"):
        _w(o / f, f)
    _w(o / "OptiScaler.ini", INI)
    _w(o / "VERSION", "v0.9.4")
    _w(a / "optipatcher" / "OptiPatcher.asi", "op")
    _w(a / "reframework" / "dinput8.dll", "ref")
    return a


def _ue_game(root, extra=()):
    g = root / "Game"
    _w(g / "Expedition33.exe", "x" * 100)
    _w(g / "Engine" / "Binaries" / "ThirdParty" / "x.dll")
    _w(g / "Sandfall" / "Binaries" / "Win64" / "SandFall-Win64-Shipping.exe", "x" * 5000)
    for f in extra:
        _w(g / f)
    return g


def _read_ini(path):
    return open(path).read()


# ---- scan + plan ---------------------------------------------------------------------------------------------------
def test_scan_unreal_dlss_and_anti_cheat(tmp_path):
    g = _ue_game(tmp_path, ["Engine/Plugins/DLSS/Binaries/ThirdParty/Win64/nvngx_dlss.dll", "EasyAntiCheat/x.sys"])
    exe_dir = injector.find_game_exe_dir(str(g))
    assert exe_dir.endswith(os.path.join("Sandfall", "Binaries", "Win64"))
    s = gamescan.scan(str(g), exe_dir)
    assert s["engine"] == "unreal" and s["upscalers"]["dlss"] and s["exes"] == ["SandFall-Win64-Shipping.exe"]
    assert s["anti_cheat"] == ["Easy Anti-Cheat"]
    p = injector.plan(s, {}, True)
    assert p["mode"] == "none" and p["blocked"]


def test_plan_dlss_only_spoofs_unless_optipatcher(tmp_path):
    g = _ue_game(tmp_path, ["Engine/Plugins/DLSS/nvngx_dlss.dll"])
    s = gamescan.scan(str(g), injector.find_game_exe_dir(str(g)))
    p = injector.plan(s, {}, True)
    assert p["mode"] == "optiscaler" and p["options"]["spoof"] and p["options"]["multiplier"] == 3
    p2 = injector.plan(s, {"optipatcher": True}, False)
    assert p2["options"]["optipatcher"] and not p2["options"]["spoof"] and p2["options"]["multiplier"] == 2


def test_plan_fsr3_with_frame_gen_uses_fsr_input(tmp_path):
    g = tmp_path / "G"
    _w(g / "game.exe", "x" * 10)
    _w(g / "amd_fidelityfx_dx12.dll")
    _w(g / "amd_fidelityfx_framegeneration_dx12.dll")
    s = gamescan.scan(str(g), str(g))
    p = injector.plan(s, {}, True)
    assert p["mode"] == "optiscaler" and p["options"]["fg_input"] == "fsrfg" and not p["options"]["spoof"]
    assert any("Borderless" in w for w in p["warnings"])


def test_plan_native_xess_needs_nothing(tmp_path):
    g = tmp_path / "G"
    _w(g / "game.exe", "x")
    _w(g / "libxess.dll")
    p = injector.plan(gamescan.scan(str(g), str(g)), {}, True)
    assert p["mode"] == "native" and "XMX" in " ".join(p["reasons"]) and p["alternative"] == "optiscaler"


def test_plan_no_upscaler_falls_back_to_lsfg_and_says_not_xmx(tmp_path):
    g = tmp_path / "G"
    _w(g / "game.exe", "x")
    p = injector.plan(gamescan.scan(str(g), str(g)), {}, True)
    assert p["mode"] == "lsfg" and "not the XMX cores" in " ".join(p["reasons"])
    assert "Lossless Scaling" in p["warnings"][0]


def test_plan_broken_on_compat_list_goes_to_lsfg(tmp_path):
    g = tmp_path / "G"
    _w(g / "game.exe", "x")
    _w(g / "nvngx_dlss.dll")
    p = injector.plan(gamescan.scan(str(g), str(g)), {"entry": {"status": "broken", "notes": "", "inputs": []}}, True)
    assert p["mode"] == "lsfg"


def test_plan_re_engine_adds_reframework_and_compat_fix(tmp_path):
    g = tmp_path / "MHWilds"
    _w(g / "MonsterHunterWilds.exe", "x" * 10)
    _w(g / "re_chunk_000.pak")
    _w(g / "nvngx_dlss.dll")
    _w(g / "amd_fidelityfx_dx12.dll")
    s = gamescan.scan(str(g), str(g))
    assert s["engine"] == "re"
    c = {"entry": {"status": "works", "inputs": ["DLSS", "FSR3.1/4", "XeSS"], "notes": "Requires REFramework, set Dxgi=false"},
         "ini_fixes": {"Spoofing": {"Dxgi": "false"}}, "needs_reframework": True}
    p = injector.plan(s, c, True)
    assert p["options"]["reframework"] and not p["options"]["spoof"]
    assert any("REFramework" in x for x in p["steps"]) and any("Dxgi=false" in w for w in p["warnings"])


def test_plan_vulkan_game_has_no_xefg(tmp_path):
    g = tmp_path / "G"
    _w(g / "game.exe", "x")
    _w(g / "amd_fidelityfx_vk.dll")
    p = injector.plan(gamescan.scan(str(g), str(g)), {}, True)
    assert p["mode"] == "optiscaler" and p["options"]["fg"] is False
    assert "FrameGen" not in injector.ini_for(p["options"])


# ---- install / update / uninstall ----------------------------------------------------------------------------------
def test_install_like_setup_linux_sh_then_uninstall(tmp_path, release):
    g = _ue_game(tmp_path, ["Engine/Plugins/DLSS/nvngx_dlss.dll"])
    exe = g / "Sandfall" / "Binaries" / "Win64"
    _w(exe / "dxgi.dll", "reshade")                               # a mod's own dxgi.dll must survive
    s = gamescan.scan(str(g), str(exe))
    opts = injector.plan(s, {"optipatcher": True}, True)["options"]
    inj = injector.Injector(str(release))
    info = inj.install_optiscaler(str(g), opts, {"optiscaler": "v0.9.4", "optipatcher": "r1"})
    assert info["proxy"] == "winmm.dll" and open(exe / "winmm.dll").read().strip() == "OptiScaler.dll"
    assert open(exe / "dxgi.dll").read().strip() == "reshade"
    assert (exe / "D3D12_Optiscaler" / "D3D12Core.dll").exists() and (exe / "libxess_fg.dll").exists()
    assert not (exe / "setup_linux.sh").exists() and not (exe / "VERSION").exists()
    assert (exe / "OptiScaler" / "plugins" / "OptiPatcher.asi").exists()
    ini = _read_ini(exe / "OptiScaler.ini")
    for kv in ("Dx12Upscaler=xess", "Dx11Upscaler=xess_12", "FGOutput=xefg", "FGInput=upscaler", "InterpolationCount=2",
               "Dxgi=false", "LoadAsiPlugins=true", "BuildPipelines=true"):
        assert ini.count(kv.split("=")[0] + "=") == 1 and kv in ini, kv
    assert "; Select Upscaler for Dx12 games" in ini
    st = inj.state(str(g))
    assert st["mode"] == "optiscaler" and st["version"] == "v0.9.4" and not st["foreign_optiscaler"]
    # a second scan sees the game as shipped, not ClawDeck's files
    assert not gamescan.scan(str(g), str(exe))["upscalers"]["xess"]
    inj.uninstall(str(g))
    left = sorted(os.listdir(exe))
    assert left == ["SandFall-Win64-Shipping.exe", "dxgi.dll"] and open(exe / "dxgi.dll").read().strip() == "reshade"


def test_update_reinstalls_with_same_options(tmp_path, release):
    g = tmp_path / "G"
    _w(g / "game.exe", "x")
    _w(g / "nvngx_dlss.dll")
    _w(g / "amd_fidelityfx_dx12.dll", "game's own fsr")
    inj = injector.Injector(str(release))
    opts = injector.plan(gamescan.scan(str(g), str(g)), {}, False)["options"]
    inj.install_optiscaler(str(g), opts, {"optiscaler": "v0.9.3"})
    _w(release / "optiscaler" / "OptiScaler.dll", "new build")
    info = inj.install_optiscaler(str(g), inj.marker(str(g))["options"], {"optiscaler": "v0.9.4"})
    assert info["version"] == "v0.9.4" and open(g / "dxgi.dll").read().strip() == "new build"
    assert open(g / ("amd_fidelityfx_dx12.dll" + injector.BACKUP_SUFFIX)).read().strip() == "game's own fsr"
    inj.uninstall(str(g))
    assert open(g / "amd_fidelityfx_dx12.dll").read().strip() == "game's own fsr"


def test_reframework_goes_in_as_dinput8(tmp_path, release):
    g = tmp_path / "DD2"
    _w(g / "DD2.exe", "x")
    _w(g / "re_chunk_000.pak")
    _w(g / "nvngx_dlss.dll")
    inj = injector.Injector(str(release))
    opts = injector.plan(gamescan.scan(str(g), str(g)), {}, True)["options"]
    info = inj.install_optiscaler(str(g), opts, {"optiscaler": "v0.9.4", "reframework": "nightly-1"})
    assert open(g / "dinput8.dll").read().strip() == "ref" and info["reframework_version"] == "nightly-1"
    env = launchenv.compose({"inject": "optiscaler", "proxy": info["proxy"], "reframework": True})
    assert env["WINEDLLOVERRIDES"] == "dxgi=n,b;dinput8=n,b"


def test_foreign_optiscaler_is_left_alone(tmp_path, release):
    g = tmp_path / "G"
    _w(g / "game.exe", "x")
    _w(g / "OptiScaler.ini", "someone else's")
    inj = injector.Injector(str(release))
    assert inj.state(str(g))["foreign_optiscaler"]
    with pytest.raises(FileExistsError):
        inj.install_optiscaler(str(g), {})


def test_choose_proxy_skips_taken_names(tmp_path):
    for n in ("dxgi.dll", "winmm.dll"):
        _w(tmp_path / n)
    assert injector.choose_proxy(str(tmp_path)) == "version.dll"


def test_lsfg_layer_install(tmp_path):
    staged = tmp_path / "lsfg"
    _w(staged / "lib" / "liblsfg-vk-layer.so")
    _w(staged / "share" / "vulkan" / "implicit_layer.d" / "VkLayer_LSFGVK_frame_generation.json", "{}")
    home = tmp_path / "home"
    injector.install_lsfg_layer(str(staged), str(home))
    st = injector.lsfg_state(str(home))
    assert st["layer"] and st["v2"] and not st["ready"]          # no Lossless Scaling yet
    _w(home / ".local/share/Steam/steamapps/common/Lossless Scaling/Lossless.dll")
    assert injector.lsfg_state(str(home))["ready"]


# ---- compatibility list / OptiPatcher ------------------------------------------------------------------------------
WIKI = """| Game | Compatibility | Upscaler <br>Inputs | OptiPatcher <br>Support | Notes | Images |
| ---- | :-----------: | :-----------------: | :---: | ----- | :----: |
| [Dragon’s Dogma II](Dragons-Dogma-2) | ✅ | DLSS, FSR3 |  | Check Wiki entry for more info | [1](https://x) |
| [Monster Hunter Wilds](Monster-Hunter-Wilds) | ✅ | DLSS, FSR3.1/4, XeSS |  | Requires REFramework, set Dxgi=false in the OptiScaler.ini to avoid crashes. | [1](https://x) |
| [Clair Obscur: Expedition 33](Clair-Obscur-Expedition-33) | ✅ | DLSS, XeSS | ✨ |  |  |
| Some Online Shooter | ❌ | DLSS |  | Anti-cheat | |
"""


def test_compat_list_parse_lookup_fixes():
    rows = compat.parse_wiki(WIKI)
    assert len(rows) == 4 and rows[3]["status"] == "broken"
    dd2 = compat.lookup(rows, "Dragon's Dogma 2")
    assert dd2["name"] == "Dragon’s Dogma II" and dd2["page"].endswith("/wiki/Dragons-Dogma-2")
    mh = compat.lookup(rows, "Monster Hunter Wilds")
    assert compat.ini_fixes(mh["notes"]) == {"Spoofing": {"Dxgi": "false"}}
    assert compat.lookup(rows, "Clair Obscur: Expedition 33")["optipatcher"]
    assert compat.lookup(rows, "Something Else Entirely") is None


def test_optipatcher_list_like_setup_windows_bat():
    src = '#define CHECK_UE(name) exeName == (#name "-win64-shipping.exe")\nif (CHECK_UE(sandfall) || CHECK_UE(gwt2))\nelse if (exeName == "bloom&rage.exe")'
    names = compat.parse_optipatcher(src)
    assert {"sandfall-win64-shipping.exe", "sandfall-wingdk-shipping.exe", "bloom&rage.exe"} <= names
    assert "name-win64-shipping.exe" not in names


def test_compat_for_game_uses_cache_offline(tmp_path, monkeypatch):
    (tmp_path / "optiscaler-compat.md").write_text(WIKI)
    (tmp_path / "optipatcher-dllmain.cpp").write_text("if (CHECK_UE(sandfall))")

    def offline(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(compat.urllib.request, "urlopen", offline)
    c = compat.Compat(str(tmp_path))
    r = c.for_game("Clair Obscur: Expedition 33", ["SandFall-Win64-Shipping.exe"])
    assert r["listed"] and r["optipatcher"] and r["optipatcher_exe"] == "SandFall-Win64-Shipping.exe"


# ---- updates -------------------------------------------------------------------------------------------------------
def test_lsfg_latest_parse():
    page = '<li><a href="./lsfg-vk-2.1.3.x86_64.tar.xz">lsfg-vk 2.1.3</a> Latest release (2026-10-01)</li>'
    v, url = updates.lsfg_latest(page, "https://builds.lsfg-vk.dev/")
    assert v == "lsfg-vk-2.1.3.x86_64" and url == "https://builds.lsfg-vk.dev/lsfg-vk-2.1.3.x86_64.tar.xz"


def test_updates_download_newer_and_cache_the_check(tmp_path, monkeypatch):
    up = updates.Updates(str(tmp_path / "assets"), str(tmp_path / "updates.json"))
    calls = {"latest": 0}

    def latest(comp):
        calls["latest"] += 1
        return {"version": "v0.9.5", "url": "https://x/Optiscaler_0.9.5.7z", "name": "Optiscaler_0.9.5.7z"}
    monkeypatch.setattr(up, "latest", latest)

    def fake_download(url, dest, timeout=60):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "w").write("7z")
        return dest

    def fake_extract(archive, dest):
        _w(os.path.join(dest, "pkg", "OptiScaler.dll"))
        _w(os.path.join(dest, "pkg", "OptiScaler.ini"), "[x]")
        return dest
    monkeypatch.setattr(updates.downloads, "download", fake_download)
    monkeypatch.setattr(updates.downloads, "extract", fake_extract)
    st = up.check()
    assert st["optiscaler"]["update"] and st["optiscaler"]["installed"] is None
    r = up.ensure("optiscaler")
    assert r["updated"] and up.installed("optiscaler") == "v0.9.5"
    assert os.path.exists(os.path.join(up.dir("optiscaler"), "OptiScaler.dll"))
    n = calls["latest"]
    assert not up.ensure("optiscaler")["updated"]                     # nothing newer: no download
    up.check()
    assert calls["latest"] == n                                       # asked at most once a day
