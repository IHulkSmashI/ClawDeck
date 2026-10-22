"""ClawDeck - Decky Loader backend. Thin: every call maps to one module in py_modules/clawdeck.

Runs as root (plugin.json flag root) because sysfs, InputPlumber's config, /etc/firmware and the Steam library
need it. Battery rule: nothing here runs on a timer unless autotune is on for a running game (1 Hz, one asyncio
task). Power-source changes and resume arrive as events from Steam (frontend listeners).

Scope (2026-10-21 reset): only what Bazzite and Steam's Quick Access do not already do. Four panels: Power
(profile + autotune), Per-game (XeSS XMX, XeLL, OptiScaler), Fixes (VRR range, face buttons, health), Setup
(installs, self-test, dry run, log export).
"""
import asyncio
import glob
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "py_modules"))

import decky  # noqa: E402

from clawdeck import (autotune, battery, buttons, changes, compat, downloads, edid, gamescan, health, hw,  # noqa: E402
                      injector, launchenv, power, profiles, stack, sysfs, updates, userexec, xess)

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
    userexec.write_new(path, text, 0o755 if path.endswith(".sh") else 0o644)
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
        self.updates = updates.Updates(os.path.join(RUNTIME_DIR, "assets"), os.path.join(RUNTIME_DIR, "updates.json"))
        self.compat = compat.Compat(os.path.join(RUNTIME_DIR, "cache"))
        mangohud_dir = os.path.join(RUNTIME_DIR, "mangohud")
        # games run as the desktop user and MangoHud writes its frame-time log here: the folder must be theirs
        os.makedirs(mangohud_dir, exist_ok=True)
        userexec.chown_tree(mangohud_dir, HOME)
        self.autotune = autotune.Autotune(self.hw, self.power, mangohud_dir, csv_log=os.path.join(LOG_DIR, "autotune.csv"))
        self.autotune.enabled = bool(self.settings.get("autotune"))
        self.running_appid: str | None = None
        self._tasks: set[asyncio.Task] = set()
        self._health: tuple[float, list] | None = None
        self._power_lock = asyncio.Lock()      # one power write at a time: a fast profile change never interleaves
        self.running_plugged: bool | None = None
        decky.logger.info("ClawDeck on %s (%s), kernel %s, dry-run %s", self.hw.model["name"], self.hw.product,
                          self.hw.kernel, sysfs.dry_run())
        self._install_wrapper()

    async def _unload(self):
        self.autotune.stop()
        n = self.power.revert_all()
        decky.logger.info("ClawDeck unloaded, %d value(s) reverted", n)

    async def _uninstall(self):
        """Leave nothing behind that changes how the Claw or a game behaves (the face-button fix stays on purpose:
        without it the buttons are dead)."""
        self.power.revert_all()
        if glob.glob(f"{edid.EDID_DIR}/clawdeck-*.bin"):   # the wider VRR range: undone at the next restart
            decky.logger.info("removing the wider VRR range: %s", edid.disable(sysfs.dry_run()))
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
            if sysfs.dry_run():
                return {"ok": True, "dry_run": True, "would": f"systemctl enable --now hhd@{u.pw_name}"}
            r = await self._bg(lambda: subprocess.run(["systemctl", "enable", "--now", f"hhd@{u.pw_name}"],
                                                      capture_output=True, text=True, timeout=60, check=False))
            return {"ok": r.returncode == 0, "log": (r.stdout + r.stderr)[-2000:]}
        if fix == "install_xess":
            return await self.xess_install(allow_unpinned)
        if fix == "fix_face_buttons":
            return await self._bg(buttons.apply)
        if fix == "undo_face_buttons":
            return await self._bg(buttons.remove)
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
        return {"vrr": vrr, "buttons": buttons.status(self.hw.model_id), "health": health.summary(checks)}

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

    async def self_test(self) -> dict:
        """tools/selftest.py through the plugin: every control written, read back, measured and reverted. Never
        while a game runs (it would take the game's power away for a minute)."""
        if self.running_appid is not None:
            return {"ok": False, "error": "close the game first: the self-test changes and reverts the power limits"}
        if sysfs.dry_run():
            return {"ok": False, "error": "turn Dry run off first: the self-test has to write for real"}
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
    async def get_power(self) -> dict:
        return self.power.status()

    async def apply_profile(self, name: str, appid: str | None = None) -> dict:
        """Apply a profile and read the limits back (HARDWARE-FINDINGS §14): the UI shows `summary` in its toast
        and `error` in red; `read` is what the hardware reports after the write."""
        async with self._power_lock:
            plugged = self.battery.plugged()
            prof = self.profiles.get(appid, bool(plugged)) if appid else {}
            # a profile picked by hand wins over the game's saved TDP: its watts are what the user just chose
            overrides = {k: v for k, v in prof.items() if k in POWER_KEYS and k != "tdp"}
            self.settings["last_profile"] = name
            self._save_settings()
            sysfs.clear_error()
            r = self.power.apply_profile(name, overrides, plugged)
            if "tdp" in r["applied"]:
                self.autotune.set_ceiling(r["applied"]["tdp"])   # autotune now works under this profile's power
            st = self.power.status()
        r["read"] = {"tdp_w": st["tdp_w"], "pl2_w": st["rapl"]["pl2_w"], "cpu_max_pct": st["cpu"]["max_perf_pct"],
                     "epp": st["cpu"]["epp"], "backend": st["tdp_backend"]}
        r["ok"] = not r["failed"]
        want = r["applied"].get("tdp")
        if r["ok"] and want is not None and st["tdp_w"] is not None and st["tdp_w"] != want:
            r["ok"] = False
            r["error"] = f"asked for {want} W, hardware reads {st['tdp_w']} W"
        elif not r["ok"]:
            r["error"] = sysfs.last_error() or f"could not set: {', '.join(r['failed'])}"
        else:
            r["error"] = None
        r["summary"] = (f"{name} set → {self._reads(st)}" if r["ok"] else f"{name}: {r['error']}")
        if sysfs.dry_run():
            r["summary"] = f"dry run: would apply {name} (hardware unchanged, {self._reads(st)})"
            r["ok"], r["error"] = True, None
        return r

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
        base = prof.get("base") or self.settings.get("last_profile") or ("performance" if plugged else "battery")
        overrides = {k: v for k, v in prof.items() if k in POWER_KEYS}
        result = self.power.apply_profile(base, overrides, plugged)
        if self.settings.get("autotune") and prof.get("autotune", True) and prof.get("target_fps"):
            # the profile's power limit is autotune's ceiling: it only ever lowers power below what the user picked
            lim = self.hw.model["tdp"]
            ceiling = result["applied"].get("tdp") or (lim["plugged_max"] if plugged else lim["battery_max"])
            self.autotune.start(appid, int(prof["target_fps"]), prof.get("learned"), lim["min"], int(ceiling), plugged)
            result["autotune"] = "started"
        return result

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
        task = asyncio.get_running_loop().create_task(self._after_game(appid))
        self._tasks.add(task)                      # keep a reference so it isn't garbage-collected mid-way
        task.add_done_callback(self._tasks.discard)
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
        return self.autotune.status()

    async def set_target_fps(self, appid: str, fps: int) -> dict:
        """Autotune's target for a game, the same on battery and plugged in."""
        fps = max(30, min(240, int(fps)))
        for plugged in (False, True):
            self.profiles.set(appid, plugged, {"target_fps": fps})
        return {"ok": True, "target_fps": fps}

    async def get_game_profile(self, appid: str) -> dict:
        return {"battery": self.profiles.get(appid, False), "plugged": self.profiles.get(appid, True),
                "launch": self.profiles.get_launch(appid)}

    # ---- per-game launch environment (injector, XeSS XMX, XeLL, Mesa) ----------------------------------------------
    def _write_launch(self, appid: str) -> dict:
        try:
            return launchenv.write(RUNTIME_DIR, appid, self.profiles.get_launch(appid))
        except ValueError as e:
            return {"error": str(e)}

    async def get_launch(self, appid: str) -> dict:
        launch = self.profiles.get_launch(appid)
        try:
            env, error = launchenv.compose(launch), None
        except ValueError as e:      # e.g. an imported profile with a value this version doesn't know
            env, error = {}, str(e)
        return {"launch": launch, "env": env, "wrapper": WRAPPER + " %command%", "error": error}

    async def set_launch(self, appid: str, values: dict) -> dict:
        allowed = {"lsfg", "mesa", "xmx", "xell"}   # inject / proxy / reframework are set by injector_install
        clean = {k: v for k, v in values.items() if k in allowed}
        try:   # a value the launch wrapper can't use must be refused here, not break get_launch later
            launchenv.compose({**self.profiles.get_launch(appid), **clean})
        except ValueError as e:
            return {"ok": False, "error": str(e), **(await self.get_launch(appid))}
        self.profiles.set_launch(appid, clean)
        return {"ok": True, **(await self.get_launch(appid)), "file": self._write_launch(appid)}

    async def game_status(self, appid: str) -> dict:
        """The Per-game panel in one call: the XeSS driver, what XeLL did in this game, and the injector's state."""
        launch = self.profiles.get_launch(appid)
        xs, xl, st = await asyncio.gather(self._bg(xess.state, HOME), self._bg(xess.game_xell, HOME, appid, bool(launch.get("xell"))),
                                          self.injector_state(appid))
        return {"launch": launch, "xess": xs, "xell": xl, "injector": st, "wrapper": WRAPPER + " %command%"}

    # ---- XMX injector: OptiScaler / lsfg-vk per game, always the newest release -------------------------------------
    def _xess_ok(self) -> bool:
        return xess.state(HOME)["status"]["state"] == "ok"

    def _game(self, appid: str) -> tuple[str | None, dict | None]:
        game_dir = injector.steam_install_dir(HOME, appid)
        if not game_dir:
            return None, None
        exe_dir = injector.find_game_exe_dir(game_dir)
        return game_dir, gamescan.scan(game_dir, exe_dir)

    async def injector_state(self, appid: str) -> dict:
        return await self._bg(self._injector_state, appid)

    def _injector_state(self, appid: str) -> dict:
        game_dir = injector.steam_install_dir(HOME, appid)
        st = self.injector.state(game_dir) if game_dir else {"dir": None, "mode": "none", "optiscaler_present": False,
                                                               "native_xess": False, "info": None, "foreign_optiscaler": False}
        st["assets"] = self.injector.assets_ready()
        st["lsfg"] = injector.lsfg_state(HOME)
        st["lsfg_version"] = self.settings.get("lsfg_version")
        return st

    async def injector_plan(self, appid: str, name: str = "") -> dict:
        """Everything the Per-game panel needs to recommend one tier for this game, OptiClick-style: what the game
        ships, what OptiScaler's community list and OptiPatcher know about it, and what is installed now."""
        loop = asyncio.get_running_loop()
        game_dir, scan = await loop.run_in_executor(None, self._game, appid)
        if scan is None:
            return {"found": False}
        info = await loop.run_in_executor(None, self.compat.for_game, name, scan["exes"])
        p = injector.plan(scan, info, self._xess_ok())
        st = await self.injector_state(appid)
        latest = await loop.run_in_executor(None, self.updates.check)
        return {"found": True, "plan": p, "found_in_game": gamescan.summary(scan), "scan": scan,
                "compat": {k: info[k] for k in ("listed", "optipatcher", "needs_reframework", "list_loaded")},
                "state": st, "updates": latest}

    async def injector_install(self, appid: str, mode: str, options: dict | None = None, lsfg: dict | None = None,
                               name: str = "") -> dict:
        """Set this game up with mode (optiscaler | lsfg | none). options override the plan (multiplier 2/3/4, fg).
        Downloads the newest OptiScaler / OptiPatcher / REFramework first when upstream has a newer one."""
        game_dir = injector.steam_install_dir(HOME, appid)
        if not game_dir:
            return {"error": "game folder not found in Steam's library (is it a Steam game, installed?)"}
        loop = asyncio.get_running_loop()
        try:
            if mode == "optiscaler":
                _, scan = await loop.run_in_executor(None, self._game, appid)
                info = await loop.run_in_executor(None, self.compat.for_game, name, scan["exes"])
                p = injector.plan(scan, info, self._xess_ok())
                opts = {**p["options"], **{k: v for k, v in (options or {}).items() if k in ("multiplier", "fg")}}
                if sysfs.dry_run():
                    return {"mode": mode, "dry_run": True, "would": f"install the newest OptiScaler into {scan['exe_dir']} with {opts}"}
                versions = {"optiscaler": (await loop.run_in_executor(None, self.updates.ensure, "optiscaler"))["version"]}
                for comp in ("optipatcher", "reframework"):
                    if opts.get(comp):
                        try:
                            versions[comp] = (await loop.run_in_executor(None, self.updates.ensure, comp))["version"]
                        except Exception as e:    # the game still works without it; say so
                            decky.logger.warning("%s download failed: %s", comp, e)
                r = await loop.run_in_executor(None, self.injector.install_optiscaler, game_dir, opts, versions)
                self._give_back(r)
                self.profiles.set_launch(appid, {"inject": "optiscaler", "proxy": r["proxy"],
                                                 "reframework": bool(r.get("reframework_version")), "lsfg": None})
            elif mode == "lsfg":
                ls = injector.lsfg_state(HOME)
                if not ls["lossless_dll"]:
                    return {"error": "Lossless Scaling is not installed. Install it from your Steam library first (you need to own it)."}
                if not ls["layer"]:
                    got = await self.lsfg_install()
                    if not got.get("ok"):
                        return {"error": got.get("error", "lsfg-vk could not be installed")}
                if (await self._bg(self.injector.state, game_dir))["optiscaler_present"]:
                    await self._bg(self.injector.uninstall, game_dir)
                self.profiles.set_launch(appid, {"inject": "lsfg", "proxy": None, "reframework": None,
                                                 "lsfg": {**injector.LSFG_DEFAULTS, **(lsfg or {})}})
                r = {"mode": "lsfg"}
            elif mode == "none":
                r = await self._bg(self.injector.uninstall, game_dir)
                self.profiles.set_launch(appid, {"inject": "none", "proxy": None, "reframework": None, "lsfg": None})
            else:
                return {"error": f"unknown mode {mode}"}
        except Exception as e:   # network, archive, permissions: report, never crash the backend
            return {"error": f"{type(e).__name__}: {e}"}
        self._write_launch(appid)
        return r

    def _give_back(self, info: dict) -> None:
        """The backend runs as root; files it adds to a game folder must belong to the user, or Steam can no longer
        update or verify the game."""
        exe_dir = os.path.dirname(info["copied"][0]) if info.get("copied") else None
        for p in info.get("copied", []) + info.get("created_dirs", []):
            userexec.chown_tree(p, HOME)
        for p in info.get("backed_up", []):
            userexec.chown_tree(p + injector.BACKUP_SUFFIX, HOME)
        if exe_dir:
            for f in ("OptiScaler.ini", injector.MARKER):
                userexec.chown_tree(os.path.join(exe_dir, f), HOME)

    async def optiscaler_download(self) -> dict:
        """Setup → Install OptiScaler: the newest release (and OptiPatcher, REFramework) into ClawDeck's assets, so a
        game's first setup needs no download. Per game it goes in through injector_install."""
        if sysfs.dry_run():
            return {"ok": True, "dry_run": True, "would": "download the newest OptiScaler, OptiPatcher and REFramework into ClawDeck's assets"}
        got, errors = {}, []
        for comp in ("optiscaler", "optipatcher", "reframework"):
            try:
                got[comp] = (await self._bg(self.updates.ensure, comp))["version"]
            except Exception as e:
                errors.append(f"{comp}: {type(e).__name__}: {e}")
        ok = "optiscaler" in got
        return {"ok": ok, "versions": got, "error": None if ok and not errors else " · ".join(errors)}

    async def lsfg_install(self) -> dict:
        """Install or update lsfg-vk v2 (newest build). On Bazzite through its own `ujust get-lsfg install`, elsewhere
        the same download ClawDeck unpacks into ~/.local itself."""
        if sysfs.dry_run():
            return {"ok": True, "dry_run": True, "would": "install the newest lsfg-vk into ~/.local"}
        loop = asyncio.get_running_loop()
        try:
            latest = ((await loop.run_in_executor(None, self.updates.check)).get("lsfg") or {}).get("latest")
            if shutil.which("ujust"):
                r = await loop.run_in_executor(None, lambda: userexec.run(["ujust", "get-lsfg", "install"], HOME, timeout=300))
                ok = r["rc"] == 0
            else:
                got = await loop.run_in_executor(None, self.updates.ensure, "lsfg")
                await loop.run_in_executor(None, injector.install_lsfg_layer, got["dir"], HOME)
                userexec.chown_tree(os.path.join(HOME, ".local", "lib"), HOME)
                userexec.chown_tree(os.path.join(HOME, ".local", "share", "vulkan"), HOME)
                ok, r = True, {"out": ""}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if ok:
            self.settings["lsfg_version"] = latest
            self._save_settings()
        return {"ok": ok, "version": latest, "log": r.get("out", "")[-1500:]}

    async def get_updates(self, force: bool = False) -> dict:
        loop = asyncio.get_running_loop()
        st = await loop.run_in_executor(None, self.updates.check, bool(force))
        lat = (st.get("lsfg") or {}).get("latest")
        st["lsfg"] = {"installed": self.settings.get("lsfg_version"), "latest": lat,
                      "update": bool(lat) and bool(self.settings.get("lsfg_version")) and lat != self.settings.get("lsfg_version")}
        st["auto"] = self.settings.get("auto_update", True)
        return st

    async def _refresh_all(self) -> None:
        """Refresh every set-up game; one game that fails (moved, read-only) never stops the others."""
        for appid in self.profiles.games():
            if self.running_appid is not None:
                return
            try:
                await self._refresh_game(appid)
            except Exception as e:
                decky.logger.warning("refreshing game %s failed: %s", appid, e)

    async def _refresh_game(self, appid: str) -> dict | None:
        """A game set up with an older OptiScaler / REFramework / OptiPatcher than the one downloaded: reinstall it
        with the same options. Only called when no game is running."""
        game_dir = injector.steam_install_dir(HOME, appid)
        if not game_dir:
            return None
        info = await self._bg(lambda: self.injector.marker(injector.find_game_exe_dir(game_dir)))
        if not info or info.get("mode") != "optiscaler":
            return None
        have = {"optiscaler": self.updates.installed("optiscaler"), "reframework": self.updates.installed("reframework"),
                "optipatcher": self.updates.installed("optipatcher")}
        if not have["optiscaler"]:          # nothing downloaded to refresh with (cleared cache, failed download)
            return None
        stale = info.get("version") != have["optiscaler"] or \
            (info.get("reframework_version") and info["reframework_version"] != have["reframework"]) or \
            (info.get("optipatcher_version") and info["optipatcher_version"] != have["optipatcher"])
        if not stale or sysfs.dry_run() or self.running_appid is not None:   # never while a game runs
            return None
        loop = asyncio.get_running_loop()
        r = await loop.run_in_executor(None, self.injector.install_optiscaler, game_dir, info.get("options") or {}, have)
        self._give_back(r)
        decky.logger.info("game %s: OptiScaler refreshed to %s", appid, have["optiscaler"])
        return r

    async def _after_game(self, appid: str) -> None:
        """When a game closes: fetch newer releases if there are any (at most one check a day) and refresh this game."""
        if not self.settings.get("auto_update", True):
            return
        try:
            st = await asyncio.get_running_loop().run_in_executor(None, self.updates.check)
            for comp in ("optiscaler", "optipatcher", "reframework"):
                if st[comp]["update"] and self.updates.installed(comp):
                    await asyncio.get_running_loop().run_in_executor(None, self.updates.ensure, comp)
            if self.running_appid is None:
                await self._refresh_game(appid)
        except Exception as e:
            decky.logger.warning("auto update after %s failed: %s", appid, e)

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
        return await self._bg(xess.state, HOME)

    async def xess_install(self, allow_unpinned: bool = False) -> dict:
        self._health = None
        try:
            r = await self._bg(xess.install, HOME, self._versions()["xess_xmx_linux"], bool(allow_unpinned), sysfs.dry_run())
        except Exception as e:
            r = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if not r.get("ok"):
            decky.logger.warning("XeSS XMX install failed: %s", r.get("error"))
        return r

    async def xess_probe(self) -> dict:
        return await self._bg(xess.probe, HOME)

    async def xess_uninstall(self) -> dict:
        self._health = None
        return await self._bg(xess.uninstall, HOME)

    # ---- the VRR range fix (ClawLab's EDID, Fixes panel) -------------------------------------------------------------
    async def get_wide_vrr(self) -> dict:
        return await self._bg(edid.status)

    async def set_wide_vrr(self, on: bool) -> dict:
        """30-120 Hz VRR on the Claw's own screen (ClawLab's EDID fix). Rebuilds the initramfs: a minute or two."""
        fn = edid.enable if on else edid.disable
        r = await self._bg(fn, sysfs.dry_run())
        decky.logger.info("wide VRR %s: %s", "install" if on else "remove", r)
        return r
