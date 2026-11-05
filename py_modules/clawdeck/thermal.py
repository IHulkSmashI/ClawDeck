"""Temperature guard for the time thermald is paused (HARDWARE-FINDINGS §21).

thermald rewrites RAPL PL1 every few seconds on the Claw 8 EX, so power.py stops it while ClawDeck holds a TDP. That
also takes away thermald's own temperature control, so while it is stopped this guard reads the package
temperature (the x86_pkg_temp thermal zone, coretemp as a fallback; hw.find_pkg_temp) every CHECK_S seconds:

  above TRIP_C   -> the guard trips: the TDP steps down STEP_W watts,
  while tripped  -> another STEP_W every CHECK_S until the package is under CLEAR_C, never below the floor.

  stepped, cool  -> under RECOVER_C for RECOVER_CHECKS checks in a row: +STEP_W back toward your limit, again every
                    RECOVER_CHECKS checks, until it is back at your limit (then the step is gone).

It used to stay stepped until a new pick or the game closed: one hot minute at 35 W left the Claw at 21 W for the rest
of the session at 55 °C (maintainer, 2026-11-04). Autotune works under the stepped TDP while it runs. The SoC's own limit (TCC) and the EC's fan
control stay active the whole time; this guard only keeps ClawDeck's TDP from pushing the chip into them.
Cost: one small file read every 10 s, only while thermald is paused.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from . import sysfs

log = logging.getLogger("clawdeck.thermal")

TRIP_C = 95.0
CLEAR_C = 90.0
STEP_W = 2
CHECK_S = 10
RECOVER_C = 80.0        # well under the trip: a step back up only once the package has clearly cooled
RECOVER_CHECKS = 3      # ... for 30 s, and the next step 30 s after that (so it doesn't bounce off TRIP_C)


def read_c(path: str | None) -> float | None:
    v = sysfs.read_int(path) if path else None
    return None if v is None else v / 1000.0


class Guard:
    def __init__(self, temp_path: str | None, sensor: str | None = None):
        self.path, self.sensor = temp_path, sensor
        self.tripped = False
        self.stepped_w: int | None = None     # the TDP the guard brought ClawDeck down to (None: never stepped)
        self.temp_c: float | None = None
        self.stepped_at: float | None = None
        self.cool = 0                         # checks in a row under RECOVER_C while stepped

    def check(self, current_w: int | None, floor_w: int, set_tdp: Callable[[int], bool],
              limit_w: int | None = None, raise_tdp: Callable[[int], bool] | None = None) -> int | None:
        """One check. Returns the watts it stepped to (down or back up), or None when it left the TDP alone.
        limit_w: your limit, where stepping back up stops; raise_tdp: how to go back up (default set_tdp)."""
        t = read_c(self.path)
        self.temp_c = t
        if t is None or current_w is None:
            return None
        if not self.tripped:
            if t <= TRIP_C:
                return self._recover(t, current_w, limit_w, raise_tdp or set_tdp)
            self.tripped = True
            log.warning("package %.0f °C (over %.0f °C) while thermald is paused: stepping the TDP down", t, TRIP_C)
        if t < CLEAR_C:
            self.tripped = False
            log.info("package %.0f °C: under %.0f °C, TDP stays at %s W", t, CLEAR_C, self.stepped_w or current_w)
            return None
        w = max(int(floor_w), int(current_w) - STEP_W)
        if w >= current_w:
            return None                                      # already at the floor: the SoC's own limit takes over
        if not set_tdp(w):
            log.warning("thermal guard: %d W was not applied", w)
            return None
        self.stepped_w, self.stepped_at, self.cool = w, time.monotonic(), 0
        log.info("thermal: stepped to %d W (package %.0f °C)", w, t)
        return w

    def _recover(self, t: float, current_w: int, limit_w: int | None, up: Callable[[int], bool]) -> int | None:
        if self.stepped_w is None or limit_w is None:
            return None
        if t >= RECOVER_C:
            self.cool = 0
            return None
        self.cool += 1
        if self.cool < RECOVER_CHECKS:
            return None
        self.cool = 0
        w = min(int(limit_w), max(int(current_w), self.stepped_w) + STEP_W)
        if not up(w):
            log.warning("thermal guard: back up to %d W was not applied", w)
            return None
        if w >= int(limit_w):
            log.info("thermal: package %.0f °C, back at your %d W", t, w)
            self.stepped_w, self.stepped_at = None, None
        else:
            self.stepped_w, self.stepped_at = w, time.monotonic()
            log.info("thermal: package %.0f °C, back up to %d W (your limit %d W)", t, w, limit_w)
        return w

    def reset(self) -> None:
        """The user set a TDP, or the power was reverted: the guard's step no longer applies."""
        self.tripped, self.stepped_w, self.stepped_at, self.cool = False, None, None, 0

    def status(self) -> dict:
        return {"sensor": self.sensor, "temp_c": None if self.temp_c is None else round(self.temp_c),
                "tripped": self.tripped, "stepped_w": self.stepped_w,
                "line": None if self.stepped_w is None else f"thermal: stepped to {self.stepped_w} W",
                "stepped_s": None if self.stepped_at is None else round(time.monotonic() - self.stepped_at)}
