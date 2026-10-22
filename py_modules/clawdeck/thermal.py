"""Temperature guard for the time thermald is paused (HARDWARE-FINDINGS §21).

thermald rewrites RAPL PL1 every few seconds on the Claw 8 EX, so power.py stops it while ClawDeck holds a TDP. That
also takes away thermald's own temperature control, so while it is stopped this guard reads the package
temperature (the x86_pkg_temp thermal zone, coretemp as a fallback; hw.find_pkg_temp) every CHECK_S seconds:

  above TRIP_C   -> the guard trips: the TDP steps down STEP_W watts,
  while tripped  -> another STEP_W every CHECK_S until the package is under CLEAR_C, never below the floor.

The stepped TDP stays as the most ClawDeck uses for this game (autotune works under it) until the user picks a
profile or moves the slider, or the power is reverted (game closed). The SoC's own limit (TCC) and the EC's fan
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

    def check(self, current_w: int | None, floor_w: int, set_tdp: Callable[[int], bool]) -> int | None:
        """One check. Returns the watts it stepped to, or None when it left the TDP alone."""
        t = read_c(self.path)
        self.temp_c = t
        if t is None or current_w is None:
            return None
        if not self.tripped:
            if t <= TRIP_C:
                return None
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
        self.stepped_w, self.stepped_at = w, time.monotonic()
        log.info("thermal: stepped to %d W (package %.0f °C)", w, t)
        return w

    def reset(self) -> None:
        """The user set a TDP, or the power was reverted: the guard's step no longer applies."""
        self.tripped, self.stepped_w, self.stepped_at = False, None, None

    def status(self) -> dict:
        return {"sensor": self.sensor, "temp_c": None if self.temp_c is None else round(self.temp_c),
                "tripped": self.tripped, "stepped_w": self.stepped_w,
                "line": None if self.stepped_w is None else f"thermal: stepped to {self.stepped_w} W",
                "stepped_s": None if self.stepped_at is None else round(time.monotonic() - self.stepped_at)}
