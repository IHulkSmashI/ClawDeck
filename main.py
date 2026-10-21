"""ClawDeck - Decky Loader backend. Thin: every call maps to one module in py_modules/clawdeck.

Runs as root (plugin.json flag _root) because sysfs, Handheld Daemon's socket and the Steam library need it.
Battery rule: nothing here runs on a timer unless autotune is on for a running game or a benchmark run is going
(1 Hz, one asyncio task each). Power-source changes and resume arrive as events from Steam (frontend listeners).
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "py_modules"))

import decky  # noqa: E402

from clawdeck import (autotune, battery, benchmark, controller, display, downloads, fans, health, hhd, hw,  # noqa: E402
                      injector, launchenv, power, profiles, proton, sleep, stack, sysfs, userexec, xess)

PLUGIN_DIR = os.path.dirname(os.path.realpath(__file__))
SETTINGS_DIR = decky.DECKY_PLUGIN_SETTINGS_DIR
RUNTIME_DIR = decky.DECKY_PLUGIN_RUNTIME_DIR
LOG_DIR = decky.DECKY_PLUGIN_LOG_DIR
HOME = decky.DECKY_USER_HOME
DESKTOP = os.path.join(HOME, "Desktop")
VERSIONS = os.path.join(PLUGIN_DIR, "assets", "versions.json")
POWER_KEYS = ("tdp", "cpu_max_pct", "gpu_min_mhz", "gpu_max_mhz")
# The launch wrapper lives outside the plugin folder: Steam's launch options keep pointing at it after an uninstall
# deletes the plugin, so it must stay there (as a pass-through) or those games would no longer start.
WRAPPER = os.path.join(HOME, ".local", "share", "clawdeck", "clawdeck-launch.sh")
PASSTHROUGH = "#!/bin/bash\n# ClawDeck was removed; this only starts the game. Delete it from the game's launch options when you like.\nexec \"$@\"\n"


def _write_user_file(path: str, text: str) -> str:
    """Write a file in the user's home as the user would: missing folders and the file end up owned by them."""
    d = os.path.dirname(path)
    missing = []
    while d and not os.path.isdir(d):
        missing.append(d)
        d = os.path.dirname(d)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    for n in [path, *missing]:
        userexec.chown_tree(n, HOME)
    return path


class Plugin:
    # ---- lifecycle ---------------------------------------------------------------------------------------------------
    async def _main(self):
        self.settings = self._load_settings()
        env_dry = os.environ.get("CLAWDECK_DRY_RUN", "0") not in ("", "0")
        sysfs.set_dry_run(env_dry or bool(self.settings.get("dry_run")))
        self.hw = hw.detect()
        self.power = power.Power(self.hw)
        self.battery = battery.Battery(self.hw)
        self.profiles = profiles.Profiles(os.path.join(SETTINGS_DIR, "profiles.json"))
        self.injector = injector.Injector(os.path.join(RUNTIME_DIR, "assets"))
        mangohud_dir = os.path.join(RUNTIME_DIR, "mangohud")
        # games run as the desktop user and MangoHud writes its frame-time log here: the folder must be theirs
        os.makedirs(mangohud_dir, exist_ok=True)
        userexec.chown_tree(mangohud_dir, HOME)
        self.autotune = autotune.Autotune(self.hw, self.power, mangohud_dir, csv_log=os.path.join(LOG_DIR, "autotune.csv"))
        self.autotune.enabled = bool(self.settings.get("autotune"))
        self.bench = benchmark.Benchmark(self.hw, mangohud_dir, os.path.join(SETTINGS_DIR, "benchmarks.json"))
        self.controller = controller.Controller()
        self.fans = fans.Fans(self.hw)
        self.display = display.Display(self.hw, HOME)
        self.running_appid: str | None = None
        self.running_plugged: bool | None = None
        decky.logger.info("ClawDeck on %s (%s), kernel %s, dry-run %s", self.hw.model["name"], self.hw.product,
                          self.hw.kernel, sysfs.dry_run())
        # the one setting applied at every start on purpose: the charge limit (sysfs resets at boot)
        self._apply_charge_limit()
        self._make_tools_executable()
        self._install_wrapper()

    def _make_tools_executable(self) -> None:
        """Decky unpacks plugin zips without permission bits; the launch wrapper must be executable for Steam."""
        tools = os.path.join(PLUGIN_DIR, "tools")
        try:
            for fn in os.listdir(tools):
                if fn.endswith((".sh", ".py")):
                    os.chmod(os.path.join(tools, fn), 0o755)
        except OSError as e:
            decky.logger.warning("could not mark tools executable: %s", e)

    async def _unload(self):
        self.autotune.stop()
        self.bench.stop()
        n = self.power.revert_all()
        decky.logger.info("ClawDeck unloaded, %d value(s) reverted", n)

    async def _uninstall(self):
        """Leave nothing behind that changes how the Claw or a game behaves."""
        self.power.revert_all()
        self.display.reset()
        if self.settings.get("fan_preset_set"):
            self.fans.apply_preset("auto")
        try:
            _write_user_file(WRAPPER, PASSTHROUGH)
            os.chmod(WRAPPER, 0o755)
        except OSError as e:
            decky.logger.warning("could not leave the pass-through wrapper: %s", e)

    def _install_wrapper(self) -> None:
        try:
            with open(os.path.join(PLUGIN_DIR, "tools", "clawdeck-launch.sh"), "r", encoding="utf-8") as f:
                _write_user_file(WRAPPER, f.read())
            os.chmod(WRAPPER, 0o755)
        except OSError as e:
            decky.logger.warning("could not install the launch wrapper: %s", e)

    def _apply_charge_limit(self) -> None:
        lim = self.settings.get("charge_limit")
        if lim and self.battery.supported():
            try:
                self.battery.set_charge_limit(int(lim))
            except ValueError:
                pass

    def _load_settings(self) -> dict:
        try:
            with open(os.path.join(SETTINGS_DIR, "settings.json"), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_settings(self) -> None:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(os.path.join(SETTINGS_DIR, "settings.json"), "w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=2)

    def _versions(self) -> dict:
        return downloads.load_versions(VERSIONS)

    # ---- setup / health ----------------------------------------------------------------------------------------------
    async def get_hardware(self) -> dict:
        return {**self.hw.to_dict(), "stack": stack.detect(), "other_plugins": stack.other_plugins(HOME)}

    async def get_health(self) -> list:
        return health.checks(self.hw, HOME)

    async def collect_logs(self) -> str:
        dest = os.path.join(DESKTOP, f"clawdeck-report-{time.strftime('%Y%m%d-%H%M%S')}.json")
        path = health.collect_logs(self.hw, HOME, dest)
        userexec.chown_tree(path, HOME)
        return path

    async def run_fix(self, fix: str, allow_unpinned: bool = False) -> dict:
        """The one-tap fixes the health check offers (Health.fix)."""
        if fix == "enable_hhd":
            if stack.detect()["inputplumber"]:
                return {"ok": False, "error": "InputPlumber is running; starting Handheld Daemon too would make them fight over the controller"}
            u = userexec.user_of(HOME)
            if u is None:
                return {"ok": False, "error": "cannot tell which user to enable Handheld Daemon for"}
            if sysfs.dry_run():
                return {"ok": True, "dry_run": True, "would": f"systemctl enable --now hhd@{u.pw_name}"}
            r = subprocess.run(["systemctl", "enable", "--now", f"hhd@{u.pw_name}"], capture_output=True, text=True,
                               timeout=60, check=False)
            return {"ok": r.returncode == 0, "log": (r.stdout + r.stderr)[-2000:]}
        if fix == "install_xess":
            return await self.xess_install(allow_unpinned)
        if fix == "report_unknown_model":
            r = subprocess.run(["bash", os.path.join(PLUGIN_DIR, "tools", "sysfs-survey.sh")], capture_output=True,
                               text=True, timeout=60, check=False)
            path = _write_user_file(os.path.join(DESKTOP, f"clawdeck-survey-{time.strftime('%Y%m%d-%H%M%S')}.txt"),
                                    r.stdout + r.stderr)
            return {"ok": True, "path": path, "next": "open a GitHub issue 'Survey: <model>' and attach this file"}
        return {"ok": False, "error": f"unknown fix {fix}"}

    async def get_setting(self, key: str):
        return self.settings.get(key)

    async def set_setting(self, key: str, value) -> bool:
        self.settings[key] = value
        self._save_settings()
        if key == "autotune":
            self.autotune.enabled = bool(value)
            if not value:
                self.autotune.pause("autotune switched off")
        return True

    async def set_dry_run(self, on: bool) -> bool:
        sysfs.set_dry_run(bool(on))
        return sysfs.dry_run()

    async def get_versions(self) -> dict:
        v = self._versions()
        return {k: {**e, "pinned": downloads.is_pinned(e)} for k, e in v.items() if not k.startswith("_")}

    # ---- power -------------------------------------------------------------------------------------------------------
    async def get_power(self) -> dict:
        return self.power.status()

    async def set_tdp(self, watts: int) -> bool:
        self.autotune.pause("manual TDP")
        return self.power.set_tdp(int(watts), self.battery.plugged())

    async def set_cpu_max_pct(self, pct: int) -> bool:
        self.autotune.pause("manual CPU ceiling")
        return self.power.set_cpu_max_pct(int(pct))

    async def set_cpu_turbo(self, on: bool) -> bool:
        return self.power.set_cpu_turbo(bool(on))

    async def set_gpu_window(self, min_mhz, max_mhz) -> bool:
        self.autotune.pause("manual GPU clocks")
        return self.power.set_gpu_window(None if min_mhz is None else int(min_mhz), None if max_mhz is None else int(max_mhz))

    async def reset_gpu_window(self) -> bool:
        return self.power.reset_gpu_window()

    async def apply_profile(self, name: str, appid: str | None = None) -> dict:
        self.autotune.pause(f"profile {name} applied by hand")
        plugged = self.battery.plugged()
        prof = self.profiles.get(appid, bool(plugged)) if appid else {}
        overrides = {k: v for k, v in prof.items() if k in POWER_KEYS}
        self.settings["last_profile"] = name
        self._save_settings()
        return self.power.apply_profile(name, overrides, plugged)

    async def revert_power(self) -> int:
        self.autotune.pause("revert to defaults")
        return self.power.revert_all()

    async def list_profiles(self) -> dict:
        return hw.profiles_for(self.hw.product)

    async def save_game_power(self, appid: str, values: dict) -> dict:
        """Save the current manual values as this game's profile for the current power source."""
        plugged = bool(self.battery.plugged())
        clean = {k: (None if values.get(k) is None else int(values[k])) for k in POWER_KEYS if k in values}
        if "base" in values:
            clean["base"] = str(values["base"])
        return self.profiles.set(appid, plugged, clean)

    # ---- per-game profiles -------------------------------------------------------------------------------------------
    async def get_game_profile(self, appid: str) -> dict:
        return {"battery": self.profiles.get(appid, False), "plugged": self.profiles.get(appid, True),
                "launch": self.profiles.get_launch(appid)}

    async def set_game_profile(self, appid: str, plugged: bool, values: dict) -> dict:
        return self.profiles.set(appid, bool(plugged), values)

    async def reset_learned(self, appid: str) -> bool:
        for plugged in (False, True):
            self.profiles.set(appid, plugged, {"learned": None})
        return True

    async def export_profiles(self, appid: str | None = None) -> str:
        return self.profiles.export_json(appid)

    async def import_profiles(self, text: str, overwrite: bool = False) -> int:
        return self.profiles.import_json(text, bool(overwrite))

    async def export_profiles_file(self, appid: str | None = None) -> str:
        tag = appid or "all"
        return _write_user_file(os.path.join(DESKTOP, f"clawdeck-profiles-{tag}-{time.strftime('%Y%m%d')}.json"),
                                self.profiles.export_json(appid))

    async def list_profile_files(self) -> list:
        """Profile files the user can import: *.json with 'clawdeck' in the name on the Desktop or in Downloads."""
        out = []
        for d in (DESKTOP, os.path.join(HOME, "Downloads")):
            try:
                for fn in sorted(os.listdir(d)):
                    if fn.lower().endswith(".json") and "clawdeck" in fn.lower() and "report" not in fn.lower():
                        out.append(os.path.join(d, fn))
            except OSError:
                continue
        return out

    async def import_profiles_file(self, path: str, overwrite: bool = False) -> dict:
        allowed = (os.path.realpath(DESKTOP) + os.sep, os.path.realpath(os.path.join(HOME, "Downloads")) + os.sep)
        rp = os.path.realpath(path)
        if not rp.startswith(allowed):
            return {"ok": False, "error": "import only from Desktop or Downloads"}
        try:
            with open(rp, "r", encoding="utf-8") as f:
                n = self.profiles.import_json(f.read(), bool(overwrite))
        except (OSError, ValueError) as e:
            return {"ok": False, "error": str(e)}
        for appid in self.profiles.games():
            self._write_launch(appid)
        return {"ok": True, "imported": n}

    # ---- game lifecycle (called by the frontend's Steam hooks) ------------------------------------------------------
    def _start_game_power(self, appid: str, plugged: bool) -> dict:
        prof = self.profiles.get(appid, plugged)
        base = prof.get("base", "plugged" if plugged else "battery")
        overrides = {k: v for k, v in prof.items() if k in POWER_KEYS}
        result = self.power.apply_profile(base, overrides, plugged)
        if self.settings.get("autotune") and prof.get("autotune", True) and prof.get("target_fps"):
            lim = self.hw.model["tdp"]
            self.autotune.start(appid, int(prof["target_fps"]), prof.get("learned"), lim["min"],
                                lim["plugged_max"] if plugged else lim["battery_max"], plugged)
            result["autotune"] = "started"
        return result

    def _save_learned(self, appid: str, plugged: bool) -> dict | None:
        learned = self.autotune.stop()
        if learned and learned.get("learned"):
            self.profiles.set(appid, plugged, {"learned": learned["learned"]})
        return learned

    async def game_started(self, appid: str) -> dict:
        plugged = bool(self.battery.plugged())
        self.running_appid, self.running_plugged = str(appid), plugged
        return self._start_game_power(str(appid), plugged)

    async def game_stopped(self, appid: str) -> dict:
        plugged = self.running_plugged if self.running_plugged is not None else bool(self.battery.plugged())
        learned = self._save_learned(str(appid), plugged)
        self.running_appid, self.running_plugged = None, None
        n = self.power.revert_all()
        return {"reverted": n, "learned": learned}

    async def power_source_changed(self, plugged: bool) -> dict:
        """Steam reported AC plugged/unplugged: the running game switches to its other slot (battery / plugged), and
        autotune keeps learning in the slot that matches the new source."""
        plugged = bool(plugged)
        if self.running_appid is None or plugged == self.running_plugged:
            self.running_plugged = plugged if self.running_appid else None
            return {"changed": False}
        appid = self.running_appid
        self._save_learned(appid, bool(self.running_plugged))
        self.running_plugged = plugged
        return {"changed": True, **self._start_game_power(appid, plugged)}

    async def resumed(self) -> dict:
        """After resume from suspend: firmware may have reset limits; re-apply the charge limit and the running game."""
        self._apply_charge_limit()
        if self.running_appid is None:
            return {"reapplied": False}
        plugged = bool(self.battery.plugged())
        self._save_learned(self.running_appid, bool(self.running_plugged))
        self.running_plugged = plugged
        return {"reapplied": True, **self._start_game_power(self.running_appid, plugged)}

    async def get_autotune(self) -> dict:
        return self.autotune.status()

    # ---- battery and fans --------------------------------------------------------------------------------------------
    async def get_battery(self) -> dict:
        return self.battery.status()

    async def set_charge_limit(self, percent: int) -> bool:
        ok = self.battery.set_charge_limit(int(percent))
        if ok:
            self.settings["charge_limit"] = int(percent)
            self._save_settings()
        return ok

    async def get_fans(self) -> dict:
        return self.fans.status()

    async def set_fan_preset(self, name: str) -> dict:
        r = self.fans.apply_preset(name)
        if r.get("ok"):
            self.settings["fan_preset_set"] = name != "auto"
            self._save_settings()
        return r

    # ---- display -----------------------------------------------------------------------------------------------------
    async def get_display(self) -> dict:
        return self.display.read()

    async def save_display(self, settings: dict) -> dict:
        return self.display.save(settings, sysfs.dry_run())

    async def reset_display(self) -> dict:
        return self.display.reset()

    # ---- sleep -------------------------------------------------------------------------------------------------------
    async def get_sleep(self) -> dict:
        return sleep.status()

    # ---- controller --------------------------------------------------------------------------------------------------
    async def get_controller(self) -> dict:
        return self.controller.settings()

    async def set_controller(self, key: str, value) -> dict:
        try:
            return self.controller.set(key, value)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    # ---- per-game launch environment (injector, HDR, Mesa) ---------------------------------------------------------
    def _write_launch(self, appid: str) -> dict:
        try:
            return launchenv.write(RUNTIME_DIR, appid, self.profiles.get_launch(appid))
        except ValueError as e:
            return {"error": str(e)}

    async def get_launch(self, appid: str) -> dict:
        launch = self.profiles.get_launch(appid)
        return {"launch": launch, "env": launchenv.compose(launch),
                "wrapper": WRAPPER + " %command%"}

    async def set_launch(self, appid: str, values: dict) -> dict:
        allowed = {"inject", "lsfg", "hdr", "mesa", "proton"}
        clean = {k: v for k, v in values.items() if k in allowed}
        self.profiles.set_launch(appid, clean)
        return {**(await self.get_launch(appid)), "file": self._write_launch(appid)}

    # ---- XMX injector ------------------------------------------------------------------------------------------------
    async def injector_state(self, appid: str) -> dict:
        game_dir = injector.steam_install_dir(HOME, appid)
        st = self.injector.state(game_dir) if game_dir else {"dir": None, "mode": "none", "optiscaler_present": False,
                                                               "native_xess": False, "info": None}
        st["assets"] = self.injector.assets_ready()
        st["lsfg"] = injector.lsfg_state(HOME)
        return st

    async def injector_install(self, appid: str, mode: str, xess_fg: bool = True, lsfg: dict | None = None) -> dict:
        game_dir = injector.steam_install_dir(HOME, appid)
        if not game_dir:
            return {"error": "game folder not found in Steam's library (is it a Steam game, installed?)"}
        try:
            if mode == "optiscaler":
                if sysfs.dry_run():
                    return {"mode": mode, "dry_run": True, "would": f"copy OptiScaler into {injector.find_game_exe_dir(game_dir)}"}
                r = self.injector.install_optiscaler(game_dir, bool(xess_fg))
            elif mode == "lsfg":
                # lsfg-vk is a Vulkan layer: nothing goes into the game folder; remove an OptiScaler install if any
                r = {"mode": "lsfg", **({"removed": self.injector.uninstall(game_dir)} if self.injector.state(game_dir)["optiscaler_present"] else {})}
            elif mode == "none":
                r = self.injector.uninstall(game_dir)
            else:
                return {"error": f"unknown mode {mode}"}
        except OSError as e:
            return {"error": str(e)}
        vals: dict = {"inject": mode}
        if mode == "lsfg":
            vals["lsfg"] = {**injector.LSFG_DEFAULTS, **(lsfg or {})}
        await self.set_launch(appid, vals)
        return r

    async def injector_download(self, kind: str, allow_unpinned: bool = False) -> dict:
        """Download OptiScaler ('optiscaler') or Intel's XeSS runtime DLLs ('xess') from their GitHub releases."""
        key = {"optiscaler": "optiscaler", "xess": "xess_sdk"}.get(kind)
        if key is None:
            return {"ok": False, "error": f"unknown asset {kind}"}
        try:
            got = downloads.fetch(self._versions()[key], os.path.join(RUNTIME_DIR, "downloads"), bool(allow_unpinned))
            return {"ok": True, "tag": got["tag"], **self.injector.stage(kind, got["dir"])}
        except Exception as e:   # network, archive, permission: report, never crash the backend
            return {"ok": False, "error": str(e)}

    async def injector_launch_env(self, mode: str) -> dict:
        return injector.Injector.launch_env(mode)

    # ---- XeSS driver (xess-xmx-linux) --------------------------------------------------------------------------------
    async def xess_state(self) -> dict:
        return xess.state(HOME)

    async def xess_install(self, allow_unpinned: bool = False) -> dict:
        try:
            return xess.install(HOME, self._versions()["xess_xmx_linux"], bool(allow_unpinned), sysfs.dry_run())
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def xess_probe(self) -> dict:
        return xess.probe(HOME)

    async def xess_switch_driver(self, version: str) -> dict:
        try:
            return xess.switch_driver(HOME, version)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    async def xess_uninstall(self) -> dict:
        return xess.uninstall(HOME)

    # ---- Proton ------------------------------------------------------------------------------------------------------
    async def list_proton(self) -> list:
        return proton.installed(HOME)

    # ---- benchmark ---------------------------------------------------------------------------------------------------
    async def bench_start(self, appid: str, name: str, label: str, duration_s: int = 120) -> dict:
        st = self.power.status()
        settings = {"tdp": (st.get("tdp") or {}).get("custom_tdp"), "tdp_mode": (st.get("tdp") or {}).get("mode"),
                    "cpu_max_pct": st["cpu"]["max_perf_pct"], "gpu_min_mhz": st["gpu"]["min_mhz"],
                    "gpu_max_mhz": st["gpu"]["max_mhz"], "plugged": self.battery.plugged(),
                    "launch": self.profiles.get_launch(appid)}
        return self.bench.start(appid, name, label, settings, int(duration_s))

    async def bench_status(self) -> dict:
        return self.bench.status()

    async def bench_stop(self) -> dict | None:
        return self.bench.stop()

    async def bench_results(self) -> list:
        return self.bench.results()

    async def bench_export(self) -> str:
        return _write_user_file(os.path.join(DESKTOP, f"clawdeck-benchmarks-{time.strftime('%Y%m%d-%H%M%S')}.csv"),
                                self.bench.export_csv())

    async def bench_clear(self) -> bool:
        self.bench.clear()
        return True

    # ---- HHD passthrough (read-only; writes go through the modules above) --------------------------------------------
    async def hhd_state(self) -> dict | None:
        return hhd.get_state()

