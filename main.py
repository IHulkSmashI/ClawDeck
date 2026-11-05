"""ClawDeck - Decky Loader backend. Thin: every call maps to one module in py_modules/clawdeck.

Runs as root (plugin.json flag root) because sysfs, InputPlumber's config, /etc/firmware and the Steam library
need it. Battery rule: nothing here runs on a timer unless autotune is on for a running game (1 Hz, one asyncio
task) or thermald is paused while ClawDeck holds a TDP (the temperature guard: one file read every 10 s, §21).
Power-source changes and resume arrive as events from Steam (frontend listeners).

Scope: only what Bazzite and Steam's Quick Access do not already do. Four panels: Power (profile + autotune),
Per-game (XeSS on XMX: frame generation multiplier, FSR on XeSS, latest XeSS, low latency; ClawDeck's own pinned
OptiScaler per game with the recommended setup), Fixes (VRR range, face buttons, health), Setup (the XeSS driver,
ClawDeck's OptiScaler, self-test, log export). Decky Framegen is compatibility only: a game it patched is detected
and left alone (framegen.py, optiscaler.owner).
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

from clawdeck import (autotune, quiet, osupdate, battery, buttons, changes, downloads, edid, framegen, health, hw, launchenv,  # noqa: E402
                      fan, gamedb, livedb, optiscaler, power, profiles, quietlog, reframework, stack, storage, sysfs, thermal, telemetry, userexec, xess, xmxscope)

PLUGIN_DIR = os.path.dirname(os.path.realpath(__file__))
SETTINGS_DIR = decky.DECKY_PLUGIN_SETTINGS_DIR
RUNTIME_DIR = decky.DECKY_PLUGIN_RUNTIME_DIR
LOG_DIR = decky.DECKY_PLUGIN_LOG_DIR
HOME = decky.DECKY_USER_HOME
DESKTOP = os.path.join(HOME, "Desktop")
VERSIONS = os.path.join(PLUGIN_DIR, "assets", "versions.json")
GAMES = os.path.join(PLUGIN_DIR, "assets", "games.json")
# the per-game lists fetched at start (livedb): ClawDeck's games.json from main, OptiClick's Supported Game List
LISTS_DIR = os.path.join(HOME, ".local", "share", "clawdeck", "lists")
POWER_KEYS = ("tdp", "cpu_max_pct", "gpu_min_mhz", "gpu_max_mhz", "boost")
# The launch wrapper lives outside the plugin folder: Steam's launch options keep pointing at it after an uninstall
# deletes the plugin, so it must stay there (as a pass-through) or those games would no longer start.
WRAPPER = os.path.join(HOME, ".local", "share", "clawdeck", "clawdeck-launch.sh")
# ClawDeck's cabinet.dll (tools/cabinet-lzms) next to the wrapper, where its Decky Framegen stage takes it from for
# FSR 4 on XMX (the wrapper doesn't know the plugin's folder). Copied at every start, removed by an uninstall.
WRAPPER_CABINET = os.path.join(HOME, ".local", "share", "clawdeck", "cabinet.dll")
PASSTHROUGH = "#!/bin/bash\n# ClawDeck was removed; this only starts the game. Delete it from the game's launch options when you like.\nexec \"$@\"\n"


def _write_user_file(path: str, text: str | bytes) -> str:
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
        # the per-game "XeSS on XMX" off switch is gone (maintainer, 2026-11-05: "the whole point of the Intel driver is
        # to use XMX cores when you can"): a game switched off earlier goes back to automatic
        for appid in self.profiles.games():
            if self.profiles.get_launch(appid).get("xmx") is False:
                self.profiles.set_launch(appid, {"xmx": None})
                decky.logger.info("game %s: XMX switch-off dropped (the switch was removed)", appid)
        mangohud_dir = os.path.join(RUNTIME_DIR, "mangohud")
        # games run as the desktop user and MangoHud writes its frame-time log here: the folder must be theirs
        os.makedirs(mangohud_dir, exist_ok=True)
        userexec.chown_tree(mangohud_dir, HOME)
        self.autotune = autotune.Autotune(self.hw, self.power, mangohud_dir, csv_log=os.path.join(LOG_DIR, "autotune.csv"))
        # on by default, on the charger too: the least power for (nearly) the same frames, less heat and fan noise
        # (maintainer, 2026-11-04). The switches in Power still turn either off.
        # beta: off until switched on (ClawTweaks removed its AutoTDP in 2026-07; Steam has none; maintainer
        # 2026-11-05: "Auto tdp can be released in beta or turned off")
        self.settings.setdefault("autotune", False)
        self.settings.setdefault("autotune_plugged", True)
        self.autotune.enabled = bool(self.settings.get("autotune"))
        self._autotune_flag(self.autotune.enabled)
        self.running_appid: str | None = None
        self._health: tuple[float, list] | None = None
        # is a Bazzite update waiting (kernel, Intel graphics driver, Mesa, gamescope)? Once per start, off the event loop
        asyncio.get_event_loop().run_in_executor(None, osupdate.refresh)
        # the per-game lists, kept current (OptiClick reads its database at every start too): ClawDeck's, OptiClick's
        # supported list, OptiScaler's Compatibility List; offline: the copies from last time, else the plugin's
        asyncio.get_event_loop().run_in_executor(None, livedb.refresh, LISTS_DIR, GAMES)
        self._power_lock = asyncio.Lock()      # one power write at a time: a fast profile change never interleaves
        self._install_lock = asyncio.Lock()    # one download/install at a time: two taps never race on one file
        self.guard = thermal.Guard(self.hw.paths.pkg_temp, self.hw.paths.pkg_temp_sensor)
        self._guard_task: asyncio.Task | None = None
        self.running_plugged: bool | None = None
        self._holding_pick = False             # no game runs and ClawDeck holds the user's pick (menus) on the chip
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
        self.fan_hw = fan.find_hwmon()
        self._reapply_fan_charge("start")
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
        quiet.background(False)
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
        try:
            os.remove(WRAPPER_CABINET)
        except FileNotFoundError:
            pass
        except OSError as e:
            decky.logger.warning("could not remove %s: %s", WRAPPER_CABINET, e)
        for appid, folder in list((self.settings.get("opti_games") or {}).items()):
            try:
                decky.logger.info("uninstall: OptiScaler out of %s: %s, REFramework: %s", appid,
                                  optiscaler.unpatch(folder), reframework.remove(folder, self._ref_shas()))
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
        try:
            if os.path.isfile(optiscaler.OWN_CABINET):
                with open(optiscaler.OWN_CABINET, "rb") as f:
                    _write_user_file(WRAPPER_CABINET, f.read())
        except OSError as e:
            decky.logger.warning("could not put ClawDeck's cabinet.dll next to the launch wrapper: %s", e)

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
        vrr = {**vrr, "idle_hold": await self._bg(edid.gamescope_idle_hold)}
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
        # your limit (the pick) and what Autotune runs under it now: the slider shows the first, the status line both
        s = self.autotune.session
        st["limit_w"] = s.ceiling_tdp if s is not None else None
        st["autotune_w"] = s.learned.tdp if s is not None and not s.paused else None
        st["autotune_floor"] = self.settings.get("autotune_floor")
        st["autotune_keep"] = self.settings.get("autotune_keep") or autotune.KEEP_PCT
        st["thermal"] = self.guard.status()
        slot = "plugged" if self.battery.plugged() else "battery"
        st["pl2_manual"] = bool(self.settings.get("pl2_manual"))
        st["pl2_set_w"] = self.settings.get(f"pl2_w_{slot}")
        st["pl2_max"] = self.hw.model["tdp"].get("sppt_max", 45)
        return st

    def _limit_result(self, name: str, applied: dict, failed: list, plugged) -> dict:
        """Read back after a profile or a TDP-slider change. With autotune running the limit is its ceiling and the
        hardware reads what autotune uses under it, so that is what is checked (right after a pick autotune starts
        over at the new limit, so the two are the same)."""
        want = applied.get("tdp")
        s = self.autotune.session
        at_w = s.learned.tdp if want is not None and s is not None else None
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
            slot = "plugged" if plugged else "battery"
            self.settings[f"last_profile_{slot}"], self.settings[f"custom_tdp_{slot}"] = "custom", want
            self._save_settings()
            if self.running_appid:                      # the game remembers it for this power source
                self.profiles.set(self.running_appid, bool(plugged), {"base": "custom", "tdp": want, "learned": None})
            sysfs.clear_error()
            r = self._apply_pick_now(bool(plugged))     # the guard's step and autotune's learned TDP start over
            ok = r.get("applied", {}).get("tdp") is not None
            return self._limit_result("Custom", {"tdp": r["applied"]["tdp"]} if ok else {}, [] if ok else ["tdp"], plugged)

    def _pl2_for(self, pk: dict, plugged: bool) -> None:
        """PL2 by hand (ClawTweaks' switch): only with the Custom profile, per power source; otherwise PL1 + 2 W."""
        slot = "plugged" if plugged else "battery"
        w = self.settings.get(f"pl2_w_{slot}") if self.settings.get("pl2_manual") and pk.get("profile") == "custom" else None
        self.power.pl2_w = int(w) if w else None

    async def set_pl2(self, manual: bool, watts: int | None = None) -> dict:
        """Power → Custom: "Set PL2 yourself" on (with its watts, for this power source) or off (PL2 = PL1 + 2 W, as
        Handheld Daemon / PowerControl / PowerStation do). Applied at once to the current pick."""
        async with self._power_lock:
            plugged = bool(self.battery.plugged())
            slot = "plugged" if plugged else "battery"
            self.settings["pl2_manual"] = bool(manual)
            if watts is not None:
                hi = self.hw.model["tdp"].get("sppt_max", 45)
                self.settings[f"pl2_w_{slot}"] = max(self.hw.model["tdp"]["min"], min(int(watts), hi))
            self._save_settings()
            sysfs.clear_error()
            r = self._apply_pick_now(plugged)
            st = self.power.status()
            got = st.get("pl2_w")
            want = self.power.pl2_w
            ok = r.get("applied", {}).get("tdp") is not None and (want is None or got is None or round(got) == want or
                                                                   round(got) == max(r["applied"]["tdp"], want))
            line = (f"PL2 {round(got)} W" if got is not None else "PL2 set") + (" (yours)" if want is not None else " (PL1 + 2 W)")
            return {"ok": ok, "manual": bool(manual), "pl2_w": want, "read_w": got, "summary": line,
                    "error": None if ok else (sysfs.last_error() or "the chip didn't take that PL2")}

    async def power_slot(self, appid: str | None = None) -> dict:
        """What the running game remembers for this power source (Power's "Saved for <game>" line), and `pick`: the
        limit that applies now on this power source (the game's own pick, else your last pick on it, else the default),
        which is what the profile list and the slider show. The game ClawDeck set up wins over the UI's guess."""
        gid = str(self.running_appid or appid or "")
        plugged = bool(self.battery.plugged())
        own = self.profiles.own(gid, plugged) if gid else {}
        pk = self._pick(gid or None, plugged)
        return {"appid": gid or None, "plugged": plugged, "base": own.get("base"), "tdp": own.get("tdp"),
                "running": self.running_appid is not None,
                "pick": {"profile": pk["profile"], "tdp": pk["tdp"], "from": pk["from"]}}

    async def reset_power_slot(self, appid: str | None = None) -> dict:
        """Forget this game's pick for this power source: it follows the last pick for all games again."""
        gid = str(appid or self.running_appid or "")
        if not gid:
            return {"ok": False, "error": "no game running"}
        plugged = bool(self.battery.plugged())
        self.profiles.set(gid, plugged, {"base": None, "tdp": None, "learned": None})
        r = {"ok": True}
        if self.running_appid == gid:
            async with self._power_lock:
                r = {"ok": True, **self._restart_game_power(gid, plugged)}
        return r

    async def apply_profile(self, name: str, appid: str | None = None) -> dict:
        """Apply a profile and read the limits back (HARDWARE-FINDINGS §14): the UI shows `summary` in its toast
        and `error` in red; `read` is what the hardware reports after the write."""
        name = hw.profile_name(name)
        if name == "custom":
            slot = "plugged" if self.battery.plugged() else "battery"
            return await self.set_tdp(int(self.settings.get(f"custom_tdp_{slot}") or self.settings.get("custom_tdp")
                                          or self.power.read_tdp() or 17))
        async with self._power_lock:
            plugged = self.battery.plugged()
            self.settings["last_profile"] = name
            self.settings[f"last_profile_{'plugged' if plugged else 'battery'}"] = name
            self._save_settings()
            gid = self.running_appid or appid
            if gid:                                     # the game remembers it for this power source
                self.profiles.set(gid, bool(plugged), {"base": name, "tdp": None, "learned": None})
            sysfs.clear_error()
            # in a game: the game starts over from the pick (autotune measures again at its watts, nothing learned at
            # the old limit pulls it down); else the pick goes on the chip and stays after games too
            r = self._apply_pick_now(bool(plugged))
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
    def _pick(self, appid: str | None, plugged: bool) -> dict:
        """The limit the user picked for this power source, as the menus show it and the chip gets it: the game's own
        pick (saved when it was made while the game ran), else the last pick on this power source for any game, else
        the default (Battery / Performance). The default slot's base used to win every time, so a game started on
        battery always came back at Battery 15 W, whatever was picked in it (maintainer, 2026-11-04).
        Returns profile (a name or "custom"), tdp (the watts it sets here), from (game / last / default), and what
        power.apply_profile needs (apply, overrides) plus the merged profile (prof: autotune, learned)."""
        slot = "plugged" if plugged else "battery"
        own = self.profiles.own(appid, plugged) if appid else {}
        prof = self.profiles.get(appid or "", plugged)
        if own.get("base"):
            base, src = own["base"], "game"
        elif self.settings.get(f"last_profile_{slot}"):
            base, src = self.settings[f"last_profile_{slot}"], "last"
        else:
            base, src = prof.get("base") or ("performance" if plugged else "battery"), "default"
        base = hw.profile_name(base)
        if src != "game" and base == "custom" and self.settings.get(f"custom_tdp_{slot}"):
            prof = {**prof, "tdp": self.settings.get(f"custom_tdp_{slot}")}
        overrides = {k: v for k, v in prof.items() if k in POWER_KEYS}
        apply = base
        if base == "custom":     # the TDP slider's value is the limit; the rest of the levers stay as they are
            apply, overrides = "", {**overrides, "tdp": overrides.get("tdp") or self.settings.get("custom_tdp")}
        w = overrides.get("tdp") or hw.profiles_for(self.hw.model_key).get(base, {}).get("tdp")
        return {"profile": base, "tdp": None if w is None else self.power.clamp_tdp(int(w), plugged), "from": src,
                "apply": apply, "overrides": overrides, "prof": prof}

    def _start_game_power(self, appid: str, plugged: bool) -> dict:
        pk = self._pick(appid, plugged)
        prof = pk["prof"]
        self.guard.reset()
        self._pl2_for(pk, plugged)
        result = self.power.apply_profile(pk["apply"], pk["overrides"], plugged)
        result["limit_w"] = result["applied"].get("tdp")
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
            quiet.background(True)                       # the 1 Hz loop: on the E-cores, low priority, away from the game
        self._held_or_release(result)
        return result

    def _held_or_release(self, result: dict) -> None:
        """A TDP is held: the temperature guard watches it. None could be set: thermald back in charge."""
        if result["applied"].get("tdp") is None and self.autotune.session is None:
            self.power.release_thermald()
        self._ensure_guard()

    def _restart_game_power(self, appid: str, plugged: bool) -> dict:
        """The running game from a clean start on this power source: autotune's levers and the guard's step back to
        what was there before ClawDeck (thermald stays stopped: the limit is set again at once), then the user's pick
        and a fresh autotune at that limit. Used after a pick in the game, Use default, unplugging and resume."""
        self.autotune.stop(revert=False)
        self.power.revert_all(keep_thermald=True)
        return self._start_game_power(appid, plugged)

    def _apply_idle_pick(self, plugged: bool) -> dict | None:
        """No game runs: the limit you picked for this power source goes (back) on the chip, as the menus show it. If
        you never picked one on this power source, the chip keeps the firmware's limits and thermald runs as usual.
        Every write still goes through sysfs.write, so unloading the plugin reverts it."""
        slot = "plugged" if plugged else "battery"
        if not self.settings.get(f"last_profile_{slot}"):
            self._holding_pick = False
            self.power.release_thermald()
            return None
        pk = self._pick(None, plugged)
        self.guard.reset()
        self._pl2_for(pk, plugged)
        r = self.power.apply_profile(pk["apply"], pk["overrides"], plugged)
        self._holding_pick = True
        self._held_or_release(r)
        return {**r, "limit_w": r["applied"].get("tdp"), "pick": {"profile": pk["profile"], "tdp": pk["tdp"]}}

    def _apply_pick_now(self, plugged: bool) -> dict:
        """A pick was just saved (profile list or slider): the running game starts over from it, else it goes on the
        chip as it is."""
        if self.running_appid:
            self.running_plugged = plugged
            return self._restart_game_power(self.running_appid, plugged)
        return self._apply_idle_pick(plugged) or {"applied": {}, "failed": ["tdp"]}

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
            plugged = bool(self.battery.plugged())
            if self.autotune.session is not None:
                sess = self.autotune.session
                return self.guard.check(sess.learned.tdp, self._floor_w(), self.autotune.thermal_step,
                                        limit_w=sess.ceiling_tdp, raise_tdp=self.autotune.thermal_release)
            # your limit for this power source (the game's pick, or the last one): where stepping back up stops
            limit = self._pick(self.running_appid, plugged)["tdp"]
            return self.guard.check(self.power.read_tdp(), self._floor_w(), lambda w: self.power.set_tdp(w, plugged),
                                    limit_w=limit)

    def _save_learned(self, appid: str, plugged: bool) -> dict | None:
        """Stop autotune and keep what it found for this game and power source. The power stays as it is: the caller
        reverts and sets the next limit."""
        learned = self.autotune.stop(revert=False)
        quiet.background(False)
        # only a finished result: a session cut short (a quick launch, a menu, still lowering) keeps the last good one
        if learned and learned.get("learned") and learned.get("settled"):
            self.profiles.set(appid, plugged, {"learned": learned["learned"]})
        return learned

    async def game_started(self, appid: str) -> dict:
        appid = str(appid)
        async with self._power_lock:
            if self.running_appid and self.running_appid != appid:
                # a second game (or a launcher) started while one runs: save what the first one learned first
                self._save_learned(self.running_appid, bool(self.running_plugged))
            plugged = bool(self.battery.plugged())
            self.running_appid, self.running_plugged = appid, plugged
            self._holding_pick = False
            self.power.revert_all(keep_thermald=True)   # the game starts from what was there before ClawDeck + its pick
            return self._start_game_power(appid, plugged)

    async def game_stopped(self, appid: str) -> dict:
        appid = str(appid)
        if self.running_appid is not None and appid != self.running_appid:
            # Steam reports every app that stops (launchers): only the game ClawDeck set up counts
            return {"ignored": True}
        async with self._power_lock:
            plugged = self.running_plugged if self.running_plugged is not None else bool(self.battery.plugged())
            learned = self._save_learned(appid, plugged)
            self.running_appid, self.running_plugged = None, None
            # everything the game and autotune changed goes back, then the limit you picked for the power source the
            # Claw is on now, not the firmware's boot limits (maintainer, 2026-11-04: "doesn't reset back to what it
            # was set to")
            n = self.power.revert_all(keep_thermald=True)
            self.guard.reset()
            idle = self._apply_idle_pick(bool(self.battery.plugged()))
            return {"reverted": n, "learned": learned, "limit": idle}

    async def power_source_changed(self, plugged: bool) -> dict:
        """Steam reported AC plugged/unplugged: the running game switches to its other slot (battery / plugged), and
        autotune keeps learning in the slot that matches the new source."""
        plugged = bool(plugged)
        if self.running_appid is None:
            if not self._holding_pick:
                return {"changed": False}
            async with self._power_lock:            # no game: the pick for the new power source, as the menus show
                self.power.revert_all(keep_thermald=True)
                return {"changed": True, "limit": self._apply_idle_pick(plugged)}
        if plugged == self.running_plugged:
            return {"changed": False}
        appid = self.running_appid
        async with self._power_lock:
            self._save_learned(appid, bool(self.running_plugged))
            self.running_plugged = plugged
            return {"changed": True, **self._restart_game_power(appid, plugged)}

    # ---- fan and charge limit (Power) ---------------------------------------------------------------------------------
    def _reapply_fan_charge(self, why: str) -> None:
        """The saved fan preset and charge limit, at every start and resume (sysfs and the EC may reset)."""
        try:
            p = self.settings.get("fan_preset")
            if p and p != "auto" and getattr(self, "fan_hw", None):
                r = fan.apply(self.fan_hw, p, self.settings.get("fan_custom"))
                decky.logger.info("fan (%s): %s", why, r["summary"] if r["ok"] else r["error"])
            lim = self.settings.get("charge_limit")
            if lim and self.hw.paths.charge_limit:
                r = fan.set_charge(self.hw.paths.charge_limit, int(lim), self.settings.get("charge_original"))
                fan.kde_charge_limit(HOME, int(lim))
                decky.logger.info("charge limit (%s): %s", why, r["summary"] if r["ok"] else r["error"])
        except Exception as e:                          # never stop the plugin over this
            decky.logger.warning("fan/charge reapply (%s): %s", why, e)

    async def get_fan(self) -> dict:
        return {**fan.status(self.fan_hw), "preset": self.settings.get("fan_preset") or "auto",
                "custom": self.settings.get("fan_custom") or fan.PRESETS["quiet"]}

    async def set_fan(self, preset: str, custom: list | None = None) -> dict:
        r = await asyncio.get_event_loop().run_in_executor(None, fan.apply, self.fan_hw, preset, custom)
        if r["ok"]:
            self.settings["fan_preset"] = preset
            if preset == "custom":
                self.settings["fan_custom"] = [fan.snap(x) for x in custom or []]
            self._save_settings()
        decky.logger.info("fan: %s", r["summary"] if r["ok"] else r["error"])
        return r

    async def get_charge(self) -> dict:
        st = fan.charge_status(self.hw.paths.charge_limit)
        lim = self.settings.get("charge_limit")
        if lim and st.get("supported") and st.get("limit") != int(lim):
            # something else wrote the battery (KDE's helper on the way into Desktop Mode): put the limit back
            decky.logger.info("charge limit was reset to %s by something else: back to %s", st.get("limit"), lim)
            fan.set_charge(self.hw.paths.charge_limit, int(lim), self.settings.get("charge_original"))
            st = fan.charge_status(self.hw.paths.charge_limit)
        return {**st, "setting": lim}

    async def set_charge_limit(self, limit: int | None) -> dict:
        path = self.hw.paths.charge_limit
        if path and "charge_original" not in self.settings:   # what the battery had before ClawDeck: Off restores it
            self.settings["charge_original"] = sysfs.read_int(path)
        r = fan.set_charge(path, limit, self.settings.get("charge_original"))
        if r.get("ok"):
            await self._bg(fan.kde_charge_limit, HOME, None if limit is None else r["wanted"])
        if r["ok"]:
            self.settings["charge_limit"] = None if limit is None else r["wanted"]
            self._save_settings()
        decky.logger.info("charge limit: %s", r["summary"] if r["ok"] else r["error"])
        return r

    async def resumed(self) -> dict:
        """After resume from suspend: firmware may have reset limits; re-apply the running game."""
        self._reapply_fan_charge("resume")
        plugged = bool(self.battery.plugged())
        async with self._power_lock:
            if self.running_appid is None:
                if not self._holding_pick:
                    return {"reapplied": False}
                self.power.revert_all(keep_thermald=True)
                return {"reapplied": True, "limit": self._apply_idle_pick(plugged)}
            self._save_learned(self.running_appid, bool(self.running_plugged))
            self.running_plugged = plugged
            return {"reapplied": True, **self._restart_game_power(self.running_appid, plugged)}

    async def get_autotune(self) -> dict:
        st = {**self.autotune.status(), "thermal": self.guard.status(), "game": self.running_appid}
        if not st.get("running"):
            st["why"] = self._autotune_why()
        return st

    def _autotune_why(self) -> str:
        """Why no Autotune session runs, in the words the Power panel shows (it used to always blame the launch
        options, maintainer 2026-11-05: Autotune on, CONTROL running, "waiting for frame times")."""
        if not self.settings.get("autotune"):
            return "off"
        if self.running_appid is None:
            return "no game reported to ClawDeck yet"
        plugged = bool(self.running_plugged if self.running_plugged is not None else self.battery.plugged())
        if plugged and not self.settings.get("autotune_plugged"):
            return "off on the charger (Advanced → Autotune on the charger)"
        if self.profiles.get(self.running_appid, plugged).get("autotune") is False:
            return "off for this game"
        return "not started"

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
        OptiScaler in the game and whose it is, and Decky Framegen's state (compatibility)."""
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
                "files": files, "opti_version": self._opti_entry(launch)["version"],
                "xmx_proof": await self._bg(xess.xmx_proof, HOME, appid, RUNTIME_DIR)}

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
        """nitec's FSR 4.1.2 XMX build, where the launch wrapper takes it from (~/fsr4-xmx). Not downloaded by
        ClawDeck: it is shared in the OptiScaler Discord, and the user places it (amd_fidelityfx_upscaler_dx12.dll,
        fsr-original.dll). The cabinet.dll it needs is ClawDeck's own (tools/cabinet-lzms), not the user's."""
        missing = optiscaler.fsr4_xmx_missing(HOME)
        return {"dir": os.path.join(HOME, optiscaler.FSR4_XMX_DIR), "ready": not missing, "missing": missing}

    # ---- recommended setup per game (assets/games.json; what OptiClick does on Windows) --------------------------
    REC_KEYS = ("opti", "opti_xefg", "opti_fg", "opti_ini", "reframework", "fsr", "opti_dll", "opti_overlay")

    def _rec(self, appid: str) -> tuple[dict, str | None, str | None]:
        d = xess.steam_install_dir(HOME, appid)
        rec = gamedb.recommend(gamedb.load(self._games_path()), d)
        folder = optiscaler.main_exe_dir(d, rec["main_exes"]) if d else None
        self._opticlick_verdict(rec, appid)
        self._optiscaler_entry(rec, appid)
        self._prefer_fsr4(rec, d, appid)
        return rec, d, folder

    @staticmethod
    def _games_path() -> str:
        return livedb.games_path(LISTS_DIR, GAMES)

    @staticmethod
    def _opticlick_verdict(rec: dict, appid: str) -> None:
        """OptiClick's Supported Game List on this game (Intel Arc column). Not supported there: not recommended,
        and the reason says so; not in the list: set up the same way, marked as not on OptiClick's list."""
        oc = livedb.opticlick_says(livedb.opticlick(LISTS_DIR), appid)
        rec["opticlick"] = oc
        if not oc["known"] or rec["route"] == "own_xess":
            return
        if oc["listed"] and oc["intel_ok"] is False:
            rec["recommended"] = False
            rec["reasons"].insert(0, "OptiClick lists this game as not supported on Intel Arc: ClawDeck doesn't "
                                     "recommend OptiScaler here. You can still pick an upscaler by hand.")
        elif oc["listed"]:
            rec["reasons"].append(f"On OptiClick's supported list for Intel Arc ({oc['name']})")
        else:
            rec["reasons"].append("Not on OptiClick's supported list yet: set up from what the game ships, not tested by OptiClick")

    @staticmethod
    def _wiki_for(appid: str, fetch: bool = True) -> tuple[dict | None, dict | None]:
        """The game's row in OptiScaler's Compatibility List and its detailed entry (hook file names, known
        issues), matched by its Steam name."""
        row = livedb.find_compat(livedb.compat(LISTS_DIR), xess.steam_app_name(HOME, appid))
        page = livedb.game_page(LISTS_DIR, row.get("slug")) if row and fetch else None
        return row, page

    def _optiscaler_entry(self, rec: dict, appid: str) -> None:
        """What OptiScaler's own Compatibility List says about this game (as OptiClick and Decky Framegen follow
        it): not working means not recommended; "Requires REFramework" gives the Capcom setup even when the exe
        isn't in ClawDeck's RE Engine list yet; the entry's file names are where OptiScaler goes in; its known
        issues are shown before anything is installed."""
        row, page = self._wiki_for(appid)
        rec["optiscaler_wiki"] = {"row": row, "page": page}
        if not row or rec["route"] == "own_xess":
            return
        if row["status"] == "broken" and rec.get("optiscaler"):
            rec["recommended"] = False
            rec["reasons"].insert(0, f"OptiScaler's compatibility list marks {row['name']} as not working"
                                     + (f" ({row['notes']})" if row["notes"] else "") + ".")
        if row["reframework"] and not rec.get("re_engine") and rec.get("optiscaler"):
            ini = dict(rec["launch"].get("opti_ini") or {})
            ini.pop("Spoofing_Dxgi", None)
            rec["launch"] = {"opti": "xess", "opti_fg": "dlssg", "opti_ini": ini, "reframework": True}
            rec.update(route="re_engine", re_engine=True, capcom=True, frame_gen="dlssg", fg_source="dlssg")
            rec["reasons"][0] = ("OptiScaler's compatibility list: this game needs REFramework. OptiScaler (XeSS on "
                                 "the XMX cores) with REFramework, the way OptiClick sets up Capcom games.")
        if page and page["proxies"]:
            rec["proxies"] = page["proxies"]
            rec["reasons"].append(f"Loads as {' or '.join(page['proxies'])} (OptiScaler's entry for this game)")
        for issue in (page or {}).get("issues", [])[:2]:
            rec["reasons"].append(f"Known: {issue}")

    def _prefer_fsr4(self, rec: dict, d: str | None, appid: str) -> None:
        """The newest upscaler where it can run, as OptiClick does on Intel (it installs FSR 4 on XMX with
        OptiScaler): FSR 4.1.2 on the XMX cores when the game is DirectX 12, XMX is on and nitec's files are there.
        Not for Capcom RE Engine games (black picture in Onimusha, 2026-11-04) or games with their own XeSS."""
        launch = rec.get("launch") or {}
        if not rec.get("optiscaler") or rec.get("re_engine") or launch.get("opti") != "xess" or self._xmx_off(appid):
            return
        if not d or not gamedb.is_dx12(d, rec.get("main_exes")):
            return
        files = self._fsr4_xmx_files()
        if files["ready"]:
            launch["opti"] = "fsr4_xmx"
            rec["upscaler"] = "fsr4_xmx"
            rec["reasons"][0] = rec["reasons"][0].replace("into XeSS on the XMX cores", "into FSR 4.1.2 on the XMX cores (the newest; XeSS 2 is one tap away)")
        else:
            rec["reasons"].append("FSR 4.1.2 (newer than XeSS) would be picked here: put nitec's two files in ~/fsr4-xmx (see FSR 4.1.2 files)")

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
        doc = gamedb.load(self._games_path())
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
        decky.logger.info("game %s: route %s, DirectX 12 %s, FSR 4 XMX files %s (missing %s)", appid, rec["route"], dx12,
                          fsr4["ready"], fsr4["missing"])
        return {**rec, "dir": d, "folder": folder, "applied": applied, "opti_owner": who["owner"],
                "fsr4": {"dx12": dx12, "files": fsr4["ready"], "missing": fsr4["missing"], "dir": fsr4["dir"],
                         "xmx": not self._xmx_off(appid)},
                "reframework_state": reframework.status(folder) if folder and rec["re_engine"] else None}

    def _opti_entry(self, launch: dict) -> dict:
        """The OptiScaler this game gets: OptiClick's Capcom setup (v10, subfolder layout) for a Capcom RE Engine
        game, 0.9.4 for every other one."""
        v = self._versions()
        return v["optiscaler_v10"] if launchenv.capcom(launch) and v.get("optiscaler_v10") else v["optiscaler"]

    def _ref_shas(self) -> tuple[str, ...]:
        e = self._versions().get("reframework") or {}
        return tuple(x for x in (e.get("dll_sha256"),) if x)

    def _xmx_off(self, appid: str) -> bool:
        """XMX switched off for this game (Per-game): FSR 4.1.2 isn't offered then (a black screen without XMX)."""
        return self.profiles.get_launch(appid).get("xmx") is False

    async def _opti_into(self, appid: str, folder: str, launch: dict) -> dict:
        """ClawDeck's OptiScaler into the game with the settings of `launch` (preset, XeSS-FG, the game's fixes)."""
        entry = self._opti_entry(launch)
        if not optiscaler.installed(HOME, entry["version"]) or not optiscaler.extras_ready(HOME, entry):
            if self._install_lock.locked():
                return {"ok": False, "busy": True, "error": "already downloading; wait for the first one to finish"}
            async with self._install_lock:
                r = await self._bg(optiscaler.install, HOME, entry)
            if not r.get("ok"):
                return r
        opti = launch.get("opti") or "xess"
        opti = "fsr4_xmx" if opti == "fsr4" else opti
        if opti in launchenv.FSR4_XMX and launch.get("xmx") is False:
            return {"ok": False, "error": "FSR 4.1.2 needs the XMX cores, and XMX is off for this game: pick XeSS 2, or switch XMX on first"}
        _, page = await self._bg(self._wiki_for, appid)
        r = await self._bg(optiscaler.patch, HOME, folder, entry["version"], launchenv.opti_ini({**launch, "opti": opti}),
                           opti, opti in launchenv.FSR4_XMX, entry.get("layout") or "flat", (page or {}).get("proxies"))
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
        if rec.get("recommended") is False:
            return {"ok": False, "error": rec["reasons"][0]}
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

    async def game_choose(self, appid: str, upscaler: str, fg: int, allow_unpinned: bool = False,
                          overlay: int | None = None) -> dict:
        """Per-game's two choices, as plain as OptiClick's: the upscaler (the game's own, or XeSS / FSR 4.1.2 /
        FSR 4.1.2 Lite on the XMX cores through ClawDeck's OptiScaler) and frame generation (off, or XeSS frame
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
        if upscaler.startswith("fsr4_xmx") and self._xmx_off(appid):
            return {"ok": False, "error": "FSR 4.1.2 needs the XMX cores, and XMX is off for this game: pick XeSS 2, or switch XMX on first"}
        if upscaler.startswith("fsr4_xmx") and not self._fsr4_xmx_files()["ready"]:
            f = self._fsr4_xmx_files()
            return {"ok": False, "error": f"FSR 4.1.2 on XMX needs nitec's files in {f['dir']} (missing: {', '.join(f['missing'])})"}
        if upscaler == "own":
            out = await self._bg(optiscaler.unpatch, folder)
            ref = await self._bg(reframework.remove, folder, self._ref_shas())
            self.settings.setdefault("opti_games", {}).pop(str(appid), None)
            self._save_settings()
            r = await self.set_launch(appid, {**{k: None for k in self.REC_KEYS}, "fg": fg or None})
            # a REFramework ClawDeck doesn't know (another build, put in by hand) is left alone: say it is still there
            left = ref.get("present") and not ref.get("removed")
            return {**r, "optiscaler": out, "reframework_left": bool(left)}
        who = optiscaler.owner(folder)
        if who["owner"] in ("decky-framegen", "other"):
            return {"ok": False, "owner": who["owner"], "error": "this game has OptiScaler from " + (
                "Decky Framegen: remove it there (Decky Framegen → Unpatch), then pick again" if who["owner"] == "decky-framegen"
                else "somewhere else (OptiClick's files from Windows, or by hand): remove OptiScaler.ini and its DLL, then pick again")}
        launch = {"opti": upscaler, "opti_fg": rec["fg_source"] if fg else "off", "opti_ini": rec["ini"] if rec["optiscaler"] else {},
                  "fg": fg or None, "reframework": True if rec["re_engine"] else None, "fsr": None, "opti_xefg": None,
                  "opti_overlay": overlay if isinstance(overlay, int) and overlay in launchenv.OVERLAYS else None}
        r = await self._opti_into(appid, folder, {**{k: v for k, v in launch.items() if v is not None},
                                                  "xmx": self.profiles.get_launch(appid).get("xmx")})
        if not r.get("ok"):
            return r
        launch["opti_dll"] = r["dll_override"]
        # XeSS and FSR 4.1.2 reach the XMX cores only through the shim in the prefix's driver store, which Valve's
        # Proton 11.0 doesn't make (the scope script does the same at every launch)
        shim = await self._bg(xmxscope.prefix_shim, HOME, appid, xess.package_dir(HOME))
        decky.logger.info("game %s: XMX shim in the prefix: %s", appid, "put there" if shim.get("placed") else shim.get("why") or "already there")
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
        removed = await self._bg(reframework.remove, folder, self._ref_shas()) if folder else {"ok": True}
        opti = await self._bg(optiscaler.unpatch, folder) if folder else {"ok": True}
        self.settings.setdefault("opti_games", {}).pop(str(appid), None)
        self._save_settings()
        r = await self.set_launch(appid, {k: None for k in self.REC_KEYS})
        return {**r, "reframework": removed, "optiscaler": opti, "ok": r.get("ok", True) and opti.get("ok", True)}

    # ---- ClawDeck's OptiScaler (Setup); Decky Framegen detection for compatibility -----------------------------------
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
