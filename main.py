"""ClawDeck - Decky Loader backend. Thin: every call maps to one module in py_modules/clawdeck.

Runs as root (plugin.json flag root) because sysfs, InputPlumber's config, /etc/firmware and the Steam library
need it. Battery rule: nothing here runs on a timer unless autotune is on for a running game (1 Hz, one asyncio
task) or thermald is paused while ClawDeck holds a TDP (the temperature guard: one file read every 10 s, §21).
Power-source changes and resume arrive as events from Steam (frontend listeners).

Scope: only what Bazzite and Steam's Quick Access do not already do. Four panels: Power (profile + autotune),
Per-game (XeSS on XMX: frame generation multiplier, FSR on XeSS, latest XeSS, low latency; OptiScaler presets for
Decky Framegen), Fixes (VRR range, face buttons, health), Setup (the XeSS driver, Decky Framegen, self-test, log
export). OptiScaler itself stays Decky Framegen's (Bazzite's Portal); ClawDeck installs it where it is missing.
"""
import asyncio
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "py_modules"))

# Decky Loader is a PyInstaller bundle: it exports LD_LIBRARY_PATH (and friends) pointing at its private libraries,
# and every program the plugin starts inherits that. On the Claw, rpm-ostree then loaded Decky's OpenSSL and died
# with "libsolv.so.1: undefined symbol: EVP_MD_CTX_dup" before writing the kernel arguments for the VRR fix.
# PyInstaller keeps the original value in *_ORIG; restore it (or drop the variable) once, here, so no child sees it.
for _v in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONPATH", "PYTHONHOME"):
    _orig = os.environ.pop(f"{_v}_ORIG", None)
    if _orig:
        os.environ[_v] = _orig
    else:
        os.environ.pop(_v, None)

import decky  # noqa: E402

from clawdeck import (autotune, battery, buttons, changes, downloads, edid, framegen, health, hw, launchenv,  # noqa: E402
                      gamedb, optiscaler, power, profiles, quietlog, reframework, stack, storage, sysfs, thermal, telemetry, userexec, xess, xmxscope)

PLUGIN_DIR = os.path.dirname(os.path.realpath(__file__))
SETTINGS_DIR = decky.DECKY_PLUGIN_SETTINGS_DIR
RUNTIME_DIR = decky.DECKY_PLUGIN_RUNTIME_DIR
LOG_DIR = decky.DECKY_PLUGIN_LOG_DIR
HOME = decky.DECKY_USER_HOME
DESKTOP = os.path.join(HOME, "Desktop")
VERSIONS = os.path.join(PLUGIN_DIR, "assets", "versions.json")
GAMES = os.path.join(PLUGIN_DIR, "assets", "games.json")
POWER_KEYS = ("tdp", "cpu_max_pct", "gpu_min_mhz", "gpu_max_mhz", "boost")
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
    userexec.write_new(path, text, 0o755 if path.endswith(".sh") else 0o644)
    for n in [path, *missing]:
        userexec.chown_tree(n, HOME)
    return path


class Plugin:
    # ---- lifecycle ---------------------------------------------------------------------------------------------------
    async def _main(self):
        self.settings = self._load_settings()
        if self.settings.pop("dry_run", None) is not None:   # Dry run was removed: drop the old setting
            self._save_settings()
        self.hw = hw.detect()
        self.power = power.Power(self.hw)
        self.battery = battery.Battery(self.hw)
        self.profiles = profiles.Profiles(os.path.join(SETTINGS_DIR, "profiles.json"))
        mangohud_dir = os.path.join(RUNTIME_DIR, "mangohud")
        # games run as the desktop user and MangoHud writes its frame-time log here: the folder must be theirs
        os.makedirs(mangohud_dir, exist_ok=True)
        userexec.chown_tree(mangohud_dir, HOME)
        self.autotune = autotune.Autotune(self.hw, self.power, mangohud_dir, csv_log=os.path.join(LOG_DIR, "autotune.csv"))
        self.autotune.enabled = bool(self.settings.get("autotune"))
        self._autotune_flag(self.autotune.enabled)
        self.running_appid: str | None = None
        self._health: tuple[float, list] | None = None
        self._power_lock = asyncio.Lock()      # one power write at a time: a fast profile change never interleaves
        self._install_lock = asyncio.Lock()    # one download/install at a time: two taps never race on one file
        self.guard = thermal.Guard(self.hw.paths.pkg_temp, self.hw.paths.pkg_temp_sensor)
        self._guard_task: asyncio.Task | None = None
        self.running_plugged: bool | None = None
        decky.logger.info("ClawDeck on %s (%s), kernel %s", self.hw.model["name"], self.hw.product, self.hw.kernel)
        self._install_wrapper()
        # XeSS on XMX per game: a session-wide driver file from an earlier install (ClawDeck 0.1, or install.sh run
        # by hand into ClawDeck's folder) moves aside, and Protons added since get the per-game scope
        try:
            if xmxscope.session_active(HOME) and (xess.package_dir(HOME) or "").startswith(os.path.join(HOME, xess.INSTALL_ROOT)):
                decky.logger.info("XeSS XMX per game: %s", xmxscope.isolate_session(HOME))
            if xmxscope.xmx_env(HOME):
                xmxscope.install_scope(HOME)
        except OSError as e:
            decky.logger.warning("XeSS XMX per game: %s", e)
        await self._first_fixes()
        if self.settings.get("overlay_power"):   # sysfs resets at boot: reapply the user's choice at every start
            r = telemetry.apply(True)
            decky.logger.info("watts in the overlay: %s", "on" if r["ok"] else r["error"])

    async def _first_fixes(self) -> None:
        """Fixes every Claw of that model needs, applied by themselves once (on install, and again after a system
        update undid them), unless the user removed them in Fixes: the face buttons on the Claw 8 EX, and the
        split-lock log flood where steamos-manager has no crash reporter. Each stays one tap to remove in Fixes."""
        auto = self.settings.setdefault("auto_fixes", {})
        jobs = []
        bs = buttons.status(self.hw.model_id)
        if bs["applies"] and not bs["ok"] and not bs["installed"] and auto.get("face_buttons") != "removed":
            jobs.append(("face_buttons", buttons.apply))
        ql = quietlog.status()
        if ql["applies"] and not ql["installed"] and auto.get("quiet_log") != "removed":
            jobs.append(("quiet_log", quietlog.apply))
        for key, fn in jobs:
            try:
                r = await self._bg(fn)
                auto[key] = "applied" if r.get("ok") else f"failed: {r.get('error') or r.get('log')}"
                decky.logger.info("first-run fix %s: %s", key, auto[key])
            except Exception as e:
                auto[key] = f"failed: {e}"
                decky.logger.warning("first-run fix %s: %s", key, e)
        if "xmx_every_game" not in auto and xmxscope.xmx_env(HOME):
            r = xmxscope.set_every_game(HOME, True)
            auto["xmx_every_game"] = "on" if r["ok"] else f"failed: {r.get('error')}"
            jobs.append(("xmx_every_game", None))
        if jobs:
            self._save_settings()

    async def _unload(self):
        self.autotune.stop()
        n = self.power.revert_all()
        decky.logger.info("ClawDeck unloaded, %d value(s) reverted", n)

    async def _uninstall(self):
        """Leave nothing behind that changes how the Claw or a game behaves (the face-button fix stays on purpose:
        without it the buttons are dead)."""
        self.power.revert_all()
        if glob.glob(f"{edid.EDID_DIR}/clawdeck-*.bin"):   # the wider VRR range: undone at the next restart
            decky.logger.info("removing the wider VRR range: %s", edid.disable())
        try:
            _write_user_file(WRAPPER, PASSTHROUGH)
            os.chmod(WRAPPER, 0o755)
        except OSError as e:
            decky.logger.warning("could not leave the pass-through wrapper: %s", e)
        for appid, folder in list((self.settings.get("opti_games") or {}).items()):
            try:
                decky.logger.info("uninstall: OptiScaler out of %s: %s, REFramework: %s", appid,
                                  optiscaler.unpatch(folder), reframework.remove(folder))
            except Exception as e:
                decky.logger.warning("uninstall: OptiScaler out of %s: %s", appid, e)
        # XeSS XMX back as xess-xmx-linux's installer left it (session-wide), the log and power counters as shipped
        for what, undo in (("Proton per-game XMX files", lambda: xmxscope.remove_scope(HOME)),
                           ("the session's XMX driver file", lambda: xmxscope.restore_session(HOME)),
                           ("the split-lock log setting", quietlog.remove),
                           ("the overlay's power counters", lambda: telemetry.apply(False))):
            try:
                decky.logger.info("uninstall: %s: %s", what, undo())
            except Exception as e:
                decky.logger.warning("uninstall: %s: %s", what, e)

    def _install_wrapper(self) -> None:
        try:
            with open(os.path.join(PLUGIN_DIR, "tools", "clawdeck-launch.sh"), "r", encoding="utf-8") as f:
                _write_user_file(WRAPPER, f.read())
            os.chmod(WRAPPER, 0o755)
        except OSError as e:
            decky.logger.warning("could not install the launch wrapper: %s", e)

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

    @staticmethod
    async def _bg(fn, *args):
        """Anything that touches the network, runs a program or walks a game folder runs off Decky's event loop, so
        the rest of ClawDeck (and the autotune loop) keeps answering while it works."""
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    # ---- setup / health ----------------------------------------------------------------------------------------------
    async def get_hardware(self) -> dict:
        return {**self.hw.to_dict(), "stack": stack.detect(), "other_plugins": stack.other_plugins(HOME)}

    async def get_health(self, force: bool = False) -> list:
        """The checks run programs and read files, so a result is reused for two minutes (the Quick Access panel asks
        every time it opens); Setup → Re-check and every fix ask for a fresh one."""
        now = time.monotonic()
        if not force and self._health and now - self._health[0] < 120:
            return self._health[1]
        result = await self._bg(health.checks, self.hw, HOME)
        self._health = (now, result)
        return result

    async def collect_logs(self) -> str:
        dest = os.path.join(DESKTOP, f"clawdeck-report-{time.strftime('%Y%m%d-%H%M%S')}.json")
        path = await self._bg(health.collect_logs, self.hw, HOME, dest)
        userexec.chown_tree(path, HOME)
        return path

    async def run_fix(self, fix: str, allow_unpinned: bool = False) -> dict:
        """The one-tap fixes the health check offers (Health.fix) and the Fixes panel's buttons."""
        self._health = None   # whatever the fix does, the next health check is fresh
        if fix == "enable_hhd":
            if stack.detect()["inputplumber"]:
                return {"ok": False, "error": "InputPlumber is running; starting Handheld Daemon too would make them fight over the controller"}
            u = userexec.user_of(HOME)
            if u is None:
                return {"ok": False, "error": "cannot tell which user to enable Handheld Daemon for"}
            r = await self._bg(lambda: subprocess.run(["systemctl", "enable", "--now", f"hhd@{u.pw_name}"],
                                                      capture_output=True, text=True, timeout=60, check=False))
            return {"ok": r.returncode == 0, "log": (r.stdout + r.stderr)[-2000:]}
        if fix == "install_xess":
            return await self.xess_install(allow_unpinned)
        if fix in ("fix_face_buttons", "undo_face_buttons", "quiet_split_lock_log", "undo_quiet_split_lock_log"):
            undo = fix.startswith("undo_")
            key = "face_buttons" if "face" in fix else "quiet_log"
            fn = {("face_buttons", False): buttons.apply, ("face_buttons", True): buttons.remove,
                  ("quiet_log", False): quietlog.apply, ("quiet_log", True): quietlog.remove}[(key, undo)]
            r = await self._bg(fn)
            if r.get("ok"):
                self.settings.setdefault("auto_fixes", {})[key] = "removed" if undo else "applied"
                self._save_settings()
            return r
        if fix in ("overlay_power_on", "overlay_power_off"):
            on = fix.endswith("_on")
            r = await self._bg(lambda: telemetry.apply(on))
            if r["ok"]:
                self.settings["overlay_power"] = on
                self._save_settings()
            return r
        if fix == "report_unknown_model":
            r = await self._bg(lambda: subprocess.run(["bash", os.path.join(PLUGIN_DIR, "tools", "sysfs-survey.sh")],
                                                      capture_output=True, text=True, timeout=60, check=False))
            path = _write_user_file(os.path.join(DESKTOP, f"clawdeck-survey-{time.strftime('%Y%m%d-%H%M%S')}.txt"),
                                    r.stdout + r.stderr)
            return {"ok": True, "path": path, "next": "open a GitHub issue 'Survey: <model>' and attach this file"}
        return {"ok": False, "error": f"unknown fix {fix}"}

    async def get_fixes(self) -> dict:
        """The Fixes panel in one call: the VRR range fix, the face-button fix, and the health check in one line."""
        vrr, checks = await asyncio.gather(self._bg(edid.status), self.get_health())
        return {"vrr": vrr, "buttons": buttons.status(self.hw.model_id), "health": health.summary(checks),
                "overlay_power": {**telemetry.status(), "battery_w": telemetry.battery_watts()},
                "quiet_log": {**quietlog.status(), "per_minute": await self._bg(quietlog.flood_rate)}}

    async def ui_log(self, msg: str) -> None:
        """A line from the panel (VRR switching), so it shows in ClawDeck's log next to the backend's."""
        decky.logger.info("ui: %s", str(msg)[:300])

    async def display_external(self) -> dict:
        """An external display connected? Read by the "VRR only in games" switch every few seconds (src/vrr.ts)."""
        return telemetry.external_display()

    async def get_setting(self, key: str):
        return self.settings.get(key)

    async def set_setting(self, key: str, value) -> bool:
        self.settings[key] = value
        self._save_settings()
        if key == "autotune":
            self.autotune.enabled = bool(value)
            self._autotune_flag(bool(value))
            if not value:
                self.autotune.pause("autotune switched off")
        return True

    @staticmethod
    def _autotune_flag(on: bool) -> None:
        """<runtime>/autotune.on tells the launch wrapper to ask the XeSS shim for XeLL's frame timing
        (IGDEXT_XELL_LOG=1), so autotune counts rendered frames, not generated ones. Read at the next game start."""
        flag = os.path.join(RUNTIME_DIR, "autotune.on")
        try:
            if on:
                os.makedirs(RUNTIME_DIR, exist_ok=True)
                with open(flag, "w", encoding="utf-8") as f:
                    f.write("1\n")
                os.chmod(flag, 0o644)
            elif os.path.exists(flag):
                os.remove(flag)
        except OSError as e:
            decky.logger.warning("autotune flag for the launch wrapper: %s", e)

    async def self_test(self) -> dict:
        """tools/selftest.py through the plugin: every control written, read back, measured and reverted. Never
        while a game runs (it would take the game's power away for a minute)."""
        if self.running_appid is not None:
            return {"ok": False, "error": "close the game first: the self-test changes and reverts the power limits"}
        script = os.path.join(PLUGIN_DIR, "tools", "selftest.py")
        try:
            r = await self._bg(lambda: subprocess.run([sys.executable, script], capture_output=True, text=True,
                                                      timeout=240, check=False, cwd=PLUGIN_DIR))
        except (OSError, subprocess.SubprocessError) as e:
            return {"ok": False, "error": f"could not run the self-test: {e}"}
        out = (r.stdout or "") + (r.stderr or "")
        lines = [ln.strip() for ln in out.splitlines() if ln.strip().startswith(("PASS", "FAIL"))]
        total = [ln for ln in out.splitlines() if ln.strip().endswith("failed") and "passed" in ln]
        failed = [ln for ln in lines if ln.startswith("FAIL")]
        self._health = None
        return {"ok": r.returncode == 0 and not failed, "passed": len(lines) - len(failed), "failed": len(failed),
                "lines": lines, "summary": total[-1].strip() if total else (f"exit {r.returncode}: " + out.strip().splitlines()[-1] if out.strip() else f"exit {r.returncode}"),
                "error": None if r.returncode == 0 and not failed else (" · ".join(failed) or out.strip()[-300:] or f"exit {r.returncode}")}

    # ---- power -------------------------------------------------------------------------------------------------------
    @staticmethod
    def _done(ok: bool, what: str, wanted, read, unit: str = "") -> dict:
        """What every single-lever write answers (HARDWARE-FINDINGS §14): the value asked for, the value read back
        from the hardware afterwards, and on failure the reason. The UI shows `summary` in its toast, `error` in red."""
        u = f" {unit}" if unit else ""
        fmt = lambda v: "?" if v is None else (("on" if v else "off") if isinstance(v, bool) else f"{v}{u}")
        err = None
        if not ok:
            err = sysfs.last_error() or f"{what} could not be set"
            summary = f"{what}: not set ({err})"
        elif read is not None and read != wanted:
            err = f"asked for {fmt(wanted)}, hardware reads {fmt(read)}"
            summary, ok = f"{what}: {err}", False
        else:
            summary = f"{what} set to {fmt(wanted)} → hardware reads {fmt(read)}"
        return {"ok": ok, "what": what, "wanted": wanted, "read": read, "unit": unit, "summary": summary, "error": err}

    async def get_power(self) -> dict:
        st = self.power.status()
        st["custom_tdp"] = self.settings.get("custom_tdp")
        st["autotune_floor"] = self.settings.get("autotune_floor")
        st["autotune_keep"] = self.settings.get("autotune_keep") or autotune.KEEP_PCT
        st["thermal"] = self.guard.status()
        return st

    def _limit_result(self, name: str, applied: dict, failed: list, plugged) -> dict:
        """Read back after a profile or a TDP-slider change. With autotune running the limit is its ceiling and the
        hardware reads what autotune uses under it, so that is what is checked."""
        want = applied.get("tdp")
        at_w = self.autotune.set_ceiling(want) if want is not None and self.autotune.running() else None
        expect = at_w if at_w is not None else want
        st = self.power.status()
        r = {"profile": name, "applied": applied, "failed": failed, "limit_w": want, "autotune_w": at_w,
             "read": {"tdp_w": st["tdp_w"], "pl2_w": st["rapl"]["pl2_w"], "cpu_max_pct": st["cpu"]["max_perf_pct"],
                      "epp": st["cpu"]["epp"], "backend": st["tdp_backend"]}}
        r["ok"] = not failed
        if r["ok"] and expect is not None and st["tdp_w"] is not None and st["tdp_w"] != expect:
            r["ok"], r["error"] = False, f"asked for {expect} W, hardware reads {st['tdp_w']} W"
        elif not r["ok"]:
            r["error"] = sysfs.last_error() or f"could not set: {', '.join(failed)}"
        else:
            r["error"] = None
        tail = f" (Autotune uses {at_w} W of it)" if at_w is not None and at_w != want else ""
        r["summary"] = f"{name} {want} W → {self._reads(st)}{tail}" if r["ok"] else f"{name}: {r['error']}"
        return r

    async def set_tdp(self, watts: int) -> dict:
        """The TDP slider (§17): a custom limit anywhere in the model's range, kept as the "custom" profile and used as
        autotune's ceiling."""
        async with self._power_lock:
            plugged = self.battery.plugged()
            want = self.power.clamp_tdp(int(watts), plugged)
            self.settings["last_profile"], self.settings["custom_tdp"] = "custom", want
            self._save_settings()
            sysfs.clear_error()
            self.guard.reset()                          # the user's new limit replaces the guard's step
            ok = self.power.set_tdp(want, plugged)
            self._ensure_guard()
            return self._limit_result("Custom", {"tdp": want} if ok else {}, [] if ok else ["tdp"], plugged)

    async def apply_profile(self, name: str, appid: str | None = None) -> dict:
        """Apply a profile and read the limits back (HARDWARE-FINDINGS §14): the UI shows `summary` in its toast
        and `error` in red; `read` is what the hardware reports after the write."""
        name = hw.profile_name(name)
        if name == "custom":
            return await self.set_tdp(int(self.settings.get("custom_tdp") or self.power.read_tdp() or 17))
        async with self._power_lock:
            plugged = self.battery.plugged()
            prof = self.profiles.get(appid, bool(plugged)) if appid else {}
            # a profile picked by hand wins over the game's saved TDP: its watts are what the user just chose
            overrides = {k: v for k, v in prof.items() if k in POWER_KEYS and k != "tdp"}
            self.settings["last_profile"] = name
            self._save_settings()
            sysfs.clear_error()
            self.guard.reset()
            r = self.power.apply_profile(name, overrides, plugged)
            self._ensure_guard()
            out = self._limit_result(name.capitalize(), r["applied"], r["failed"], plugged)
        return {**out, "profile": name}

    # ---- advanced levers (§19): Steam has none of these for this chip; each pauses autotune, never fights the user --
    async def set_cpu_turbo(self, on: bool) -> dict:
        sysfs.clear_error()
        self.autotune.pause("manual CPU boost")
        ok = self.power.set_cpu_turbo(bool(on))
        return self._done(ok, "CPU boost", bool(on), self.power.turbo())

    async def set_cpu_max_pct(self, pct: int) -> dict:
        sysfs.clear_error()
        self.autotune.pause("manual CPU ceiling")
        ok = self.power.set_cpu_max_pct(int(pct))
        return self._done(ok, "CPU ceiling", int(pct), self.power.status()["cpu"]["max_perf_pct"], "%")

    async def set_gpu_window(self, min_mhz, max_mhz) -> dict:
        sysfs.clear_error()
        self.autotune.pause("manual GPU clocks")
        lo = None if min_mhz is None else int(min_mhz)
        hi = None if max_mhz is None else int(max_mhz)
        ok = self.power.set_gpu_window(lo, hi)
        g = self.power.status()["gpu"]
        if hi is None:
            return self._done(ok, "GPU floor", lo, g["min_mhz"], "MHz")
        if lo is None:
            return self._done(ok, "GPU ceiling", hi, g["max_mhz"], "MHz")
        return self._done(ok, "GPU clocks", f"{lo}–{hi}", f"{g['min_mhz']}–{g['max_mhz']}", "MHz")

    async def set_epp(self, value: str) -> dict:
        sysfs.clear_error()
        self.autotune.pause("manual CPU energy mode")
        try:
            ok = self.power.set_epp(str(value))
        except ValueError as e:
            return {"ok": False, "what": "CPU energy mode", "wanted": value, "read": self.power.epp(), "unit": "",
                    "summary": f"CPU energy mode: {e}", "error": str(e)}
        return self._done(ok, "CPU energy mode", str(value), self.power.epp())

    async def set_gpu_power_profile(self, saving: bool) -> dict:
        """GPU efficiency mode (xe power_profile, findings §22.3): power_saving prefers the efficient clock."""
        sysfs.clear_error()
        self.autotune.pause("manual GPU efficiency mode")
        want = "power_saving" if saving else "base"
        try:
            ok = self.power.set_gpu_power_profile(want)
        except ValueError as e:
            return {"ok": False, "what": "GPU efficiency mode", "wanted": want, "read": self.power.gpu_power_profile()[0],
                    "unit": "", "summary": str(e), "error": str(e)}
        return self._done(ok, "GPU efficiency mode", want, self.power.gpu_power_profile()[0])

    async def set_autotune_floor(self, watts: int | None) -> dict:
        """The lowest TDP autotune may use; None = the model minimum."""
        lo, hi = hw.tdp_range(self.hw.model, True)
        w = None if watts is None else max(lo, min(int(watts), hi))
        self.settings["autotune_floor"] = w
        self._save_settings()
        self.autotune.set_floor(w if w is not None else lo)
        return {"ok": True, "floor_w": w if w is not None else lo, "summary": f"Autotune floor {w if w is not None else lo} W", "error": None}

    @staticmethod
    def _reads(st: dict) -> str:
        """'RAPL reads 17 W / 24 W': the sustained and short-term limits from the backend that enforces them."""
        who = {"rapl": "RAPL", "hhd": "Handheld Daemon", "msi-firmware": "MSI firmware"}.get(st["tdp_backend"] or "", "hardware")
        w = "?" if st["tdp_w"] is None else f"{st['tdp_w']:g}"
        pl2 = st["rapl"]["pl2_w"] if st["tdp_backend"] == "rapl" else None
        return f"{who} reads {w} W" + (f" / {pl2:g} W" if pl2 else "") + \
            (f", CPU {st['cpu']['max_perf_pct']}%" if st["cpu"]["max_perf_pct"] is not None else "")

    async def list_profiles(self) -> dict:
        return hw.profiles_for(self.hw.model_key)

    # ---- game lifecycle (called by the frontend's Steam hooks) ------------------------------------------------------
    def _start_game_power(self, appid: str, plugged: bool) -> dict:
        prof = self.profiles.get(appid, plugged)
        base = hw.profile_name(prof.get("base") or self.settings.get("last_profile") or ("performance" if plugged else "battery"))
        overrides = {k: v for k, v in prof.items() if k in POWER_KEYS}
        if base == "custom":     # the TDP slider's value is the limit; the rest of the levers stay as they are
            base, overrides = "", {**overrides, "tdp": overrides.get("tdp") or self.settings.get("custom_tdp")}
        self.guard.reset()
        result = self.power.apply_profile(base, overrides, plugged)
        # autotune is for battery: plugged in it only runs when the user asked for that too (Advanced)
        if self.settings.get("autotune") and prof.get("autotune", True) and (not plugged or self.settings.get("autotune_plugged")):
            # the picked limit (profile or slider) is the set TDP autotune measures at and works under; its floor is
            # the model minimum or the user's
            lo, hi = hw.tdp_range(self.hw.model, plugged)
            ceiling = result["applied"].get("tdp") or hi
            floor = max(lo, min(int(self.settings.get("autotune_floor") or lo), int(ceiling)))
            self.autotune.start(appid, prof.get("learned"), floor, int(ceiling), plugged,
                                keep_pct=self.settings.get("autotune_keep"),
                                xell_log=os.path.join(xess.prefix_dir(HOME, appid), "igdext_xell.log"))
            result["autotune"] = "started"
        self._ensure_guard()
        return result

    # ---- temperature guard (findings §21): only while thermald is paused --------------------------------------------
    def _floor_w(self) -> int:
        """The lowest TDP the guard may step to: autotune's floor while it runs, else the user's or the model's."""
        if self.autotune.session is not None:
            return self.autotune.session.floor_tdp
        lo, _ = hw.tdp_range(self.hw.model, self.battery.plugged())
        return max(lo, int(self.settings.get("autotune_floor") or lo))

    def _ensure_guard(self) -> None:
        """Start the guard's 10 s loop if ClawDeck now holds a TDP with thermald stopped (and it isn't running)."""
        if self.power.thermald_paused() and (self._guard_task is None or self._guard_task.done()):
            self._guard_task = asyncio.get_event_loop().create_task(self._guard_loop())

    async def _guard_loop(self) -> None:
        if not self.guard.path:
            decky.logger.warning("thermald is paused but no package temperature sensor was found: no temperature guard")
            return
        while self.power.thermald_paused():
            await asyncio.sleep(thermal.CHECK_S)
            try:
                await self._guard_check()
            except Exception as e:   # a sensor hiccup must never kill the guard
                decky.logger.exception("temperature guard: %s", e)
        self.guard.reset()          # thermald is back in charge

    async def _guard_check(self) -> int | None:
        async with self._power_lock:
            if not self.power.thermald_paused():
                return None
            if self.autotune.session is not None:
                return self.guard.check(self.autotune.session.learned.tdp, self._floor_w(), self.autotune.thermal_step)
            plugged = self.battery.plugged()
            return self.guard.check(self.power.read_tdp(), self._floor_w(), lambda w: self.power.set_tdp(w, plugged))

    def _save_learned(self, appid: str, plugged: bool) -> dict | None:
        learned = self.autotune.stop()
        if learned and learned.get("learned"):
            self.profiles.set(appid, plugged, {"learned": learned["learned"]})
        return learned

    async def game_started(self, appid: str) -> dict:
        appid = str(appid)
        if self.running_appid and self.running_appid != appid:
            # a second game (or a launcher) started while one runs: save what the first one learned before switching
            self._save_learned(self.running_appid, bool(self.running_plugged))
        plugged = bool(self.battery.plugged())
        self.running_appid, self.running_plugged = appid, plugged
        return self._start_game_power(appid, plugged)

    async def game_stopped(self, appid: str) -> dict:
        appid = str(appid)
        if self.running_appid is not None and appid != self.running_appid:
            # Steam reports every app that stops (launchers): only the game ClawDeck set up counts
            return {"ignored": True}
        plugged = self.running_plugged if self.running_plugged is not None else bool(self.battery.plugged())
        learned = self._save_learned(appid, plugged)
        self.running_appid, self.running_plugged = None, None
        n = self.power.revert_all()
        self.guard.reset()                          # thermald is back; its loop ends at its next check
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
        """After resume from suspend: firmware may have reset limits; re-apply the running game."""
        if self.running_appid is None:
            return {"reapplied": False}
        plugged = bool(self.battery.plugged())
        self._save_learned(self.running_appid, bool(self.running_plugged))
        self.running_plugged = plugged
        return {"reapplied": True, **self._start_game_power(self.running_appid, plugged)}

    async def get_autotune(self) -> dict:
        return {**self.autotune.status(), "thermal": self.guard.status()}

    async def set_autotune_keep(self, pct: float) -> dict:
        """How close to the most the set TDP gives autotune must stay (2-15 %, default 5): smaller = more frames,
        larger = more battery."""
        pct = max(2.0, min(float(pct), 15.0))
        self.settings["autotune_keep"] = pct
        self._save_settings()
        self.autotune.set_keep(pct)
        return {"ok": True, "keep_pct": pct, "summary": f"Autotune keeps within {pct:g} % of the most the TDP gives", "error": None}

    # ---- per-game launch environment (XeSS XMX, XeLL) --------------------------------------------------------------
    def _write_launch(self, appid: str) -> dict:
        try:
            return launchenv.write(RUNTIME_DIR, appid, self.profiles.get_launch(appid), not xmxscope.session_active(HOME))
        except ValueError as e:
            return {"error": str(e)}

    async def get_launch(self, appid: str) -> dict:
        launch = self.profiles.get_launch(appid)
        try:
            env, error = launchenv.compose(launch, not xmxscope.session_active(HOME)), None
        except ValueError as e:      # e.g. an imported profile with a value this version doesn't know
            env, error = {}, str(e)
        return {"launch": launch, "env": env, "wrapper": WRAPPER + " %command%", "error": error}

    async def set_launch_any(self, appid: str, values: dict) -> dict:
        """set_launch, and for a game with ClawDeck's OptiScaler its OptiScaler.ini follows (frame generation's
        multiplier, upscaler)."""
        r = await self.set_launch(appid, values)
        folder = (self.settings.get("opti_games") or {}).get(str(appid))
        if r.get("ok") and folder and optiscaler.owner(folder)["owner"] == "clawdeck":
            r2 = await self._opti_into(appid, folder, self.profiles.get_launch(appid))
            if not r2.get("ok"):
                r = {**r, "ok": False, "error": f"saved, but OptiScaler.ini wasn't updated: {r2.get('error')}"}
        return r

    async def set_launch(self, appid: str, values: dict) -> dict:
        allowed = launchenv.KEYS
        clean = {k: v for k, v in values.items() if k in allowed}
        try:   # a value the launch wrapper can't use must be refused here, not break get_launch later
            launchenv.compose({**self.profiles.get_launch(appid), **clean})
        except ValueError as e:
            return {"ok": False, "error": str(e), **(await self.get_launch(appid))}
        self.profiles.set_launch(appid, clean)
        return {"ok": True, **(await self.get_launch(appid)), "file": self._write_launch(appid)}

    async def game_status(self, appid: str) -> dict:
        """The Per-game panel in one call: the XeSS driver, what its low latency did in this game's last launch,
        OptiScaler in the game, and Decky Framegen."""
        launch = self.profiles.get_launch(appid)
        xs, lat, op, fg = await asyncio.gather(
            self._bg(xess.state, HOME), self._bg(xess.game_latency, HOME, appid, launch.get("low_latency") is not False),
            self._bg(xess.optiscaler_in, HOME, appid), self._bg(framegen.state, HOME))
        scope = await self._bg(xmxscope.state, HOME)
        d = op.get("dir")
        detected = await self._bg(xmxscope.ships_xess, d)
        auto = detected or launchenv.compose({**launch, "xmx": None}).get("CLAWDECK_XMX") == "1"
        return {"launch": launch, "xess": xs, "latency": lat, "optiscaler": op, "framegen": fg, "wrapper": WRAPPER + " %command%",
                "xmx_scope": {**scope, "auto": auto}, "drive": storage.game_drive(d), "fsr4_xmx": self._fsr4_xmx_files(),
                "opti_owner": (files := await self._bg(self._game_files, appid, xess.steam_install_dir(HOME, appid)))["owner"],
                "files": files, "opti_version": self._versions()["optiscaler"]["version"]}

    def _opti_owner(self, appid: str, d: str | None) -> str | None:
        return self._game_files(appid, d)["owner"]

    def _game_files(self, appid: str, d: str | None) -> dict:
        """What is in the game's folder right now: whose OptiScaler (and as which DLL, which preset), REFramework."""
        folder = self.settings.get("opti_games", {}).get(str(appid)) or (optiscaler.main_exe_dir(d) if d else None)
        who = optiscaler.owner(folder)
        return {**who, "folder": folder, "reframework": reframework.status(folder)["present"] if folder else False,
                "install_dir": d}

    @staticmethod
    def _fsr4_xmx_files() -> dict:
        """nitec's FSR 4.1.1 XMX build, where the launch wrapper takes it from (~/fsr4-xmx). Not downloaded by
        ClawDeck: it is shared in the OptiScaler Discord, and the user places it."""
        d = os.path.join(HOME, "fsr4-xmx")
        missing = [f for f in ("amd_fidelityfx_upscaler_dx12.dll", "fsr-original.dll", "cabinet.dll")
                   if not os.path.isfile(os.path.join(d, f))]
        return {"dir": d, "ready": not missing, "missing": missing}

    # ---- recommended setup per game (assets/games.json; what OptiClick does on Windows) --------------------------
    REC_KEYS = ("opti", "opti_xefg", "opti_fg", "opti_ini", "reframework", "fsr", "opti_dll")

    def _rec(self, appid: str) -> tuple[dict, str | None, str | None]:
        d = xess.steam_install_dir(HOME, appid)
        rec = gamedb.recommend(gamedb.load(GAMES), d)
        folder = optiscaler.main_exe_dir(d, rec["main_exes"]) if d else None
        return rec, d, folder

    async def games_list(self) -> list[dict]:
        """Installed Steam games for the Games page, with what ClawDeck has set up in each."""
        games = await self._bg(xess.installed_games, HOME)
        games = await self._bg(self._with_support, games)
        ours = self.settings.get("opti_games") or {}
        for g in games:
            launch = self.profiles.get_launch(g["appid"])
            g["setup"] = "optiscaler" if g["appid"] in ours else "launch" if launch else None
        return games

    def _with_support(self, games: list[dict]) -> list[dict]:
        """Which installed games ClawDeck can set up (the Game list shows only those, as OptiClick does). Walking a
        game folder is slow on an SD card, so the answer is kept per game until its folder changes."""
        path = os.path.join(RUNTIME_DIR, "support-cache.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except (OSError, ValueError):
            cache = {}
        doc = gamedb.load(GAMES)
        changed = False
        for g in games:
            try:
                stamp = int(os.stat(g["dir"]).st_mtime)
            except OSError:
                stamp = 0
            c = cache.get(g["appid"])
            if not c or c.get("stamp") != stamp:
                c = {**gamedb.support(doc, g["dir"]), "stamp": stamp}
                cache[g["appid"]] = c
                changed = True
            g["supported"], g["via"] = c["ok"], c["via"]
        if changed:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(cache, f)
            except OSError:
                pass
        return games

    async def game_recommend(self, appid: str) -> dict:
        rec, d, folder = await self._bg(self._rec, appid)
        launch = self.profiles.get_launch(appid)
        applied = bool(rec["launch"]) and all(launch.get(k) == v for k, v in rec["launch"].items())
        who = optiscaler.owner(folder)
        fsr4 = self._fsr4_xmx_files()
        dx12 = await self._bg(gamedb.is_dx12, d, rec["main_exes"]) if d else False
        return {**rec, "dir": d, "folder": folder, "applied": applied, "opti_owner": who["owner"],
                "fsr4": {"dx12": dx12, "files": fsr4["ready"], "missing": fsr4["missing"], "dir": fsr4["dir"]},
                "reframework_state": reframework.status(folder) if folder and rec["re_engine"] else None}

    async def _opti_into(self, appid: str, folder: str, launch: dict) -> dict:
        """ClawDeck's OptiScaler into the game with the settings of `launch` (preset, XeSS-FG, the game's fixes)."""
        entry = self._versions()["optiscaler"]
        if not optiscaler.installed(HOME, entry["version"]) or not all(
                os.path.isfile(os.path.join(optiscaler.package_dir(HOME, entry["version"]), x["dest"])) for x in entry.get("extras", [])):
            if self._install_lock.locked():
                return {"ok": False, "busy": True, "error": "already downloading; wait for the first one to finish"}
            async with self._install_lock:
                r = await self._bg(optiscaler.install, HOME, entry)
            if not r.get("ok"):
                return r
        opti = launch.get("opti") or "xess"
        opti = "fsr4_xmx" if opti == "fsr4" else opti
        r = await self._bg(optiscaler.patch, HOME, folder, entry["version"], launchenv.opti_ini({**launch, "opti": opti}),
                           opti, opti in launchenv.FSR4_XMX)
        if r.get("ok"):
            games = self.settings.setdefault("opti_games", {})
            games[str(appid)] = folder
            self._save_settings()
        return r

    async def game_apply_recommended(self, appid: str, allow_unpinned: bool = False) -> dict:
        """The recommended setup: launch settings, ClawDeck's OptiScaler in the game when the route needs it, and
        REFramework for an RE Engine game. The panel then adds ClawDeck's wrapper to the launch options."""
        rec, d, folder = await self._bg(self._rec, appid)
        if not d or not folder:
            return {"ok": False, "error": "can't find this game's install folder or its exe"}
        launch = dict(rec["launch"])
        if rec["optiscaler"]:
            who = optiscaler.owner(folder)
            if who["owner"] in ("decky-framegen", "other"):
                return {"ok": False, "owner": who["owner"], "error": (
                    "Decky Framegen has put OptiScaler in this game: remove it there (Decky Framegen → Unpatch), "
                    "then tap again" if who["owner"] == "decky-framegen" else
                    "this game already has OptiScaler from somewhere else (OptiClick's files from Windows, or put "
                    "there by hand): remove OptiScaler.ini and its DLL, then tap again")}
            r = await self._opti_into(appid, folder, launch)
            if not r.get("ok"):
                return r
            launch["opti_dll"] = r["dll_override"]
        if rec["re_engine"]:
            if self._install_lock.locked():
                return {"ok": False, "busy": True, "error": "already installing; wait for the first one to finish"}
            async with self._install_lock:
                r = await self._bg(reframework.install, HOME, folder, self._versions()["reframework"], bool(allow_unpinned))
            if not r.get("ok"):
                return r
        clean = {k: None for k in self.REC_KEYS}
        clean.update(launch)
        r = await self.set_launch(appid, clean)
        return {**r, "recommended": rec, "next": rec["reasons"][0]}

    async def game_choose(self, appid: str, upscaler: str, fg: int, allow_unpinned: bool = False) -> dict:
        """Per-game's two choices, as plain as OptiClick's: the upscaler (the game's own, or XeSS / FSR 4.1.1 /
        FSR 4.1.1 Lite on the XMX cores through ClawDeck's OptiScaler) and frame generation (off, or XeSS frame
        generation on XMX at 2x-4x, fed from the game's own frame generation when it has one). Everything they need
        (OptiScaler, REFramework for Capcom games, the game's fixes) is put in or taken out here."""
        if upscaler not in ("own", "xess", "fsr4_xmx", "fsr4_xmx_lite"):
            return {"ok": False, "error": f"unknown upscaler {upscaler!r}"}
        fg = int(fg or 0)
        if fg and not 2 <= fg <= 4:
            return {"ok": False, "error": "frame generation is 2x, 3x or 4x"}
        rec, d, folder = await self._bg(self._rec, appid)
        if not d or not folder:
            return {"ok": False, "error": "can't find this game's install folder or its exe"}
        if upscaler.startswith("fsr4_xmx") and not self._fsr4_xmx_files()["ready"]:
            f = self._fsr4_xmx_files()
            return {"ok": False, "error": f"FSR 4.1.1 on XMX needs nitec's files in {f['dir']} (missing: {', '.join(f['missing'])})"}
        if upscaler == "own":
            out = await self._bg(optiscaler.unpatch, folder)
            await self._bg(reframework.remove, folder)
            self.settings.setdefault("opti_games", {}).pop(str(appid), None)
            self._save_settings()
            r = await self.set_launch(appid, {**{k: None for k in self.REC_KEYS}, "fg": fg or None})
            return {**r, "optiscaler": out}
        who = optiscaler.owner(folder)
        if who["owner"] in ("decky-framegen", "other"):
            return {"ok": False, "owner": who["owner"], "error": "this game has OptiScaler from " + (
                "Decky Framegen: remove it there (Decky Framegen → Unpatch), then pick again" if who["owner"] == "decky-framegen"
                else "somewhere else (OptiClick's files from Windows, or by hand): remove OptiScaler.ini and its DLL, then pick again")}
        launch = {"opti": upscaler, "opti_fg": rec["fg_source"] if fg else "off", "opti_ini": rec["ini"] if rec["optiscaler"] else {},
                  "fg": fg or None, "reframework": True if rec["re_engine"] else None, "fsr": None, "opti_xefg": None}
        r = await self._opti_into(appid, folder, {k: v for k, v in launch.items() if v is not None})
        if not r.get("ok"):
            return r
        launch["opti_dll"] = r["dll_override"]
        if rec["re_engine"]:
            if self._install_lock.locked():
                return {"ok": False, "busy": True, "error": "already installing; wait for the first one to finish"}
            async with self._install_lock:
                rf = await self._bg(reframework.install, HOME, folder, self._versions()["reframework"], bool(allow_unpinned))
            if not rf.get("ok"):
                return rf
        r = await self.set_launch(appid, launch)
        return {**r, "next": rec["reasons"][0]}

    async def game_set_opti(self, appid: str, values: dict) -> dict:
        """Per-game's OptiScaler upscaler / XeSS-FG choice for a game with ClawDeck's OptiScaler: saved, and the
        game's OptiScaler.ini (and FSR 4 XMX files) rewritten at once; takes effect from the next launch."""
        rec, d, folder = await self._bg(self._rec, appid)
        if optiscaler.owner(folder)["owner"] != "clawdeck":
            return await self.set_launch(appid, values)                  # Decky Framegen: its env variables
        launch = {**self.profiles.get_launch(appid), **{k: v for k, v in values.items() if k in ("opti", "opti_xefg", "opti_fg")}}
        launch = {k: v for k, v in launch.items() if v is not None}
        r = await self._opti_into(appid, folder, launch)
        if not r.get("ok"):
            return r
        return await self.set_launch(appid, {**values, "opti_dll": r["dll_override"]})

    async def game_undo_recommended(self, appid: str) -> dict:
        rec, d, folder = await self._bg(self._rec, appid)
        removed = await self._bg(reframework.remove, folder) if folder else {"ok": True}
        opti = await self._bg(optiscaler.unpatch, folder) if folder else {"ok": True}
        self.settings.setdefault("opti_games", {}).pop(str(appid), None)
        self._save_settings()
        r = await self.set_launch(appid, {k: None for k in self.REC_KEYS})
        return {**r, "reframework": removed, "optiscaler": opti, "ok": r.get("ok", True) and opti.get("ok", True)}

    # ---- Decky Framegen (OptiScaler per game) ---------------------------------------------------------------------
    async def set_xmx_every_game(self, on: bool) -> dict:
        r = await self._bg(xmxscope.set_every_game, HOME, bool(on))
        self.settings.setdefault("auto_fixes", {})["xmx_every_game"] = "on" if on else "off"
        self._save_settings()
        return r

    async def optiscaler_state(self) -> dict:
        """Setup's OptiScaler row: ClawDeck's own (downloaded on the first recommended setup), the games it is in,
        and whether Decky Framegen is installed too (its games stay its own)."""
        e = self._versions()["optiscaler"]
        fg = await self._bg(framegen.state, HOME)
        return {"version": e["version"], "installed": optiscaler.installed(HOME, e["version"]),
                "games": len(self.settings.get("opti_games") or {}), "framegen": bool(fg.get("installed")),
                "framegen_version": fg.get("version")}

    async def optiscaler_install(self) -> dict:
        if self._install_lock.locked():
            return {"ok": False, "busy": True, "error": "already downloading; wait for the first one to finish"}
        async with self._install_lock:
            return await self._bg(optiscaler.install, HOME, self._versions()["optiscaler"])

    async def framegen_state(self) -> dict:
        return await self._bg(framegen.state, HOME)

    async def framegen_install(self, allow_unpinned: bool = False) -> dict:
        if self._install_lock.locked():      # a second tap while the first download runs (seen 2026-11-04: they broke each other)
            return {"ok": False, "busy": True, "error": "already installing; wait for the first one to finish"}
        async with self._install_lock:
            try:
                r = await self._bg(framegen.install, HOME, self._versions()["decky_framegen"], bool(allow_unpinned))
            except Exception as e:
                r = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if not r.get("ok"):
            decky.logger.warning("Decky Framegen install failed: %s", r.get("error"))
        return r

    # ---- what changed since last start --------------------------------------------------------------------------
    async def get_changes(self) -> dict:
        """Compared with the last start: what the system update changed, and which checks fail now."""
        loop = asyncio.get_running_loop()
        snap = await loop.run_in_executor(None, changes.snapshot, HOME, stack.detect(), getattr(decky, "DECKY_VERSION", None))
        r = changes.check(os.path.join(SETTINGS_DIR, "system-snapshot.json"), snap)
        if r["changed"]:
            checks = await loop.run_in_executor(None, health.checks, self.hw, HOME)
            r["failing"] = [{"title": c["title"], "detail": c["detail"]} for c in checks if not c["ok"] and not c.get("optional")]
        return r

    # ---- XeSS driver (xess-xmx-linux) --------------------------------------------------------------------------------
    async def xess_state(self) -> dict:
        """The driver's state, plus the tested release ClawDeck would install: Setup shows "update available" when
        the installed one is older (a ClawDeck update moves the pin after the new release is tested)."""
        st = await self._bg(xess.state, HOME)
        pinned = (self._versions().get("xess_xmx_linux") or {}).get("tag")
        have = (st.get("source") or {}).get("tag") or st.get("version")
        st["pinned"] = pinned if pinned and pinned != "pending-verification" else None
        st["update"] = bool(st.get("installed") and st["pinned"] and have and str(have) < str(st["pinned"]))
        return st

    async def xess_install(self, allow_unpinned: bool = False) -> dict:
        self._health = None
        if self._install_lock.locked():
            return {"ok": False, "busy": True, "error": "already installing; wait for the first one to finish"}
        async with self._install_lock:
            try:
                r = await self._bg(xess.install, HOME, self._versions()["xess_xmx_linux"], bool(allow_unpinned))
            except Exception as e:
                r = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if not r.get("ok"):
            decky.logger.warning("XeSS XMX install failed: %s", r.get("error"))
        return r

    # ---- the VRR range fix (ClawLab's EDID, Fixes panel) -------------------------------------------------------------
    async def set_wide_vrr(self, on: bool) -> dict:
        """30-120 Hz VRR on the Claw's own screen (ClawLab's EDID fix). Rebuilds the initramfs: a minute or two."""
        fn = edid.enable if on else edid.disable
        r = await self._bg(fn)
        decky.logger.info("wide VRR %s: %s", "install" if on else "remove", r)
        return r
