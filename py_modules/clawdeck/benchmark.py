"""Benchmark: record fps and watts for a game with fixed settings, so Windows-vs-Linux and profile-vs-profile claims
carry numbers. A run uses the same samplers as autotune (MangoHud frame times, RAPL package power), at 1 Hz, only
while the user has a run going; results append to benchmarks.json (and are exportable as CSV for a post).

Summary per run: average fps, 1 % low (from per-second fps), average package watts, fps per watt, duration, and the
power settings that were active (profile / TDP / CPU / GPU) plus kernel and Mesa, so a result is reproducible."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import time

from . import hw
from .autotune import Sampler

log = logging.getLogger("clawdeck.benchmark")


def summarise(fps: list[float], watts: list[float]) -> dict:
    fps = [f for f in fps if f and f > 0]
    watts = [w for w in watts if w and w > 0]
    if not fps:
        return {"samples": 0}
    srt = sorted(fps)
    n_low = max(1, len(srt) // 100)
    avg = sum(fps) / len(fps)
    low = sum(srt[:n_low]) / n_low
    w = sum(watts) / len(watts) if watts else None
    return {"samples": len(fps), "avg_fps": round(avg, 1), "low1_fps": round(low, 1),
            "avg_w": None if w is None else round(w, 2), "fps_per_w": None if not w else round(avg / w, 2)}


class Benchmark:
    def __init__(self, hardware: hw.Hardware, mangohud_dir: str, results_path: str):
        self.hw = hardware
        self.sampler = Sampler(hardware, mangohud_dir)
        self.path = results_path
        self.run: dict | None = None
        self.task: asyncio.Task | None = None

    def start(self, appid: str, name: str, label: str, settings: dict, duration_s: int = 120) -> dict:
        if self.run:
            return {"ok": False, "error": "a run is already going"}
        self.sampler.frametime()        # skip what is already in the log
        self.sampler.package_power()
        self.run = {"appid": str(appid), "name": name, "label": label, "settings": settings,
                    "started": time.time(), "duration_s": int(duration_s), "fps": [], "w": []}
        self.task = asyncio.get_event_loop().create_task(self._loop())
        return {"ok": True}

    async def _loop(self) -> None:
        while self.run is not None:
            await asyncio.sleep(1.0)
            r = self.run
            if r is None:
                break
            try:
                s = self.sampler.sample()
                if s.fps is not None:
                    r["fps"].append(s.fps)
                if s.pkg_w is not None:
                    r["w"].append(s.pkg_w)
            except Exception as e:   # a sampling error must not kill the run
                log.exception("benchmark sample failed: %s", e)
            if time.time() - r["started"] >= r["duration_s"]:
                self.stop()

    def status(self) -> dict:
        r = self.run
        if not r:
            return {"running": False}
        return {"running": True, "appid": r["appid"], "label": r["label"], "elapsed_s": int(time.time() - r["started"]),
                "duration_s": r["duration_s"], **summarise(r["fps"], r["w"])}

    def stop(self) -> dict | None:
        r, self.run = self.run, None
        if not r:
            return None
        res = {"appid": r["appid"], "name": r["name"], "label": r["label"], "settings": r["settings"],
               "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["started"])),
               "duration_s": int(time.time() - r["started"]), "kernel": self.hw.kernel, "mesa": hw.mesa_version(),
               "model": self.hw.model["name"], **summarise(r["fps"], r["w"])}
        if res.get("samples"):
            all_ = self.results()
            all_.append(res)
            self._save(all_)
        return res

    def results(self) -> list[dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, results: list[dict]) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        os.replace(tmp, self.path)

    def clear(self) -> None:
        self._save([])

    def export_csv(self) -> str:
        buf = io.StringIO()
        cols = ["time", "model", "name", "appid", "label", "avg_fps", "low1_fps", "avg_w", "fps_per_w", "duration_s",
                "kernel", "mesa", "settings"]
        w = csv.writer(buf)
        w.writerow(cols)
        for r in self.results():
            w.writerow([json.dumps(r.get(c)) if c == "settings" else r.get(c) for c in cols])
        return buf.getvalue()
