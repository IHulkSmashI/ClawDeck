"""ClawDeck - Decky Loader backend. Thin: every call maps to one module in py_modules/clawdeck.

Runs as root (plugin.json flag _root) because sysfs, Handheld Daemon's socket and the Steam library need it.
Battery rule: nothing here runs on a timer unless autotune is on for a running game (1 Hz, one asyncio task).
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "py_modules"))

import decky  # noqa: E402

from clawdeck import autotune, battery, health, hhd, hw, injector, power, profiles, sysfs  # noqa: E402

SETTINGS_DIR = decky.DECKY_PLUGIN_SETTINGS_DIR
RUNTIME_DIR = decky.DECKY_PLUGIN_RUNTIME_DIR
LOG_DIR = decky.DECKY_PLUGIN_LOG_DIR
HOME = decky.DECKY_USER_HOME


class Plugin:
    # ---- lifecycle ---------------------------------------------------------------------------------------------------
    async def _main(self):
        sysfs.set_dry_run(os.environ.get("CLAWDECK_DRY_RUN", "0") not in ("", "0"))
        self.hw = hw.detect()
        self.power = power.Power(self.hw)
        self.battery = battery.Battery(self.hw)
        self.profiles = profiles.Profiles(os.path.join(SETTINGS_DIR, "profiles.json"))
        self.injector = injector.Injector(os.path.join(RUNTIME_DIR, "assets"))
        self.autotune = autotune.Autotune(self.hw, self.power, os.path.join(RUNTIME_DIR, "mangohud"),
                                          csv_log=os.path.join(LOG_DIR, "autotune.csv"))
        self.settings = self._load_settings()
        decky.logger.info("ClawDeck on %s (%s), kernel %s, dry-run %s", self.hw.model["name"], self.hw.product,
                          self.hw.kernel, sysfs.dry_run())
        # the one setting applied at every start on purpose: the charge limit (sysfs resets at boot)
        lim = self.settings.get("charge_limit")
        if lim and self.battery.supported():
            self.battery.set_charge_limit(int(lim))

    async def _unload(self):
        self.autotune.stop()
        n = sysfs.revert_all()
        decky.logger.info("ClawDeck unloaded, %d sysfs value(s) reverted", n)

    async def _uninstall(self):
        pass

    def _load_settings(self) -> dict:
        import json
        try:
            with open(os.path.join(SETTINGS_DIR, "settings.json"), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_settings(self) -> None:
        import json
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(os.path.join(SETTINGS_DIR, "settings.json"), "w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=2)

    # ---- setup / health ----------------------------------------------------------------------------------------------
    async def get_hardware(self) -> dict:
        return self.hw.to_dict()

    async def get_health(self) -> list:
        return health.checks(self.hw, HOME)

    async def collect_logs(self) -> str:
        dest = os.path.join(HOME, "Desktop", f"clawdeck-report-{time.strftime('%Y%m%d-%H%M%S')}.json")
        return health.collect_logs(self.hw, HOME, dest)

    async def get_setting(self, key: str):
        return self.settings.get(key)

    async def set_setting(self, key: str, value) -> bool:
        self.settings[key] = value
        self._save_settings()
        return True

    async def set_dry_run(self, on: bool) -> bool:
        sysfs.set_dry_run(bool(on))
        return sysfs.dry_run()

    # ---- power -------------------------------------------------------------------------------------------------------
    async def get_power(self) -> dict:
        return self.power.status()

    async def set_tdp(self, watts: int) -> bool:
        return self.power.set_tdp(int(watts), self.battery.plugged())

    async def set_cpu_max_pct(self, pct: int) -> bool:
        return self.power.set_cpu_max_pct(int(pct))

    async def set_cpu_turbo(self, on: bool) -> bool:
        return self.power.set_cpu_turbo(bool(on))

    async def set_gpu_window(self, min_mhz, max_mhz) -> bool:
        return self.power.set_gpu_window(None if min_mhz is None else int(min_mhz), None if max_mhz is None else int(max_mhz))

    async def reset_gpu_window(self) -> bool:
        return self.power.reset_gpu_window()

    async def apply_profile(self, name: str, appid: str | None = None) -> dict:
        plugged = self.battery.plugged()
        overrides = self.profiles.get(appid, bool(plugged)) if appid else {}
        overrides.pop("base", None)
        return self.power.apply_profile(name, overrides, plugged)

    async def revert_power(self) -> int:
        return self.power.revert_all()

    async def list_profiles(self) -> dict:
        return hw.profiles_for(self.hw.product)

    # ---- per-game profiles -------------------------------------------------------------------------------------------
    async def get_game_profile(self, appid: str) -> dict:
        return {"battery": self.profiles.get(appid, False), "plugged": self.profiles.get(appid, True)}

    async def set_game_profile(self, appid: str, plugged: bool, values: dict) -> dict:
        return self.profiles.set(appid, bool(plugged), values)

    async def export_profiles(self, appid: str | None = None) -> str:
        return self.profiles.export_json(appid)

    async def import_profiles(self, text: str, overwrite: bool = False) -> int:
        return self.profiles.import_json(text, bool(overwrite))

    # ---- game lifecycle (called by the frontend's Steam hooks) ------------------------------------------------------
    async def game_started(self, appid: str) -> dict:
        plugged = bool(self.battery.plugged())
        prof = self.profiles.get(appid, plugged)
        base = prof.get("base", "plugged" if plugged else "battery")
        overrides = {k: v for k, v in prof.items() if k in ("tdp", "cpu_max_pct", "gpu_min_mhz", "gpu_max_mhz")}
        result = self.power.apply_profile(base, overrides, plugged)
        if self.settings.get("autotune") and prof.get("autotune", True) and prof.get("target_fps"):
            lim = self.hw.model["tdp"]
            self.autotune.start(appid, int(prof["target_fps"]), prof.get("learned"), lim["min"],
                                lim["plugged_max"] if plugged else lim["battery_max"])
            result["autotune"] = "started"
        return result

    async def game_stopped(self, appid: str) -> dict:
        learned = self.autotune.stop()
        if learned and learned.get("learned"):
            plugged = bool(self.battery.plugged())
            self.profiles.set(appid, plugged, {"learned": learned["learned"]})
        n = self.power.revert_all()
        return {"reverted": n, "learned": learned}

    async def get_autotune(self) -> dict:
        return self.autotune.status()

    # ---- battery -----------------------------------------------------------------------------------------------------
    async def get_battery(self) -> dict:
        return self.battery.status()

    async def set_charge_limit(self, percent: int) -> bool:
        ok = self.battery.set_charge_limit(int(percent))
        if ok:
            self.settings["charge_limit"] = int(percent)
            self._save_settings()
        return ok

    # ---- XMX injector ------------------------------------------------------------------------------------------------
    async def injector_state(self, game_dir: str) -> dict:
        return self.injector.state(game_dir)

    async def injector_install(self, game_dir: str, mode: str, xess_fg: bool = True) -> dict:
        if mode == "optiscaler":
            return self.injector.install_optiscaler(game_dir, bool(xess_fg))
        if mode == "none":
            return self.injector.uninstall(game_dir)
        return {"error": f"mode {mode} not implemented yet"}

    async def injector_launch_env(self, mode: str) -> dict:
        return injector.Injector.launch_env(mode)

    # ---- HHD passthrough (read-only; writes go through the modules above) --------------------------------------------
    async def hhd_state(self) -> dict | None:
        return hhd.get_state()
