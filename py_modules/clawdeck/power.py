"""Power profiles: TDP (Handheld Daemon, else MSI firmware attributes, else RAPL), CPU ceiling and GPU clock window (sysfs, PowerControl's paths).

Why three levers: on the Claw the package TDP is shared by CPU and GPU and the firmware splits it dynamically.
Capping the CPU hands the budget to the GPU in GPU-bound games; a GPU floor stops the GPU idling down between
frames; a GPU cap hands the budget back to the CPU in CPU-bound spots. Nothing here overclocks: every value is
clamped to what the hardware reports (rp0/rpn, 0-100 %), and every write is revertible (sysfs.py).
"""
from __future__ import annotations

import logging

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


class Power:
    def __init__(self, hardware: hw.Hardware):
        self.hw = hardware
        self.p = hardware.paths
        self._hhd_orig: dict | None = None    # HHD's TDP state before ClawDeck's first change, for revert_all
        self.boost = True                     # TDP boost (short bursts above the TDP); off = PL2 is TDP + 1 W

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
        }
        fw = None
        if p.msi_pl1:
            fw = {"pl1_w": sysfs.read_int(p.msi_pl1), "pl2_w": sysfs.read_int(p.msi_pl2) if p.msi_pl2 else None}
        return {"gpu": gpu, "cpu": cpu, "rapl": rapl, "tdp": hhd.get_tdp(), "hhd": hhd.available(),
                "msi_firmware": p.msi_pl1 is not None, "firmware_tdp": fw, "tdp_backend": self.tdp_backend(),
                "limits": self.hw.model["tdp"]}

    # ---- single levers ---------------------------------------------------------------------------------------------
    def set_tdp(self, watts: int, plugged: bool | None = None) -> bool:
        lim = self.hw.model["tdp"]
        hi = lim["plugged_max"] if plugged else lim["battery_max"]
        watts = clamp(int(watts), lim["min"], hi)
        if hhd.available():
            if not hhd.tdp_ready():
                log.warning("Handheld Daemon's TDP controls are not enabled yet (accept them once in HHD's settings)")
                return False
            if sysfs.dry_run():
                log.info("dry-run: would set Handheld Daemon TDP to %d W", watts)
                return True
            if self._hhd_orig is None:
                self._hhd_orig = hhd.get_tdp() or {}
            return hhd.set_tdp(watts, self.boost)
        # no HHD (current Bazzite): MSI's firmware attributes, exactly as HHD and steamos-manager write them - watts,
        # PL3 (if the model has it; the EX has none) then PL2 then PL1, boost limit proportional to the model's
        # ceiling, each clamped to the attribute's own min_value/max_value. On the Claw 8 EX, PowerStation reports
        # no TDP range for this chip, so Steam's slider does nothing and ClawDeck is the only writer; on models
        # where Steam's slider works it writes the same files and the last change wins. ClawDeck reverts its value
        # when the game closes.
        if self.p.msi_pl1:
            return self._set_msi_firmware(watts, lim)
        # last resort: RAPL directly (the firmware may re-assert its own value)
        if self.p.rapl_pl1:
            ok = sysfs.write(self.p.rapl_pl1, watts * 1_000_000)
            if self.p.rapl_pl2:
                ok = sysfs.write(self.p.rapl_pl2, min(watts + 10, lim["sppt_max"]) * 1_000_000) and ok
            return ok
        log.error("no way to set TDP on this system (no Handheld Daemon, no RAPL package domain)")
        return False

    def _set_msi_firmware(self, watts: int, lim: dict) -> bool:
        import time
        ceiling = max(lim["plugged_max"], lim["battery_max"])
        sppt = min(max(round(watts * lim["sppt_max"] / ceiling), watts + 1), lim["sppt_max"]) if self.boost else \
            min(watts + 1, lim["sppt_max"])   # boost off: PL2 one watt over PL1, as ClawTweaks does from a single slider
        ok = True
        for path, val in ((self.p.msi_pl3, sppt), (self.p.msi_pl2, sppt), (self.p.msi_pl1, watts)):
            if not path:
                continue
            lo, hi = firmware_range(path)
            ok = sysfs.write(path, clamp(val, lo, hi)) and ok
            if not sysfs.dry_run():
                time.sleep(0.05)   # the EC applies one limit at a time (HHD waits between writes too)
        if not ok:
            for path in (self.p.msi_pl1, self.p.msi_pl2, self.p.msi_pl3):
                if path:
                    sysfs.revert(path)
        return ok

    def tdp_backend(self) -> str | None:
        if hhd.available():
            return "hhd"
        if self.p.msi_pl1:
            return "msi-firmware"
        if self.p.rapl_pl1:
            return "rapl"
        return None

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

    def set_cpu_turbo(self, on: bool) -> bool:
        if self.p.cpu_no_turbo:
            return sysfs.write(self.p.cpu_no_turbo, 0 if on else 1)
        if self.p.cpu_boost:
            return sysfs.write(self.p.cpu_boost, 1 if on else 0)
        return False

    def set_gpu_window(self, min_mhz: int | None, max_mhz: int | None) -> bool:
        """GPU clock floor and ceiling. Order matters: when raising, max first; when lowering, min first, so that
        min <= max holds at every step (the kernel rejects a floor above the ceiling)."""
        if not (self.p.gpu_min_freq and self.p.gpu_max_freq):
            return False
        hw_min = sysfs.read_int(self.p.gpu_rpn_freq, 0) if self.p.gpu_rpn_freq else 0
        hw_max = sysfs.read_int(self.p.gpu_rp0_freq, 0) if self.p.gpu_rp0_freq else 0
        cur_min = sysfs.read_int(self.p.gpu_min_freq, hw_min) or 0
        cur_max = sysfs.read_int(self.p.gpu_max_freq, hw_max) or 0
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

    def reset_gpu_window(self) -> bool:
        ok = True
        if self.p.gpu_min_freq:
            ok = sysfs.revert(self.p.gpu_min_freq) and ok
        if self.p.gpu_max_freq:
            ok = sysfs.revert(self.p.gpu_max_freq) and ok
        return ok

    # ---- profiles ----------------------------------------------------------------------------------------------------
    def apply_profile(self, name: str, overrides: dict | None = None, plugged: bool | None = None) -> dict:
        """Apply a named profile (battery / performance / dock) with optional per-game overrides. Returns what was
        applied and what failed; a failed lever reverts itself."""
        prof = dict(hw.profiles_for(self.hw.model_key).get(name, {}))
        prof.update(overrides or {})
        result = {"profile": name, "applied": {}, "failed": []}
        if prof.get("tdp") is not None:
            if self.set_tdp(prof["tdp"], plugged):
                result["applied"]["tdp"] = prof["tdp"]
            else:
                result["failed"].append("tdp")
        if prof.get("epp") and self.p.cpu_epp:
            if self.set_epp(prof["epp"]):
                result["applied"]["epp"] = prof["epp"]
            else:
                result["failed"].append("epp")
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
