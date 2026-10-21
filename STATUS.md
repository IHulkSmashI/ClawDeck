# ClawDeck status

Updated 2026-10-21. **Nothing has been tested on real hardware yet.** Everything below passes the backend tests against
fake sysfs trees and a fake Handheld Daemon, and the UI typechecks and builds into the Decky zip.

## Done (code, tests)

| Module | Backend | UI | Notes |
|---|---|---|---|
| Hardware table + discovery | `hw.py` | Setup | one row per Claw; EX = MS-1T91 (matches HHD's table) |
| Revertible sysfs + dry run | `sysfs.py` | Setup → Dry run | also covers HHD writes now (nothing sent in dry run) |
| Health check + one-tap fixes | `health.py`, `main.run_fix` | Setup | fixes: turn on HHD, install XeSS driver, save a hardware survey |
| Power + profiles | `power.py` | Power | HHD's TDP mode/value is now restored when the game closes |
| Per-game profiles, AC follow | `profiles.py`, `main.power_source_changed` | Power | Steam's battery-state event switches battery ↔ plugged slot mid-game |
| Profile import/export | `profiles.py` | Power → Advanced | JSON on Desktop; import from Desktop/Downloads, never overwrites |
| Autotune | `autotune.py` | Power | pauses on any manual change; saved TDP clamped to the current ceiling |
| Benchmark | `benchmark.py` | Power → Advanced | avg fps, 1 % low, watts, fps/W; CSV export |
| XMX injector: OptiScaler | `injector.py` | Graphics | download from GitHub releases, backup/undo |
| XMX injector: lsfg-vk | `injector.py`, `launchenv.py` | Graphics | `LSFGVK_ENV=1` + multiplier/flow/performance per game |
| Per-game launch env | `launchenv.py`, `tools/clawdeck-launch.sh` | Graphics | injector, `DXVK_HDR`, `MESA=` choice; merges user DLL overrides |
| XeSS driver | `xess.py`, `downloads.py` | Graphics | xess-xmx-linux state, install from fork release, probe, default Mesa |
| Proton picker | `proton.py` + `SteamClient.Apps.SpecifyCompatTool` | Graphics | Steam stays owner of config.vdf |
| Display and dock | `display.py` | Display | VRR, refresh range, HDR, SDR nits → `~/.config/environment.d/60-clawdeck-display.conf` on Save |
| Controller | `controller.py` | Controller | everything HHD offers under `controllers.claw.*` and `rgb.handheld.*`, from HHD's schema |
| Fans | `fans.py` | Battery & Fans | Auto / Quiet / Balanced / Cool on HHD's MSI fan curve |
| Sleep | `sleep.py`, `tools/suspend-cycle.sh` | Setup → Advanced | read-only checks; cycle soak tool |
| Battery | `battery.py` | Battery & Fans | 50–100 % charge limit |
| Budget meter | `tools/soak.py` | — | CPU %, peak RSS, RSS slope against the release budget |
| Profiles without the plugin | `tools/power-profile.sh` | — | battery / performance / dock / reset / status, `--dry-run` |

## Tested on real hardware

Nothing yet.

## Next (on the Claw 8 EX, in this order)

1. `tools/sysfs-survey.sh` → paste into an issue; fix any path in `hw.py` that differs.
2. Sideload the CI zip; Setup tab: every check shows a real value.
3. Turn on **Dry run**, apply each profile, read the log; then off, and measure fps/W in two games (Benchmark).
4. Verify the items in docs/DESIGN.md → "Open items" one by one.
5. `tools/soak.py --hours 2` with autotune on, then `--hours 4` for the leak soak.
6. `sudo tools/suspend-cycle.sh 100` before saying anything about sleep.

## Needs Mena

- Pin tested versions in `assets/versions.json` (all are `pending-verification`, so installs ask "latest, untested?").
- Does IHulkSmashI/xess-xmx-linux publish release zips (`xess-xmx-linux-<v>.zip`)? The installer downloads from the
  fork; if the fork has no releases, set `"use_fork": false` to use HHHHanasak1's.
- Decide the release name on the Decky store (ClawDeck, per the 2026-10-21 decision) and the banner image
  (`assets/banner.png` is referenced in plugin.json but not in the repo yet).
