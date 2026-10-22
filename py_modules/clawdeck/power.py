"""Power profiles: TDP (Handheld Daemon, else RAPL, else MSI firmware attributes), CPU boost, CPU ceiling, CPU energy mode
and GPU clock window (sysfs, PowerControl's paths).

Why ClawDeck sets TDP at all: Steam's TDP slider does nothing on the Claw 8 EX. PowerStation reports MinTdp = MaxTdp
= 0 for Panther Lake (HARDWARE-FINDINGS §1) and the MSI firmware attributes steamos-manager writes are ignored (§12);
the chip's own RAPL-MMIO limit is the only control that works.

Why three levers: on the Claw the package TDP is shared by CPU and GPU and the firmware splits it dynamically.
Capping the CPU hands the budget to the GPU in GPU-bound games; a GPU floor stops the GPU idling down between
frames; a GPU cap hands the budget back to the CPU in CPU-bound spots. Nothing here overclocks: every value is
clamped to what the hardware reports (rp0/rpn, 0-100 %), and every write is revertible (sysfs.py).
"""
from __future__ import annotations

import logging
import subprocess

from . import hhd, hw, sysfs

log = logging.getLogger("clawdeck.power")


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def firmware_range(current_value: str) -> tuple[int, int]:
    """min_value / max_value next to a firmware attribute's current_value (watts); wide open when absent."""
    d = current_value.rsplit("/", 1)[0]
    lo = sysfs.read_int(f"{d}/min_value")
    hi = sysfs.read_int(f"{d}/max_value")
    return (lo if lo is not None else 0, hi if hi is not None else 1000)


THERMALD = "thermald.service"


def _systemctl(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=30, check=False,
                              env=sysfs.clean_env())
    except (OSError, subprocess.SubprocessError):
        return None


def thermald_active() -> bool:
    r = _systemctl("is-active", THERMALD)
    return bool(r) and r.stdout.strip() == "active"


class Power:
    def __init__(self, hardware: hw.Hardware):
        self.hw = hardware
        self.p = hardware.paths
        self._hhd_orig: dict | None = None    # HHD's TDP state before ClawDeck's first change, for revert_all
        self.boost = True                     # TDP boost (short bursts above the TDP); off = PL2 is TDP + 1 W
        self._thermald_paused = False         # True while ClawDeck holds a TDP and has stopped thermald (see _set_rapl)

    # ---- readouts --------------------------------------------------------------------------------------------------
    def status(self) -> dict:
        p = self.p
        gpu = {
            "available": p.gpu_min_freq is not None,
            "min_mhz": sysfs.read_int(p.gpu_min_freq) if p.gpu_min_freq else None,
            "max_mhz": sysfs.read_int(p.gpu_max_freq) if p.gpu_max_freq else None,
            "cur_mhz": sysfs.read_int(p.gpu_cur_freq) if p.gpu_cur_freq else None,
            "hw_min_mhz": sysfs.read_int(p.gpu_rpn_freq) if p.gpu_rpn_freq else None,
            "hw_max_mhz": sysfs.read_int(p.gpu_rp0_freq) if p.gpu_rp0_freq else None,
            "efficient_mhz": sysfs.read_int(p.gpu_rpe_freq) if p.gpu_rpe_freq else None,
        }
        cpu = {
            "available": p.cpu_max_perf_pct is not None,
            "max_perf_pct": sysfs.read_int(p.cpu_max_perf_pct) if p.cpu_max_perf_pct else None,
            "min_perf_pct": sysfs.read_int(p.cpu_min_perf_pct) if p.cpu_min_perf_pct else None,
            "no_turbo": sysfs.read_int(p.cpu_no_turbo) if p.cpu_no_turbo else None,
            "epp": self.epp(), "epp_available": bool(p.cpu_epp),
        }
        rapl = {
            "available": p.rapl_package is not None,
            "pl1_w": (sysfs.read_int(p.rapl_pl1) or 0) / 1e6 if p.rapl_pl1 else None,
            "pl2_w": (sysfs.read_int(p.rapl_pl2) or 0) / 1e6 if p.rapl_pl2 else None,
            # what the firmware says PL1 sustains (25 W on the EX); a higher PL1 may or may not take effect
            "max_w": round(v / 1e6) if p.rapl_pl1_max and (v := sysfs.read_int(p.rapl_pl1_max)) else None,
        }
        fw = None
        if p.msi_pl1:
            fw = {"pl1_w": sysfs.read_int(p.msi_pl1), "pl2_w": sysfs.read_int(p.msi_pl2) if p.msi_pl2 else None}
        return {"gpu": gpu, "cpu": cpu, "rapl": rapl, "tdp": hhd.get_tdp(), "hhd": hhd.available(),
                "msi_firmware": p.msi_pl1 is not None, "firmware_tdp": fw, "tdp_backend": self.tdp_backend(),
                "tdp_w": self.read_tdp(), "limits": self.hw.model["tdp"],
                "range": dict(zip(("min", "max"), hw.tdp_range(self.hw.model, True)))}

    def read_tdp(self) -> int | None:
        """The sustained power limit as the hardware reports it now, from the backend that enforces it."""
        if hhd.available():
            return (hhd.get_tdp() or {}).get("custom_tdp")
        if self.p.rapl_pl1:
            v = sysfs.read_int(self.p.rapl_pl1)
            return None if v is None else round(v / 1e6)
        if self.p.msi_pl1:
            return sysfs.read_int(self.p.msi_pl1)
        return None

    # ---- single levers ---------------------------------------------------------------------------------------------
    def clamp_tdp(self, watts: int, plugged: bool | None = None) -> int:
        """The watts set_tdp would really apply: inside the model's limits for this power source."""
        lim = self.hw.model["tdp"]
        return clamp(int(watts), lim["min"], lim["plugged_max"] if plugged else lim["battery_max"])

    def set_tdp(self, watts: int, plugged: bool | None = None) -> bool:
        lim = self.hw.model["tdp"]
        watts = self.clamp_tdp(watts, plugged)
        if hhd.available():
            if not hhd.tdp_ready():
                log.warning("Handheld Daemon's TDP controls are not enabled yet (accept them once in HHD's settings)")
                return False
            if self._hhd_orig is None:
                self._hhd_orig = hhd.get_tdp() or {}
            return hhd.set_tdp(watts, self.boost)
        # no HHD (current Bazzite): RAPL's package domain, which is what the SoC actually enforces. Measured on a
        # Claw 8 EX (BIOS E1T91IMS.10D, 2026-10-21) with an all-core load: the MSI firmware attributes
        # (ppt_pl1_spl / ppt_pl2_sppt) accept any value in range and change nothing - package power stayed at 37 W
        # with PL1 set to 10, 17 or 35 - while intel-rapl-mmio:0 writes cap it exactly (17/24 W -> 23 W measured,
        # 10/12 W -> 11 W). The firmware attributes also refuse their own initial value 0, so they cannot be
        # reverted; ClawDeck leaves them alone whenever RAPL is present. PL2 and PL1 are written in the order that
        # keeps PL2 >= PL1 at every step; revert restores the values read at first write (25/37 W on the EX).
        if self.p.rapl_pl1:
            return self._set_rapl(watts, lim)
        if self.p.msi_pl1:
            return self._set_msi_firmware(watts, lim)
        log.error("no way to set TDP on this system (no Handheld Daemon, no RAPL package domain)")
        return False

    def _sppt_for(self, watts: int, lim: dict) -> int:
        ceiling = max(lim["plugged_max"], lim["battery_max"])
        if self.boost:
            return min(max(round(watts * lim["sppt_max"] / ceiling), watts + 1), lim["sppt_max"])
        return min(watts + 1, lim["sppt_max"])   # boost off: PL2 one watt over PL1, as ClawTweaks does from a single slider

    def _set_rapl(self, watts: int, lim: dict) -> bool:
        # thermald re-applies the platform's DPTF power table to this RAPL domain every few seconds (polling mode):
        # on the Claw 8 EX it put PL1 back to 30 W within 10 s of every write (findings §21). While ClawDeck holds a
        # TDP it stops thermald (stop only, never disable) and starts it again in revert_all(). The SoC's own thermal
        # limit (TCC) and the EC's fan control stay active throughout.
        if not self._thermald_paused and thermald_active():
            r = _systemctl("stop", THERMALD)
            if r is not None and r.returncode == 0:
                self._thermald_paused = True
                log.info("thermald stopped while ClawDeck sets the TDP (it overwrites RAPL PL1); restarted on revert")
            else:
                log.warning("could not stop thermald: it will overwrite the TDP within seconds (%s)",
                            (r.stderr if r else "systemctl failed").strip()[-200:])
        pl2 = self._sppt_for(watts, lim) if self.p.rapl_pl2 else None
        cur_pl1 = sysfs.read_int(self.p.rapl_pl1, 0) // 1_000_000
        raising = watts >= cur_pl1
        writes = [(self.p.rapl_pl1, watts * 1_000_000)]
        if pl2 is not None:
            writes = ([(self.p.rapl_pl2, pl2 * 1_000_000)] + writes) if raising else (writes + [(self.p.rapl_pl2, pl2 * 1_000_000)])
        ok = True
        for path, val in writes:
            ok = sysfs.write(path, val) and ok
        # a second RAPL domain (the MSR one when MMIO is used) must not sit below the target or it becomes the cap
        for path, val in ((self.p.rapl_alt_pl1, watts), (self.p.rapl_alt_pl2, pl2)):
            if path and val is not None and sysfs.read_int(path, 0) < val * 1_000_000:
                sysfs.write(path, val * 1_000_000)
        if not ok:
            for path, _ in writes:
                sysfs.revert(path)
        return ok

    def _set_msi_firmware(self, watts: int, lim: dict) -> bool:
        import time
        sppt = self._sppt_for(watts, lim)
        ok = True
        for path, val in ((self.p.msi_pl3, sppt), (self.p.msi_pl2, sppt), (self.p.msi_pl1, watts)):
            if not path:
                continue
            lo, hi = firmware_range(path)
            ok = sysfs.write(path, clamp(val, lo, hi)) and ok
            time.sleep(0.05)   # the EC applies one limit at a time (HHD waits between writes too)
        if not ok:
            for path in (self.p.msi_pl1, self.p.msi_pl2, self.p.msi_pl3):
                if path:
                    sysfs.revert(path)
        return ok

    def tdp_backend(self) -> str | None:
        if hhd.available():
            return "hhd"
        if self.p.rapl_pl1:
            return "rapl"
        if self.p.msi_pl1:
            return "msi-firmware"
        return None

    def set_cpu_turbo(self, on: bool) -> bool:
        """CPU boost (intel_pstate no_turbo: 0 = boost on). Off is cooler and quieter, and costs fps in CPU-heavy
        scenes; autotune turns it back on when a game is CPU-bound."""
        if not self.p.cpu_no_turbo:
            return False
        return sysfs.write(self.p.cpu_no_turbo, 0 if on else 1)

    def turbo(self) -> bool | None:
        v = sysfs.read_int(self.p.cpu_no_turbo) if self.p.cpu_no_turbo else None
        return None if v is None else v == 0

    def set_cpu_max_pct(self, pct: int) -> bool:
        if not self.p.cpu_max_perf_pct:
            return False
        pct = clamp(int(pct), 10, 100)
        lo = sysfs.read_int(self.p.cpu_min_perf_pct, 0) if self.p.cpu_min_perf_pct else 0
        return sysfs.write(self.p.cpu_max_perf_pct, max(pct, lo or 0))

    EPP_CHOICES = ("performance", "balance_performance", "balance_power", "power")

    def set_epp(self, value: str) -> bool:
        """CPU energy mode (intel_pstate energy_performance_preference): how eagerly the CPU clocks up. The same
        lever as Windows' power mode slider. Written to every CPU, each one revertible."""
        if value not in self.EPP_CHOICES:
            raise ValueError(f"CPU energy mode must be one of {self.EPP_CHOICES}")
        if not self.p.cpu_epp:
            return False
        ok = True
        for path in self.p.cpu_epp:
            ok = sysfs.write(path, value) and ok
        return ok

    def epp(self) -> str | None:
        return sysfs.read(self.p.cpu_epp[0]) if self.p.cpu_epp else None

    def set_gpu_window(self, min_mhz: int | None, max_mhz: int | None) -> bool:
        """GPU clock floor and ceiling. Order matters: when raising, max first; when lowering, min first, so that
        min <= max holds at every step (the kernel rejects a floor above the ceiling)."""
        if not (self.p.gpu_min_freq and self.p.gpu_max_freq):
            return False
        cur_min = sysfs.read_int(self.p.gpu_min_freq) or 0
        cur_max = sysfs.read_int(self.p.gpu_max_freq) or 0
        hw_min = (sysfs.read_int(self.p.gpu_rpn_freq) if self.p.gpu_rpn_freq else None) or 0
        # without the hardware maximum (rp0), the current ceiling is the safe upper bound, never 0
        hw_max = (sysfs.read_int(self.p.gpu_rp0_freq) if self.p.gpu_rp0_freq else None) or max(cur_max, cur_min)
        if not hw_max:
            return False
        new_min = clamp(int(min_mhz), hw_min, hw_max) if min_mhz is not None else cur_min
        new_max = clamp(int(max_mhz), hw_min, hw_max) if max_mhz is not None else cur_max
        if new_min > new_max:
            new_min = new_max
        ok = True
        if new_max >= cur_max:
            ok = sysfs.write(self.p.gpu_max_freq, new_max) and ok
            ok = sysfs.write(self.p.gpu_min_freq, new_min) and ok
        else:
            ok = sysfs.write(self.p.gpu_min_freq, new_min) and ok
            ok = sysfs.write(self.p.gpu_max_freq, new_max) and ok
        return ok

    def reset_gpu_floor(self) -> bool:
        """Only the floor back to what it was before ClawDeck; a ceiling the user set stays."""
        return sysfs.revert(self.p.gpu_min_freq) if self.p.gpu_min_freq else False

    def reset_gpu_window(self) -> bool:
        ok = True
        if self.p.gpu_min_freq:
            ok = sysfs.revert(self.p.gpu_min_freq) and ok
        if self.p.gpu_max_freq:
            ok = sysfs.revert(self.p.gpu_max_freq) and ok
        return ok

    # ---- profiles ----------------------------------------------------------------------------------------------------
    def apply_profile(self, name: str, overrides: dict | None = None, plugged: bool | None = None) -> dict:
        """Apply a named profile (hw.PROFILES) with optional per-game overrides. Returns what was applied and what
        failed; a failed lever reverts itself."""
        name = hw.profile_name(name)
        prof = dict(hw.profiles_for(self.hw.model_key).get(name, {}))
        prof.update(overrides or {})
        result = {"profile": name, "applied": {}, "failed": []}
        if prof.get("tdp") is not None:
            if self.set_tdp(prof["tdp"], plugged):
                result["applied"]["tdp"] = self.clamp_tdp(prof["tdp"], plugged)   # what was really set
            else:
                result["failed"].append("tdp")
        if prof.get("epp") and self.p.cpu_epp:
            if self.set_epp(prof["epp"]):
                result["applied"]["epp"] = prof["epp"]
            else:
                result["failed"].append("epp")
        if prof.get("boost") is not None and self.p.cpu_no_turbo:
            if self.set_cpu_turbo(bool(prof["boost"])):
                result["applied"]["boost"] = bool(prof["boost"])
            else:
                result["failed"].append("boost")
        if prof.get("cpu_max_pct") is not None:
            if self.set_cpu_max_pct(prof["cpu_max_pct"]):
                result["applied"]["cpu_max_pct"] = prof["cpu_max_pct"]
            else:
                result["failed"].append("cpu_max_pct")
                if self.p.cpu_max_perf_pct:
                    sysfs.revert(self.p.cpu_max_perf_pct)
        if prof.get("gpu_min_mhz") is not None or prof.get("gpu_max_mhz") is not None:
            if self.set_gpu_window(prof.get("gpu_min_mhz"), prof.get("gpu_max_mhz")):
                result["applied"]["gpu_window"] = [prof.get("gpu_min_mhz"), prof.get("gpu_max_mhz")]
            else:
                result["failed"].append("gpu_window")
                self.reset_gpu_window()
        return result

    def revert_all(self) -> int:
        """Back to what was there before ClawDeck: every sysfs value it touched, and HHD's TDP mode/value."""
        n = sysfs.revert_all()
        if self._thermald_paused:
            r = _systemctl("start", THERMALD)
            self._thermald_paused = not (r is not None and r.returncode == 0)
            if self._thermald_paused:
                log.error("could not restart thermald after reverting the TDP: run 'sudo systemctl start thermald'")
            else:
                log.info("thermald restarted")
        orig, self._hhd_orig = self._hhd_orig, None
        if orig and orig.get("mode"):
            vals = {hhd.KEY_TDP_MODE: orig["mode"]}
            if orig.get("custom_tdp") is not None:
                vals[hhd.KEY_TDP_CUSTOM] = orig["custom_tdp"]
            if orig.get("boost") is not None:
                vals[hhd.KEY_TDP_BOOST] = orig["boost"]
            if hhd.set_values(vals) is not None:
                n += 1
        return n
