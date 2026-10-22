"""ClawDeck - Decky Loader backend. Thin: every call maps to one module in py_modules/clawdeck.

Runs as root (plugin.json flag root) because sysfs, InputPlumber's config, /etc/firmware and the Steam library
need it. Battery rule: nothing here runs on a timer unless autotune is on for a running game (1 Hz, one asyncio
task). Power-source changes and resume arrive as events from Steam (frontend listeners).

Scope (2026-10-21 reset): only what Bazzite and Steam's Quick Access do not already do. Four panels: Power
(profile + autotune), Per-game (XeSS XMX, XeLL), Fixes (VRR range, face buttons, health), Setup (the XeSS driver,
self-test, dry run, log export). OptiScaler and Lossless Scaling per game are Bazzite's own Decky plugins.
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

from clawdeck import (autotune, battery, buttons, changes, downloads, edid, health, hw, launchenv, power,  # noqa: E402
                      profiles, stack, sysfs, userexec, xess)

PLUGIN_DIR = os.path.dirname(os.path.realpath(__file__))
SETTINGS_DIR = decky.DECKY_PLUGIN_SETTINGS_DIR
RUNTIME_DIR = decky.DECKY_PLUGIN_RUNTIME_DIR
LOG_DIR = decky.DECKY_PLUGIN_LOG_DIR
HOME = decky.DECKY_USER_HOME
DESKTOP = os.path.join(HOME, "Desktop")
VERSIONS = os.path.join(PLUGIN_DIR, "assets", "versions.json")
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
        env_dry = os.environ.get("CLAWDECK_DRY_RUN", "0") not in ("", "0")
        sysfs.set_dry_run(env_dry or bool(self.settings.get("dry_run")))
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
    @staticmethod
    def _done(ok: bool, what: str, wanted, read, unit: str = "") -> dict:
        """What every single-lever write answers (HARDWARE-FINDINGS §14): the value asked for, the value read back
        from the hardware afterwards, and on failure the reason. The UI shows `summary` in its toast, `error` in red."""
        u = f" {unit}" if unit else ""
        fmt = lambda v: "?" if v is None else (("on" if v else "off") if isinstance(v, bool) else f"{v}{u}")
        err = None
        if sysfs.dry_run():
            summary = f"dry run: would set {what} to {fmt(wanted)} (hardware still reads {fmt(read)})"
        elif not ok:
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
        if sysfs.dry_run():
            r["summary"] = f"dry run: would set {name} {want} W (hardware unchanged, {self._reads(st)})"
            r["ok"], r["error"] = True, None
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
            ok = self.power.set_tdp(want, plugged)
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
            r = self.power.apply_profile(name, overrides, plugged)
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
        result = self.power.apply_profile(base, overrides, plugged)
        if self.settings.get("autotune") and prof.get("autotune", True):
            # the picked limit (profile or slider) is the set TDP autotune measures at and works under; its floor is
            # the model minimum or the user's
            lo, hi = hw.tdp_range(self.hw.model, plugged)
            ceiling = result["applied"].get("tdp") or hi
            floor = max(lo, min(int(self.settings.get("autotune_floor") or lo), int(ceiling)))
            self.autotune.start(appid, prof.get("learned"), floor, int(ceiling), plugged,
                                keep_pct=self.settings.get("autotune_keep"),
                                xell_log=os.path.join(xess.prefix_dir(HOME, appid), "igdext_xell.log"))
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

    async def set_autotune_keep(self, pct: float) -> dict:
        """How close to the most the set TDP gives autotune must stay (2-15 %, default 5): smaller = more frames,
        larger = more battery."""
        pct = max(2.0, min(float(pct), 15.0))
        self.settings["autotune_keep"] = pct
        self._save_settings()
        self.autotune.set_keep(pct)
        return {"ok": True, "keep_pct": pct, "summary": f"Autotune keeps within {pct:g} % of the most the TDP gives", "error": None}

    async def get_game_profile(self, appid: str) -> dict:
        return {"battery": self.profiles.get(appid, False), "plugged": self.profiles.get(appid, True),
                "launch": self.profiles.get_launch(appid)}

    # ---- per-game launch environment (XeSS XMX, XeLL) --------------------------------------------------------------
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
        allowed = {"xmx", "xell"}
        clean = {k: v for k, v in values.items() if k in allowed}
        try:   # a value the launch wrapper can't use must be refused here, not break get_launch later
            launchenv.compose({**self.profiles.get_launch(appid), **clean})
        except ValueError as e:
            return {"ok": False, "error": str(e), **(await self.get_launch(appid))}
        self.profiles.set_launch(appid, clean)
        return {"ok": True, **(await self.get_launch(appid)), "file": self._write_launch(appid)}

    async def game_status(self, appid: str) -> dict:
        """The Per-game panel in one call: the XeSS driver and what XeLL did in this game's last launch."""
        launch = self.profiles.get_launch(appid)
        xs, xl, op = await asyncio.gather(self._bg(xess.state, HOME), self._bg(xess.game_xell, HOME, appid, bool(launch.get("xell"))),
                                          self._bg(xess.optiscaler_in, HOME, appid))
        return {"launch": launch, "xess": xs, "xell": xl, "optiscaler": op, "wrapper": WRAPPER + " %command%"}

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
