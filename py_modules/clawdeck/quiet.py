"""Keep ClawDeck's own work off the cores the game runs on while a game runs.

Autotune's loop wakes once a second inside this plugin's backend process (Decky runs each plugin's backend as its
own process). Left alone, the scheduler may put it on a P-core next to the game's render thread. While a game runs,
every thread of this process is moved to the low-power E-cores at nice 10: the Atom cores (/sys/devices/cpu_atom/cpus)
outside the L3 cache, which games don't lean on (Claw 8 EX: P-cores 0-1, E-cores 2-9 on the L3, LP-E cores 10-13
without one, read on the maintainer's Claw 2026-11-04). A chip without LP-E cores uses all its E-cores. At game exit and when the plugin unloads it goes back to every core at nice 0.
Only this process is touched, never the game or the system; nothing is written to sysfs.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("clawdeck.quiet")

ATOM = "/sys/devices/cpu_atom/cpus"
NICE = 10
_on = False


def _cpus(text: str) -> set[int]:
    out: set[int] = set()
    for part in text.strip().split(","):
        if not part:
            continue
        a, _, b = part.partition("-")
        out.update(range(int(a), int(b or a) + 1))
    return out


CPU_SYS = "/sys/devices/system/cpu"


def efficiency_cores(path: str = ATOM, cpu_sys: str = CPU_SYS) -> set[int]:
    """The LP-E cores (Atom cores with no L3 cache), else every Atom core, else none."""
    try:
        with open(path, encoding="utf-8") as f:
            atom = _cpus(f.read())
    except (OSError, ValueError):
        return set()
    lpe = {c for c in atom if not os.path.exists(os.path.join(cpu_sys, f"cpu{c}", "cache", "index3"))}
    return lpe or atom


def _threads() -> list[int]:
    try:
        return [int(t) for t in os.listdir("/proc/self/task")]
    except OSError:
        return [0]


def background(on: bool, path: str = ATOM, cpu_sys: str = CPU_SYS) -> bool:
    """on: this process's threads to the efficiency cores at nice NICE; off: every core, nice 0. Returns whether
    anything was changed. A chip without E-cores only gets the nice value."""
    global _on
    if on == _on:
        return False
    every = set(range(os.cpu_count() or 1))
    cores = (efficiency_cores(path, cpu_sys) & every) if on else every
    for tid in _threads():
        try:
            if cores:
                os.sched_setaffinity(tid, cores)
            os.setpriority(os.PRIO_PROCESS, tid, NICE if on else 0)
        except OSError as e:     # a thread that just ended, or no right to raise priority back: the rest still moves
            log.debug("quiet: thread %s: %s", tid, e)
    _on = on
    log.info("ClawDeck backend %s", f"on cores {sorted(cores)} at nice {NICE}" if on else "back on every core")
    return True
