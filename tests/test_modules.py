"""Tests: HHD schema walking, the launch environment, xess state and install, downloads safety, power revert of HHD
TDP, autotune pause, and the UI <-> backend wiring rule (every callable in src/api.ts exists in main.py)."""
import json
import os
import re
import sys
import zipfile

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

from clawdeck import (downloads, hhd, hw, health,  # noqa: E402
                      launchenv, power, profiles, sysfs, xess)


def _w(path, value):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"{value}\n")


class FakeHHD:
    def __init__(self, monkeypatch, state=None):
        self.state = dict(state or {})
        self.posts = []
        monkeypatch.setattr(hhd, "get_state", lambda flat=True: dict(self.state))
        monkeypatch.setattr(hhd, "available", lambda: True)

        def _set(values):
            self.posts.append(dict(values))
            self.state.update(values)
            return dict(self.state)
        monkeypatch.setattr(hhd, "set_values", _set)


@pytest.fixture(autouse=True)
def _clean_sysfs():
    sysfs._originals.clear()
    yield
    sysfs._originals.clear()


def _hardware(paths=None, product="MS-1T91"):
    return hw.Hardware(product=product, model=hw.MODELS[product], known=True, kernel="test", cpu_model="test",
                       gpu_driver="xe", paths=paths or hw.Paths())


# ---- launch environment -----------------------------------------------------------------------------------------------
def test_launch_env_compose_and_write(tmp_path):
    # per game (the default): the XMX driver only where it helps; Per-game's switch forces it on or off
    assert launchenv.compose({}) == {}            # a game nobody touched: automatic (XeSS / OptiScaler games only)
    assert launchenv.compose({"xmx": False}) == {"CLAWDECK_XMX": "0"}
    assert launchenv.compose({"xmx": True, "low_latency": True, "fg": 0, "fsr": "off", "opti": "off"}) == {"CLAWDECK_XMX": "1"}
    # the package still session-wide (before ClawDeck moved its file): off = the stock drivers for this game
    off = launchenv.compose({"xmx": False}, per_game=False)
    assert off["VKD3D_DISABLE_EXTENSIONS"] == "" and "VK_DRIVER_FILES" in off
    assert launchenv.compose({"xmx": True}, per_game=False) == {}
    assert launchenv.compose({"xell": True}) == {}  # a 0.1 setting (XeLL driver mode) no longer does anything
    # the package's own launch options (xess-xmx-linux docs/launch-options.md)
    assert launchenv.compose({"fg": 4, "xess_latest": True, "low_latency": False}) == {"CLAWDECK_XMX": "1", "XESS_FG": "4", "XESS": "latest"}   # XeLL always on
    assert launchenv.compose({"fsr": "xess"}) == {"CLAWDECK_XMX": "1", "FSR": "xess"}
    assert launchenv.compose({"fsr": "fg"}) == {"CLAWDECK_XMX": "1", "FSR_FG": "xess"}
    assert launchenv.compose({"fsr": "inject"}) == {"CLAWDECK_XMX": "1", "FSR": "xess", "FSR_FG": "inject"}
    # Decky Framegen's fgmod writes these into the game's OptiScaler.ini
    assert launchenv.compose({"opti": "xess", "opti_xefg": True}) == {"CLAWDECK_XMX": "1", "Dx12Upscaler": "xess", "FrameGen_Enabled": "true", "FrameGen_FGOutput": "xefg"}
    assert launchenv.compose({"opti": "fsr4"}) == launchenv.compose({"opti": "fsr4_xmx"})   # INT8 retired: on XMX
    for bad in ({"fg": 1}, {"fg": 9}, {"fg": True}, {"fg": "4"}, {"fsr": "amd"}, {"opti": "dlss"}):
        with pytest.raises(ValueError):
            launchenv.compose(bad)
    r = launchenv.write(str(tmp_path), "1234", {"fg": 3})
    assert open(r["path"]).read().splitlines()[1:] == ["CLAWDECK_XMX=1", "XESS_FG=3"]
    assert launchenv.write(str(tmp_path), "1234", {"fg": 0})["path"] is None
    assert not os.path.exists(os.path.join(tmp_path, "launch", "1234.env"))
    with pytest.raises(ValueError):
        launchenv.write(str(tmp_path), "../x", {"fg": 2})


def test_launch_wrapper_reads_env_file(tmp_path):
    import subprocess
    (tmp_path / "launch").mkdir()
    (tmp_path / "launch" / "42.env").write_text("# c\nWINEDLLOVERRIDES=dxgi=n,b\nIGDEXT_TRACE=1\n")
    out = subprocess.run(["bash", os.path.join(ROOT, "tools", "clawdeck-launch.sh"), "env"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLAWDECK_RUNTIME": str(tmp_path),
                              "SteamAppId": "42", "WINEDLLOVERRIDES": "foo=n", "CLAWDECK_NO_MANGOHUD": "1"}).stdout
    assert "WINEDLLOVERRIDES=dxgi=n,b;foo=n" in out and "IGDEXT_TRACE=1" in out   # the user's own overrides are kept


def test_profiles_launch_slot(tmp_path):
    p = profiles.Profiles(str(tmp_path / "p.json"))
    p.set_launch("7", {"xmx": False, "fg": 3})
    p.set_launch("7", {"fg": None})
    assert p.get_launch("7") == {"xmx": False}
    q = profiles.Profiles(str(tmp_path / "p.json"))
    assert q.get_launch("7") == {"xmx": False} and q.games() == ["7"]


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
def _xess_release(monkeypatch, tags, asked=None):
    """downloads.release answering {repo: tag}; a repo without one answers 404 (no release published)."""
    import urllib.error

    def rel(repo, tag):
        if asked is not None:
            asked.append(repo)
        if repo not in tags:
            raise urllib.error.HTTPError("https://api.github.com/x", 404, "Not Found", {}, None)
        return {"tag_name": tags[repo], "assets": []}
    monkeypatch.setattr(downloads, "release", rel)


def _xess_runs(monkeypatch, home, install_rc=0, check_line="ok 2026-10-22 12:00 /x/libvulkan_intel.so Mesa 26.1.2"):
    """userexec.run for install.sh and session-check.sh: the check writes driver-status as the real one does."""
    ran = []

    def run(cmd, h, timeout=600, cwd=None, env=None):
        ran.append(os.path.basename(cmd[1]) + (" " + " ".join(cmd[2:]) if cmd[2:] else ""))
        if cmd[0] == "systemctl":                 # clearing the session's variables (xmxscope.isolate_session)
            return {"rc": 0, "out": ""}
        if cmd[1].endswith("session-check.sh"):
            _w(os.path.join(home, xess.STATUS), check_line)
            return {"rc": 0 if check_line.startswith("ok") else 1, "out": check_line}
        if install_rc == 0:      # install.sh points the session at the package (environment.d)
            _w(os.path.join(home, xess.ENV_CONF), f"VK_DRIVER_FILES={cwd}/run/intel_icd.json")
        return {"rc": install_rc, "out": "== fetching\nmissing igdext64.dll - run this from the unpacked package\n" if install_rc else "done"}
    monkeypatch.setattr(xess.userexec, "run", run)
    return ran


def _xess_fetch(monkeypatch, home, fetched=None):
    def fetch(entry, workdir, allow_unpinned=False, rel=None):
        if not downloads.is_pinned(entry) and not allow_unpinned:     # as the real one: ask before an untested version
            raise PermissionError(f"{entry['repo']} has no tested version pinned yet; allow 'latest (untested)' to continue")
        if fetched is not None:
            fetched.append(entry["repo"])
        tag = rel["tag_name"]
        pkg = home / f".local/share/clawdeck/xess-xmx-linux-{tag}"
        _w(pkg / "install.sh", "echo hi"); _w(pkg / "uninstall.sh", ""); _w(pkg / "tools/session-check.sh", "")
        return {"tag": tag, "archive": str(home / "a.zip"), "dir": str(pkg), "repo": entry["repo"]}
    monkeypatch.setattr(downloads, "fetch", fetch)


def test_xess_install_names_the_failing_step(tmp_path, monkeypatch):
    import urllib.error
    asked = []
    _xess_release(monkeypatch, {"HHHHanasak1/xess-xmx-linux": "20261104"}, asked)
    _xess_fetch(monkeypatch, tmp_path)
    _xess_runs(monkeypatch, str(tmp_path))
    entry = {"repo": "HHHHanasak1/xess-xmx-linux", "tag": "20261104"}
    r = xess.install(str(tmp_path), entry)
    assert r["ok"] and asked == ["HHHHanasak1/xess-xmx-linux"] and r["repo"] == "HHHHanasak1/xess-xmx-linux"
    assert xess.source_of(r["package_dir"]) == {"repo": "HHHHanasak1/xess-xmx-linux", "tag": "20261104"}
    # any failure names the step and the real reason, never a bare "file not found"
    monkeypatch.setattr(downloads, "fetch", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no asset matching x in y")))
    _w(tmp_path / xess.ENV_CONF, "")                                   # nothing installed: no up-to-date shortcut
    from clawdeck import xmxscope
    for f in (xmxscope.XMX_ENV, xmxscope.SESSION_CONF + xmxscope.PARKED_SUFFIX):   # nor ClawDeck's per-game copy
        (tmp_path / f).unlink(missing_ok=True)
    assert xess.install(str(tmp_path), entry)["error"] == "download: FileNotFoundError: no asset matching x in y"
    monkeypatch.setattr(downloads, "release", lambda *a: (_ for _ in ()).throw(urllib.error.HTTPError("https://u", 403, "rate limited", {}, None)))
    assert xess.install(str(tmp_path), entry)["error"] == "download: https://u answered 403 rate limited"


def test_xess_install_reports_the_script_failure(tmp_path, monkeypatch):
    _xess_release(monkeypatch, {"a/b": "1"})
    _xess_fetch(monkeypatch, tmp_path)
    ran = _xess_runs(monkeypatch, str(tmp_path), install_rc=1)
    r = xess.install(str(tmp_path), {"repo": "a/b", "tag": "1"})
    assert not r["ok"] and r["error"].startswith("install.sh exited 1: == fetching · missing igdext64.dll")
    assert ran == ["install.sh"]                                       # no driver check of a failed install


def test_xess_install_runs_the_driver_check_so_setup_is_right_without_a_relogin(tmp_path, monkeypatch):
    _xess_release(monkeypatch, {"a/b": "2"})
    _xess_fetch(monkeypatch, tmp_path)
    _w(tmp_path / xess.STATUS, "FAILED 2026-10-01 10:00 /old/libvulkan_intel.so: old driver")   # the last log-in's verdict
    ran = _xess_runs(monkeypatch, str(tmp_path))
    r = xess.install(str(tmp_path), {"repo": "a/b", "tag": "2"})
    assert r["ok"] and ran == ["install.sh", "session-check.sh --dry-run", "--user unset-environment VK_DRIVER_FILES"]
    assert r["check"]["ok"] and xess.state(str(tmp_path))["status"]["state"] == "ok"   # read straight after
    # a driver that fails its check: installed, and the toast says why (the log-in unit switches drivers)
    _xess_release(monkeypatch, {"a/b": "3"})
    _xess_runs(monkeypatch, str(tmp_path), check_line="FAILED 2026-10-22 12:00 /x: vulkaninfo found no device")
    r = xess.install(str(tmp_path), {"repo": "a/b", "tag": "3"})
    assert r["ok"] and not r["check"]["ok"] and "driver check failed" in r["next"] and "no device" in r["next"]


def test_xess_update_with_nothing_newer_says_up_to_date_and_reinstalls_nothing(tmp_path, monkeypatch):
    _xess_release(monkeypatch, {"a/b": "2"})
    fetched = []
    _xess_fetch(monkeypatch, tmp_path, fetched)
    ran = _xess_runs(monkeypatch, str(tmp_path))
    assert xess.install(str(tmp_path), {"repo": "a/b", "tag": "2"})["ok"] and fetched == ["a/b"]
    ran.clear()
    r = xess.install(str(tmp_path), {"repo": "a/b", "tag": "pending-verification"})   # Update; no confirm needed
    assert r["ok"] and r.get("up_to_date"), r
    assert r["summary"] == "XeSS XMX 2 is up to date" and "error" not in r
    assert fetched == ["a/b"] and ran == ["session-check.sh --dry-run"]   # no download, no install.sh, line refreshed
    _xess_release(monkeypatch, {"a/b": "3"})                             # a newer release: a real update again
    r = xess.install(str(tmp_path), {"repo": "a/b", "tag": "pending-verification"})
    assert not r["ok"] and "no tested version" in r["error"]            # the untested-version prompt, as before
    assert xess.install(str(tmp_path), {"repo": "a/b", "tag": "3"})["tag"] == "3" and fetched == ["a/b", "a/b"]


def test_xess_session_check_without_a_package_or_script(tmp_path, monkeypatch):
    assert not xess.session_check(str(tmp_path))["ran"]
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    r = xess.session_check(str(tmp_path), str(pkg))
    assert not r["ran"] and "session-check.sh" in r["error"]


def test_xess_state_and_game_low_latency(tmp_path):
    home = tmp_path
    pkg = home / ".local/share/clawdeck/xess-xmx-linux-20261104"
    _w(pkg / "lib/mesa-26.3.0-rc3/libvulkan_intel.so", "x"); _w(pkg / "lib/active", "26.3.0-rc3")
    _w(pkg / "docs/launch-options.md", "| `IGDEXT_XELL_CAP=off %command%` | turns the low latency off |")
    _w(pkg / xess.SOURCE_FILE, '{"repo": "HHHHanasak1/xess-xmx-linux", "tag": "20261104"}')
    _w(home / ".config/environment.d/50-xess-xmx.conf", f"VK_DRIVER_FILES={pkg}/run/intel_icd.json\n")
    st = xess.state(str(home))
    assert st["low_latency"] and st["version"] == "20261104" and st["drivers"] == ["26.3.0-rc3"]
    old = home / "old/xess-xmx-linux-20261018"
    _w(old / "docs/launch-options.md", "| `XESS_FG=3` |")
    assert not xess.has_low_latency(str(old), old.name)
    assert xess.has_low_latency(str(home / "none"), "xess-xmx-linux-20261110")   # no docs: by the date in the name
    pfx = home / xess.COMPATDATA / "42/pfx/drive_c"
    assert xess.game_latency(str(home), "42", False)["mode"] == "off"
    assert xess.game_latency(str(home), "42", True)["mode"] == "none"
    _w(pfx / "igdext_xell.log", "low latency: on\nauto: frame generation off (none), not pacing\n")
    assert xess.game_latency(str(home), "42", True)["mode"] == "no-fg"
    _w(pfx / "igdext_xell.log", "low latency: on\nauto: frame generation on (xefg), pacing\n"
       "auto: waited in 0 of 100 frames, 0.0 ms each (0.00 ms spun); last: 0.0 ms left, 2 in flight, frame 16.00 ms (uncapped 16.00), cpu 9.0 ms\n")
    assert xess.game_latency(str(home), "42", True)["mode"] == "idle"
    _w(pfx / "igdext_xell.log", "low latency: on\nauto: frame generation on (xefg), pacing\n"
       "auto: waited in 87 of 100 frames, 6.4 ms each (0.12 ms spun); last: 5.0 ms left, 3 in flight, frame 27.00 ms (uncapped 27.00), cpu 4.0 ms\n")
    lat = xess.game_latency(str(home), "42", True)
    assert lat["mode"] == "pacing" and "87 of the last 100" in lat["detail"] and "6.4 ms" in lat["detail"]
    _w(pfx / "igdext_xell.log", "low latency: off\n")
    assert xess.game_latency(str(home), "42", True)["mode"] == "off"


def test_framegen_detects_installs_and_never_overwrites(tmp_path, monkeypatch):
    from clawdeck import framegen
    home = tmp_path
    assert framegen.state(str(home)) == {"installed": False, "dir": None, "version": None, "fgmod_ready": False, "fgmod": None, "optiscaler": None}
    plugins = home / framegen.PLUGINS
    plugins.mkdir(parents=True)
    # install from the release zip: the folder carrying its plugin.json goes into Decky's plugins, Decky restarts
    def fetch(entry, workdir, allow_unpinned=False, rel=None):
        d = os.path.join(workdir, "unpacked")
        _w(os.path.join(d, "Decky Framegen", "plugin.json"), json.dumps({"name": "Decky-Framegen"}))
        _w(os.path.join(d, "Decky Framegen", "package.json"), json.dumps({"version": "0.17"}))
        return {"tag": "v0.17", "archive": os.path.join(workdir, "Decky Framegen.zip"), "dir": d, "repo": entry["repo"]}
    monkeypatch.setattr(downloads, "fetch", fetch)
    monkeypatch.setattr(framegen, "_restart_decky", lambda: True)
    r = framegen.install(str(home), {"repo": "xXJSONDeruloXx/Decky-Framegen", "tag": "v0.17", "asset_glob": "*.zip"})
    assert r["ok"], r
    assert r["restarting"] and "Setup OptiScaler Mod" in r["next"]
    st = framegen.state(str(home))
    assert st["installed"] and st["version"] == "0.17" and not st["fgmod_ready"]
    # an installed one is left alone
    assert framegen.install(str(home), {"repo": "x/y", "tag": "1"})["already"]
    # fgmod set up in Decky Framegen: ready, with the OptiScaler version from its manifest
    _w(home / "fgmod/fgmod", "#!/bin/bash\n"); os.chmod(home / "fgmod/fgmod", 0o755)
    _w(home / "fgmod/install-manifest.json", json.dumps({"optiscaler": {"version": "0.9.4-final.20260718"}}))
    st = framegen.state(str(home))
    assert st["fgmod_ready"] and st["optiscaler"] == "0.9.4-final.20260718"
    assert framegen.uses_fgmod("clawdeck-launch.sh ~/fgmod/fgmod %command%") and not framegen.uses_fgmod("%command%")
    # a folder of the same name that isn't Decky Framegen is never overwritten
    other = tmp_path / "other"
    (other / framegen.PLUGINS / "Decky-Framegen").mkdir(parents=True)
    assert "not overwritten" in framegen.install(str(other), {"repo": "x/y", "tag": "1"})["error"]


def test_health_summary_one_line():
    checks = [{"id": "a", "title": "A", "ok": True}, {"id": "b", "title": "XeSS XMX driver", "ok": False},
              {"id": "c", "title": "optional thing", "ok": False, "optional": True}]
    s = health.summary(checks)
    assert s == {"ok": False, "total": 3, "problems": 1, "line": "1 to fix: XeSS XMX driver"}
    assert health.summary([checks[0]])["line"] == "All checks pass"


def test_launch_wrapper_rotates_xell_logs_for_one_launch(tmp_path):
    import subprocess
    (tmp_path / "launch").mkdir()
    (tmp_path / "launch" / "7.env").write_text("XESS_FG=3\n")
    pfx = tmp_path / "compat/pfx/drive_c"
    _w(pfx / "igdext_xell.log", "old")
    subprocess.run(["bash", os.path.join(ROOT, "tools", "clawdeck-launch.sh"), "true"], check=True,
                   env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLAWDECK_RUNTIME": str(tmp_path), "SteamAppId": "7",
                        "STEAM_COMPAT_DATA_PATH": str(tmp_path / "compat"), "CLAWDECK_NO_MANGOHUD": "1"})
    assert not (pfx / "igdext_xell.log").exists() and (pfx / "igdext_xell.log.prev").read_text().strip() == "old"


def test_optiscaler_from_decky_framegen_is_seen_read_only(tmp_path):
    home = tmp_path
    steam = home / xess.STEAM
    _w(steam / "steamapps/libraryfolders.vdf", f'"libraryfolders"\n{{\n "1"\n {{\n  "path"  "{tmp_path}/sd"\n }}\n}}')
    _w(home / "sd/steamapps/appmanifest_2054970.acf", '"AppState"\n{\n\t"appid"\t\t"2054970"\n\t"installdir"\t\t"Dragons Dogma 2"\n}')
    game = home / "sd/steamapps/common/Dragons Dogma 2"
    game.mkdir(parents=True)
    assert xess.optiscaler_in(str(home), "2054970") == {"present": False, "dir": str(game)}
    _w(game / "OptiScaler.ini", "[FrameGen]\nFGOutput=xefg\n")
    _w(game / "OptiScaler.log", "OptiScaler v0.9.4-final loaded")
    r = xess.optiscaler_in(str(home), "2054970")
    assert r["present"] and r["version"] == "v0.9.4-final" and r["xess_fg"]
    assert xess.optiscaler_in(str(home), "1")["present"] is False and xess.steam_install_dir(str(home), "../x") is None


def test_launch_wrapper_asks_the_shim_for_its_xell_log(tmp_path):
    import subprocess
    pfx = tmp_path / "compat/pfx/drive_c"
    _w(pfx / "igdext_xell.log", "last launch")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLAWDECK_RUNTIME": str(tmp_path), "SteamAppId": "9",
           "STEAM_COMPAT_DATA_PATH": str(tmp_path / "compat"), "CLAWDECK_NO_MANGOHUD": "1"}
    run = lambda: subprocess.run(["bash", os.path.join(ROOT, "tools", "clawdeck-launch.sh"), "env"], env=env,
                                 capture_output=True, text=True, check=True).stdout
    # always: autotune counts rendered frames from it, Per-game reads what the low latency did
    assert "IGDEXT_XELL_LOG=1" in run()
    assert not (pfx / "igdext_xell.log").exists() and (pfx / "igdext_xell.log.prev").exists()   # one launch per log


# ---- watts in the overlay (telemetry.py) ----------------------------------------------------------------------------
def test_overlay_power_opens_and_closes_only_the_package_counters(tmp_path):
    from clawdeck import telemetry
    for d in ("intel-rapl:0", "intel-rapl:0:0", "intel-rapl-mmio:0"):
        (tmp_path / d).mkdir()
        f = tmp_path / d / "energy_uj"
        f.write_text("123\n")
        f.chmod(0o400)
    st = telemetry.status(str(tmp_path))
    assert st["available"] and not st["on"] and len(st["files"]) == 2   # the core sub-domain isn't touched
    r = telemetry.apply(True, str(tmp_path))
    assert r["ok"] and r["on"]
    assert oct((tmp_path / "intel-rapl:0:0" / "energy_uj").stat().st_mode & 0o777) == "0o400"
    r = telemetry.apply(False, str(tmp_path))
    assert r["ok"] and not r["on"]
    assert oct((tmp_path / "intel-rapl-mmio:0" / "energy_uj").stat().st_mode & 0o777) == "0o400"


def test_overlay_power_without_counters_says_so(tmp_path):
    from clawdeck import telemetry
    r = telemetry.apply(True, str(tmp_path))
    assert not r["ok"] and "no RAPL" in r["error"]


def test_battery_watts_corrects_the_ex_100x_current(tmp_path):
    from clawdeck import telemetry
    b = tmp_path / "BAT1"
    b.mkdir()
    (b / "current_now").write_text("-64580000\n")     # what the Claw 8 EX reports at about 10 W
    (b / "voltage_now").write_text("15400000\n")
    (b / "status").write_text("Discharging\n")
    assert telemetry.battery_watts(str(b)) == -9.9
    (b / "current_now").write_text("645800\n")        # a correctly scaled battery stays as it is
    (b / "status").write_text("Charging\n")
    assert telemetry.battery_watts(str(b)) == 9.9


# ---- XeSS on XMX per game (xmxscope.py) -----------------------------------------------------------------------------
def _scope_home(tmp_path):
    from clawdeck import xmxscope
    _w(tmp_path / xmxscope.SESSION_CONF, "VK_DRIVER_FILES=/p/run/intel_icd.json:/usr/share/vulkan/icd.d/intel_icd.x86_64.json\n"
       "VKD3D_DISABLE_EXTENSIONS=VK_EXT_descriptor_buffer,VK_KHR_present_wait2\nINTC_ALT_DRIVER_EXTENSIONS_PATH='C:\\\\x\\\\igd_faux.inf_1'\n")
    proton = tmp_path / ".local/share/Steam/steamapps/common/Proton 11.0"
    _w(proton / "proton", "")
    _w(proton / "user_settings.py", "# xess-xmx proton hook - written by install.sh\n")
    return xmxscope, proton


def _run_scope(script, env, monkeypatch):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    g = {"__file__": str(script)}
    exec(compile(script.read_text(), str(script), "exec"), g)
    return g["user_settings"]


def test_xmx_scope_moves_the_session_file_and_keeps_its_variables(tmp_path):
    xmxscope, proton = _scope_home(tmp_path)
    r = xmxscope.isolate_session(str(tmp_path))
    assert r["moved"] and not (tmp_path / xmxscope.SESSION_CONF).exists()
    assert (tmp_path / (xmxscope.SESSION_CONF + xmxscope.PARKED_SUFFIX)).exists()          # put back by restore_session
    env = xmxscope.xmx_env(str(tmp_path))
    assert env["VK_DRIVER_FILES"].startswith("/p/run/") and env["INTC_ALT_DRIVER_EXTENSIONS_PATH"] == "C:\\\\x\\\\igd_faux.inf_1"
    assert xess.package_dir(str(tmp_path)) == "/p"                                           # still found after the move
    assert xmxscope.restore_session(str(tmp_path))["restored"] and xmxscope.session_active(str(tmp_path))


def test_xmx_scope_loads_the_driver_only_for_xess_games_or_when_forced(tmp_path, monkeypatch):
    xmxscope, proton = _scope_home(tmp_path)
    _w(proton / "user_settings.user.py", "user_settings = {'PROTON_LOG': '1'}\n")    # the user's own Proton settings
    xmxscope.isolate_session(str(tmp_path))
    r = xmxscope.install_scope(str(tmp_path))
    assert r["ok"] and r["protons"] == [str(proton)]
    assert (proton / xmxscope.PREV_PY).exists() and xmxscope.state(str(tmp_path))["per_game"]
    script = proton / xmxscope.USER_PY
    xess_game, plain_game = tmp_path / "games/Cp", tmp_path / "games/Oni"
    _w(xess_game / "bin/x64/libxess.dll", "")
    _w(plain_game / "amd_fidelityfx_dx12.dll", "")
    base = {"HOME": str(tmp_path), "CLAWDECK_RUNTIME": str(tmp_path / "rt")}
    s = _run_scope(script, {**base, "SteamAppId": "1091500", "STEAM_COMPAT_INSTALL_PATH": str(xess_game)}, monkeypatch)
    assert s["VK_DRIVER_FILES"].startswith("/p/run/") and s["PROTON_LOG"] == "1"            # XeSS game: XMX; own settings kept
    s = _run_scope(script, {**base, "SteamAppId": "2638890", "STEAM_COMPAT_INSTALL_PATH": str(plain_game)}, monkeypatch)
    assert "VK_DRIVER_FILES" not in s                                                       # no XeSS: Bazzite's own driver
    _w(tmp_path / "rt/launch/2638890.env", "CLAWDECK_XMX=1\n")                              # Per-game: on
    s = _run_scope(script, {**base, "SteamAppId": "2638890", "STEAM_COMPAT_INSTALL_PATH": str(plain_game)}, monkeypatch)
    assert "VK_DRIVER_FILES" in s
    _w(tmp_path / "rt/launch/1091500.env", "CLAWDECK_XMX=0\n")                              # Per-game: off
    s = _run_scope(script, {**base, "SteamAppId": "1091500", "STEAM_COMPAT_INSTALL_PATH": str(xess_game)}, monkeypatch)
    assert "VK_DRIVER_FILES" not in s
    monkeypatch.setattr(xmxscope.userexec, "chown_tree", lambda p, h: None)
    assert xmxscope.set_every_game(str(tmp_path), True)["every_game"]                    # Setup: every game on XMX
    other = tmp_path / "games/Plain"
    other.mkdir(parents=True)
    s = _run_scope(script, {**base, "SteamAppId": "999", "STEAM_COMPAT_INSTALL_PATH": str(other)}, monkeypatch)
    assert "VK_DRIVER_FILES" in s                                                       # a game with nothing: XMX too
    s = _run_scope(script, {**base, "SteamAppId": "1091500", "STEAM_COMPAT_INSTALL_PATH": str(xess_game)}, monkeypatch)
    assert "VK_DRIVER_FILES" not in s                                                   # switched off per game: stays off
    assert not xmxscope.set_every_game(str(tmp_path), False)["every_game"]
    assert xmxscope.remove_scope(str(tmp_path))["protons"] == [str(proton)]
    assert (proton / "user_settings.user.py").read_text().startswith("user_settings = {'PROTON_LOG'")   # theirs is back


def test_external_display_ignores_the_internal_screen(tmp_path):
    from clawdeck import telemetry
    for c, st in (("card0-eDP-1", "connected"), ("card0-DP-1", "disconnected"), ("card0-HDMI-A-1", "disconnected"), ("card0-Writeback-1", "unknown")):
        _w(tmp_path / c / "status", st + "\n")
    assert telemetry.external_display(str(tmp_path)) == {"connected": False, "connectors": []}
    _w(tmp_path / "card0-DP-1" / "status", "connected\n")          # the MO27Q2 through USB-C
    assert telemetry.external_display(str(tmp_path)) == {"connected": True, "connectors": ["DP-1"]}


def test_quietlog_applies_only_without_the_reporter(tmp_path):
    from clawdeck import quietlog
    units = tmp_path / "units"
    _w(units / "steamos-manager.service", "[Unit]\n")
    dropin = tmp_path / "d" / "50.conf"
    assert quietlog.status(str(dropin), (str(units),)) == {"applies": True, "installed": False}
    r = quietlog.apply(str(dropin), restart=False)
    assert r["ok"] and dropin.read_text() == quietlog.TEXT and "steamos_manager::sls=off" in quietlog.TEXT
    assert quietlog.remove(str(dropin), restart=False)["ok"] and not dropin.exists()
    _w(units / "steamos-log-submitter.service", "[Unit]\n")          # SteamOS: the forwarding works there
    assert quietlog.status(str(dropin), (str(units),))["applies"] is False


def test_game_drive_flags_ntfs(tmp_path):
    from clawdeck import storage
    m = tmp_path / "mounts"
    m.write_text("/dev/sda3 /var/home btrfs rw 0 0\n/dev/mmcblk0p1 /run/media/hulk/18BE9AC8BE9A9E36 fuseblk rw 0 0\n"
                 "/dev/sdb1 /run/media/hulk/My\\040Games ext4 rw 0 0\n")
    d = storage.game_drive("/run/media/hulk/18BE9AC8BE9A9E36/SteamLibrary/steamapps/common/blackmythwukong", str(m))
    assert d["slow"] and d["sd"] and d["kind"] == "NTFS (ntfs-3g)"
    assert storage.game_drive("/var/home/hulk/.local/share/Steam/steamapps/common/CONTROL Resonant", str(m))["slow"] is False
    assert storage.game_drive("/run/media/hulk/My Games/x", str(m))["fstype"] == "ext4"
    assert storage.game_drive(None, str(m)) is None


def test_automatic_xmx_is_forced_for_hook_features():
    from clawdeck import launchenv
    assert "CLAWDECK_XMX" not in launchenv.compose({})                         # automatic: the scope decides
    assert launchenv.compose({"fsr": "xess"})["CLAWDECK_XMX"] == "1"           # an FSR-only game on XeSS (CONTROL)
    assert launchenv.compose({"fg": 3})["CLAWDECK_XMX"] == "1"
    assert launchenv.compose({"xmx": False, "fsr": "xess"})["CLAWDECK_XMX"] == "0"   # an explicit off wins


def test_ships_xess_matches_the_scope_script_depth(tmp_path):
    from clawdeck import xmxscope
    g = tmp_path / "game"
    _w(g / "a" / "b" / "c" / "libxess.dll", "x")
    assert xmxscope.ships_xess(str(g))
    deep = tmp_path / "deep"
    _w(deep / "1" / "2" / "3" / "4" / "5" / "6" / "libxess.dll", "x")   # deeper than the launch-time check looks
    assert not xmxscope.ships_xess(str(deep))
    assert not xmxscope.ships_xess(None)


def _game(tmp_path, name, files):
    d = tmp_path / name
    for f in files:
        _w(d / f, "x")
    return str(d)


def test_gamedb_recommends_by_engine_and_list(tmp_path):
    from clawdeck import gamedb
    doc = gamedb.load(os.path.join(os.path.dirname(__file__), "..", "assets", "games.json"))
    assert len(doc["games"]) > 50 and "onimushawots.exe" in doc["re_engine_exes"]
    oni = gamedb.recommend(doc, _game(tmp_path, "oni", ["OnimushaWotS.exe", "amd_fidelityfx_dx12.dll"]))
    assert oni["route"] == "re_engine" and oni["launch"]["reframework"] is True
    assert oni["ini"] == {"Hotfix_RestoreComputeSignature": "true", "Spoofing_Dxgi": "false"}   # OptiScaler main, not 0.9.4
    control = gamedb.recommend(doc, _game(tmp_path, "control", ["CONTROLResonant.exe", "amd_fidelityfx_upscaler_dx12.dll"]))
    assert control["route"] == "optiscaler" and control["optiscaler"] and "Pick FSR" in control["reasons"][0]
    own = gamedb.recommend(doc, _game(tmp_path, "x", ["game.exe", "bin/libxess.dll"]))
    assert own["route"] == "own_xess" and own["launch"] == {}
    ue = gamedb.recommend(doc, _game(tmp_path, "shf", ["SHf/Binaries/Win64/SHf-Win64-Shipping.exe"]))
    assert ue["known"] and ue["route"] == "optiscaler" and ue["launch"]["opti"] == "xess"
    from clawdeck import launchenv
    env = launchenv.compose(oni["launch"])
    assert env["Hotfix_RestoreComputeSignature"] == "true" and env["WINEDLLOVERRIDES"] == "dinput8=n,b;"
    with pytest.raises(ValueError):
        launchenv.compose({"opti_ini": {"PATH": "/tmp"}})              # only Section_Key=simple value
    with pytest.raises(ValueError):
        launchenv.compose({"opti_ini": {"Spoofing_Dxgi": "false; rm -rf"}})


def test_reframework_never_overwrites_a_foreign_dinput8(tmp_path, monkeypatch):
    from clawdeck import reframework, downloads
    folder = tmp_path / "g"
    _w(folder / "dinput8.dll", "reshade")
    r = reframework.install(str(tmp_path), str(folder), {"repo": "praydog/REFramework-nightly", "tag": "t1"})
    assert not r["ok"] and "isn't ClawDeck's" in r["error"]
    os.remove(folder / "dinput8.dll")
    src = tmp_path / "unpacked"
    _w(src / "dinput8.dll", "refw")
    monkeypatch.setattr(downloads, "fetch", lambda e, w, a=False: {"tag": "t1", "repo": e["repo"], "archive": "REFramework.zip", "dir": str(src)})
    monkeypatch.setattr(reframework.userexec, "chown_tree", lambda p, h: None)
    r = reframework.install(str(tmp_path), str(folder), {"repo": "praydog/REFramework-nightly", "tag": "t1"})
    assert r["ok"] and r["ours"] and (folder / "dinput8.dll").read_text().strip() == "refw"
    assert reframework.remove(str(folder))["removed"] and not (folder / "dinput8.dll").exists()


def test_every_listed_setting_is_a_valid_launch_variable():
    from clawdeck import gamedb, launchenv
    doc = gamedb.load(os.path.join(os.path.dirname(__file__), "..", "assets", "games.json"))
    for g in doc["games"]:
        launchenv.compose({"opti": "xess", "opti_xefg": True, "opti_ini": {**g["ini"], **g["when"].get("xefg", {})}})


def test_overlay_power_boot_rule(tmp_path):
    from clawdeck import telemetry
    p = tmp_path / "tmpfiles.d" / "x.conf"
    assert telemetry.boot_rule(True, str(p)) is None
    assert "z /sys/class/powercap/intel-rapl:0/energy_uj 0444 - - -" in p.read_text()
    assert telemetry.boot_rule(False, str(p)) is None and not p.exists()


def test_launch_wrapper_puts_fsr4_xmx_in_after_fgmod_and_takes_it_out_again(tmp_path):
    import subprocess
    from clawdeck import launchenv
    game = tmp_path / "common" / "Game"
    _w(game / "Game.exe", "x")
    src = tmp_path / "fsr4-xmx"
    for f, t in (("amd_fidelityfx_upscaler_dx12.dll", "bboost"), ("fsr-original.dll", "o"), ("cabinet.dll", "lzms")):
        _w(src / f, t)
    proton = tmp_path / "proton"
    _w(proton / "files/lib/wine/x86_64-windows/cabinet.dll", "wine-cab")
    fgmod = tmp_path / "fgmod" / "fgmod"                     # a stand-in: copies its own FSR 4, then runs "$@"
    _w(fgmod, f'#!/bin/bash\necho fgmod-fsr4 > "{game}/amd_fidelityfx_upscaler_dx12.dll"\nexec "$@"\n')
    os.chmod(fgmod, 0o755)
    (tmp_path / "launch").mkdir()
    env = launchenv.compose({"opti": "fsr4_xmx_lite"})
    assert env["FSR4_XMX_LITE"] == "1" and env["WINEDLLOVERRIDES"] == "cabinet=n,b;" and env["CLAWDECK_XMX"] == "1"
    (tmp_path / "launch" / "42.env").write_text(launchenv.render(env))
    base = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLAWDECK_RUNTIME": str(tmp_path), "SteamAppId": "42",
            "CLAWDECK_NO_MANGOHUD": "1", "CLAWDECK_FSR4_XMX_DIR": str(src), "STEAM_COMPAT_TOOL_PATHS": str(proton)}
    wrapper = os.path.join(ROOT, "tools", "clawdeck-launch.sh")
    out = subprocess.run(["bash", wrapper, str(fgmod), "bash", "-c", "env", "x", str(game / "Game.exe")],
                         capture_output=True, text=True, env=base)
    assert (game / "amd_fidelityfx_upscaler_dx12.dll").read_text().strip() == "bboost", out.stderr   # over fgmod's
    assert (game / "cabinet_wine.dll").read_text().strip() == "wine-cab" and (game / "fsr-original.dll").exists()
    assert "FSR4_XMX_LITE=1" in out.stdout and "CLAWDECK_STAGE" not in out.stdout
    (tmp_path / "launch" / "42.env").write_text(launchenv.render(launchenv.compose({"opti": "xess"})))
    subprocess.run(["bash", wrapper, str(fgmod), "bash", "-c", "true", "x", str(game / "Game.exe")], check=True, env=base)
    assert not (game / "cabinet.dll").exists() and not (game / ".clawdeck-fsr4xmx").exists()
    assert (game / "amd_fidelityfx_upscaler_dx12.dll").read_text().strip() == "fgmod-fsr4"


def _opti_pkg(home, ver="0.9.4"):
    from clawdeck import optiscaler
    d = os.path.join(home, optiscaler.ROOT, ver)
    for f, t in (("OptiScaler.dll", "opti"), ("OptiScaler.ini", "[Upscalers]\nDx12Upscaler=auto\n\n[Menu]\nUseHQFont=auto\n\n[Plugins]\nLoadAsiPlugins=auto\n"),
                 ("libxess.dll", "xess-opti"), ("amd_fidelityfx_upscaler_dx12.dll", "amd-fsr4"), ("D3D12_Optiscaler/D3D12Core.dll", "core"),
                 ("plugins/OptiPatcher.asi", "op"), ("setup_linux.sh", "x"), ("Licenses/x.txt", "l")):
        _w(os.path.join(d, f), t)
    return d


def test_optiscaler_patch_and_unpatch_leave_the_game_as_it_was(tmp_path, monkeypatch):
    from clawdeck import optiscaler
    monkeypatch.setattr(optiscaler.userexec, "chown_tree", lambda p, h: None)
    home = str(tmp_path)
    _opti_pkg(home)
    g = tmp_path / "game" / "bin"
    _w(g / "Game.exe", "x" * 100)
    _w(g / "libxess.dll", "game-own-xess")          # the game's own file: backed up, put back
    _w(g / "dxgi.dll", "reshade")                    # a proxy name already taken: next one is used
    r = optiscaler.patch(home, str(g), "0.9.4", {"Upscalers_Dx12Upscaler": "xess", "Hotfix_RestoreComputeSignature": "true"})
    assert r["ok"] and r["dll"] == "winmm.dll" and r["dll_override"] == "winmm"
    assert (g / "winmm.dll").read_text().strip() == "opti" and (g / "dxgi.dll").read_text().strip() == "reshade"
    ini = (g / "OptiScaler.ini").read_text()
    assert "Dx12Upscaler=xess" in ini and "LoadAsiPlugins=true" in ini and "UseHQFont=false" in ini
    assert "[Hotfix]\nRestoreComputeSignature=true" in ini                     # a missing section is added
    assert (g / "plugins" / "OptiPatcher.asi").exists() and not (g / "setup_linux.sh").exists()
    assert optiscaler.owner(str(g))["owner"] == "clawdeck"
    # FSR 4 on XMX: nitec's files instead of AMD's upscaler; then back
    x = tmp_path / "fsr4-xmx"
    for f in ("amd_fidelityfx_upscaler_dx12.dll", "fsr-original.dll", "cabinet.dll", "cabinet_wine.dll"):
        _w(x / f, "nitec-" + f)
    assert optiscaler.patch(home, str(g), "0.9.4", {}, "fsr4_xmx", fsr4_xmx=True)["ok"]
    assert (g / "amd_fidelityfx_upscaler_dx12.dll").read_text().strip() == "nitec-amd_fidelityfx_upscaler_dx12.dll"
    assert (g / "cabinet.dll").exists() and (g / "cabinet_wine.dll").exists()
    assert optiscaler.patch(home, str(g), "0.9.4", {}, "xess")["ok"]
    assert not (g / "cabinet.dll").exists() and (g / "amd_fidelityfx_upscaler_dx12.dll").read_text().strip() == "amd-fsr4"
    for f in ("OptiScaler.log", "fakenvapi.log", "D3D12_Optiscaler/cache.bin"):   # written by them while the game ran
        _w(g / f, "runtime")
    assert optiscaler.unpatch(str(g))["removed"]
    assert sorted(p.name for p in g.iterdir()) == ["Game.exe", "dxgi.dll", "libxess.dll"]
    assert (g / "libxess.dll").read_text().strip() == "game-own-xess"


def test_optiscaler_never_touches_another_tools_install(tmp_path, monkeypatch):
    from clawdeck import optiscaler
    home = str(tmp_path)
    _opti_pkg(home)
    g = tmp_path / "g"
    _w(g / "Game.exe", "x")
    _w(g / "OptiScaler.ini", "theirs")
    r = optiscaler.patch(home, str(g), "0.9.4", {})
    assert not r["ok"] and r["owner"] == "other" and (g / "OptiScaler.ini").read_text().strip() == "theirs"
    _w(g / "dxgi.dll.b", "backup by fgmod")
    assert optiscaler.owner(str(g))["owner"] == "decky-framegen"


def test_main_exe_dir_finds_the_real_exe(tmp_path):
    from clawdeck import optiscaler
    _w(tmp_path / "Launcher.exe", "x" * 500)
    _w(tmp_path / "Game" / "Binaries" / "Win64" / "Game-Win64-Shipping.exe", "x")
    assert optiscaler.main_exe_dir(str(tmp_path)).endswith("Binaries/Win64")
    t2 = tmp_path / "two"
    _w(t2 / "unins000.exe", "x" * 900)
    _w(t2 / "bin" / "big.exe", "x" * 300)
    assert optiscaler.main_exe_dir(str(t2)).endswith("bin")


def test_installed_games_from_steam_manifests(tmp_path):
    lib2 = tmp_path / "sd" / "SteamLibrary"
    _w(tmp_path / ".local/share/Steam/steamapps/libraryfolders.vdf", f'"libraryfolders"\n{{\n "1" {{ "path" "{lib2}" }}\n}}\n')
    for lib, appid, name, d in ((tmp_path / ".local/share/Steam", "3669870", "CONTROL Resonant", "CONTROL Resonant"),
                                (lib2, "2358720", "Black Myth: Wukong", "blackmythwukong"),
                                (tmp_path / ".local/share/Steam", "3658110", "Proton 11.0", "Proton 11.0")):
        _w(lib / "steamapps" / f"appmanifest_{appid}.acf", f'"AppState"\n{{\n "appid" "{appid}"\n "name" "{name}"\n "installdir" "{d}"\n}}\n')
        (lib / "steamapps" / "common" / d).mkdir(parents=True)
    gs = xess.installed_games(str(tmp_path))
    assert [g["name"] for g in gs] == ["Black Myth: Wukong", "CONTROL Resonant"]


def test_opti_ini_follows_the_frame_generation_multiplier():
    from clawdeck import launchenv
    ini = launchenv.opti_ini({"opti": "xess", "opti_fg": "dlssg", "fg": 3})
    assert ini["XeFG_InterpolationCount"] == "2" and ini["FrameGen_FGInput"] == "dlssg" and ini["Upscalers_Dx12Upscaler"] == "xess"
    assert "XeFG_InterpolationCount" not in launchenv.opti_ini({"opti": "xess", "opti_fg": "off", "fg": 3})


def test_support_needs_an_upscaler_or_a_list_entry(tmp_path):
    from clawdeck import gamedb
    doc = gamedb.load(os.path.join(os.path.dirname(__file__), "..", "assets", "games.json"))
    assert gamedb.support(doc, _game(tmp_path, "c", ["CONTROLResonant.exe", "nvngx_dlss.dll", "amd_fidelityfx_upscaler_dx12.dll"]))["via"][:2] == ["DLSS", "FSR"]
    assert gamedb.support(doc, _game(tmp_path, "u", ["G/Plugins/DLSS/Binaries/ThirdParty/Win64/nvngx_dlss.dll", "G.exe"]))["ok"]
    assert not gamedb.support(doc, _game(tmp_path, "b", ["Brotato.exe", "data.pck"]))["ok"]          # nothing to hook


def test_is_dx12_from_libraries_or_the_exe(tmp_path):
    from clawdeck import gamedb
    assert gamedb.is_dx12(_game(tmp_path, "a", ["G.exe", "amd_fidelityfx_dx12.dll"]))
    d = tmp_path / "b"
    _w(d / "Game-Win64-Shipping.exe", "MZ....D3D12.dll....")
    assert gamedb.is_dx12(str(d))
    assert not gamedb.is_dx12(_game(tmp_path, "c", ["Old.exe", "d3d9.dll"]), ["old.exe"])


def test_recommend_ignores_clawdeck_optiscaler_files(tmp_path):
    """OptiScaler's own libxess.dll must not turn a DLSS game into 'ships XeSS' once ClawDeck set it up."""
    import json as _j
    from clawdeck import gamedb
    g = tmp_path / "Game"
    g.mkdir()
    (g / "Game.exe").write_bytes(b"MZ")
    (g / "nvngx_dlss.dll").write_bytes(b"x")
    assert gamedb.recommend({"games": []}, str(g))["route"] != "own_xess"
    (g / "libxess.dll").write_bytes(b"x")
    (g / "amd_fidelityfx_dx12.dll").write_bytes(b"x")
    st = g / ".clawdeck-optiscaler"
    st.mkdir()
    (st / "manifest.json").write_text(_j.dumps({"added": ["libxess.dll", "amd_fidelityfx_dx12.dll", "OptiScaler.ini"],
                                                "backed": ["amd_fidelityfx_dx12.dll"]}))
    assert gamedb.recommend({"games": []}, str(g))["route"] != "own_xess"
    assert gamedb._has(str(g), ("amd_fidelityfx_dx12.dll",))     # the game's own, backed up: still counts
    (st / "manifest.json").write_text(_j.dumps({"added": [], "backed": []}))
    assert gamedb.recommend({"games": []}, str(g))["route"] == "own_xess"


def test_fsr4_xmx_picks_4_1_2_in_optiscaler(tmp_path, monkeypatch):
    """OptiScaler lists AMD's 4.0.2 first with the B Boost files; the pick must be 4.1.2 (index 6), written into the ini."""
    from clawdeck import launchenv, optiscaler
    monkeypatch.setattr(optiscaler.userexec, "chown_tree", lambda p, h: None)
    for preset in ("fsr4_xmx", "fsr4_xmx_lite"):
        ini = launchenv.opti_ini({"opti": preset})
        assert ini.get("FSR_UpscalerIndex") == "6" and ini.get("Upscalers_Dx12Upscaler", ini.get("Dx12Upscaler")) == "fsr31"
    home = str(tmp_path)
    _opti_pkg(home)
    x = tmp_path / "fsr4-xmx"
    for f in ("amd_fidelityfx_upscaler_dx12.dll", "fsr-original.dll", "cabinet.dll", "cabinet_wine.dll"):
        _w(x / f, "nitec-" + f)
    g = tmp_path / "game"
    _w(g / "Game.exe", "x")
    assert optiscaler.patch(home, str(g), "0.9.4", launchenv.opti_ini({"opti": "fsr4_xmx", "fg": 2, "opti_fg": "dlssg"}), "fsr4_xmx", fsr4_xmx=True)["ok"]
    ini = (g / "OptiScaler.ini").read_text()
    assert "UpscalerIndex=6" in ini.replace(" ", "") and "Dx12Upscaler=fsr31" in ini.replace(" ", "")
    assert "FGOutput=xefg" in ini.replace(" ", "")


def test_opti_overlay_goes_into_optiscaler_ini():
    from clawdeck import launchenv
    ini = launchenv.opti_ini({"opti": "xess", "opti_overlay": 2})
    assert ini["Menu_ShowFps"] == "true" and ini["Menu_FpsOverlayType"] == "2" and ini["Menu_FpsOverlayPos"] == "1"
    assert "Menu_ShowFps" not in launchenv.opti_ini({"opti": "xess"})
    import pytest
    with pytest.raises(ValueError):
        launchenv.compose({"opti": "xess", "opti_overlay": 9})


def test_xmx_proof_reads_the_prefix(tmp_path):
    import os, time
    from clawdeck import xess
    home = str(tmp_path)
    pfx = xess.prefix_dir(home, "42")
    os.makedirs(os.path.join(pfx, "igdext_kernels"))
    assert xess.xmx_proof(home, "42")["state"] == "never"
    rt = tmp_path / "rt" / "launch"
    rt.mkdir(parents=True)
    (rt / "42.started").write_text("")
    with open(os.path.join(pfx, "igdext_kernels", "map.txt"), "w") as f:
        f.write("1 aa\n2 bb\n")
    open(os.path.join(pfx, "igdext_kernels", "anv_canary"), "w").write("")
    p = xess.xmx_proof(home, "42", str(tmp_path / "rt"))
    assert p["state"] == "ok" and p["kernels"] == 2
    old = time.time() - 7200
    os.utime(os.path.join(pfx, "igdext_kernels", "anv_canary"), (old, old))
    assert xess.xmx_proof(home, "42", str(tmp_path / "rt"))["state"] == "stale"
    open(os.path.join(pfx, "igdext_kernels", "compile_failed.txt"), "w").write("k")
    assert xess.xmx_proof(home, "42")["state"] == "failed"


def test_profiles_own_slot_is_only_the_games(tmp_path):
    from clawdeck import profiles
    p = profiles.Profiles(str(tmp_path / "p.json")) if hasattr(profiles, "Profiles") else None
    if p is None:
        return
    assert p.own("1", False) == {} and p.get("1", False).get("base") == "battery"
    p.set("1", False, {"base": "max", "tdp": None})
    assert p.own("1", False) == {"base": "max"} and p.own("1", True) == {}
