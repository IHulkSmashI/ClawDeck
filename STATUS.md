# ClawDeck status

Updated 2026-10-21 (second pass). **Nothing has been tested on real hardware yet.** Everything below passes the backend tests against
fake sysfs trees and a fake Handheld Daemon, and the UI typechecks and builds into the Decky zip.

## Done (code, tests)

| Module | Backend | UI | Notes |
|---|---|---|---|
| Hardware table + discovery | `hw.py` | Setup | one row per Claw; EX = MS-1T91 (matches HHD's table) |
| Revertible sysfs + dry run | `sysfs.py` | Setup → Dry run | also covers HHD writes now (nothing sent in dry run) |
| Health check + one-tap fixes | `health.py`, `main.run_fix` | Setup | fixes: turn on HHD, install XeSS driver, save a hardware survey |
| Power + profiles | `power.py` | Power | TDP via HHD, else MSI firmware limits (current Bazzite), else RAPL (MMIO first) |
| Which services run | `stack.py` | Setup | InputPlumber / PowerStation / HHD; never offers HHD next to InputPlumber |
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
| Controller | `controller.py` | Controller | with HHD: everything under `controllers.claw.*` / `rgb.handheld.*`; with InputPlumber: status only (next) |
| Fans | `fans.py` | Battery & Fans | presets need HHD; without it the firmware curve runs (direct hwmon curve: next) |
| Sleep | `sleep.py`, `tools/suspend-cycle.sh` | Setup → Advanced | read-only checks; cycle soak tool |
| Battery | `battery.py` | Battery & Fans | 50–100 % charge limit |
| Budget meter | `tools/soak.py` | — | CPU %, peak RSS, RSS slope against the release budget |
| Profiles without the plugin | `tools/power-profile.sh` | — | battery / performance / dock / reset / status, `--dry-run` |

## Checked against upstream sources (2026-10-21, no hardware needed)

- Steam client calls against @decky/ui 4.16 typings; fixed: no install-dir field exists (now read from Steam's
  appmanifest), `Router` imported from @decky/ui.
- Current Bazzite: no Handheld Daemon; InputPlumber + PowerStation + Steam's TDP slider (steamos-manager → MSI
  firmware attributes). ClawDeck now sets TDP the same way when HHD is absent and no longer suggests installing HHD.
- gamescope-session variables (Bazzite uses ChimeraOS's via Terra); no Claw device quirk.
- Release assets and OptiScaler 0.9.4 INI keys (fixed `FGType` → `FGInput`/`FGOutput=xefg`, DX11 → `xess_12`).
- The IHulkSmashI xess-xmx-linux fork has no releases: the installer uses upstream's (`use_fork: false`).

## Tested on real hardware

Nothing yet.

## Next (on the Claw 8 EX, in this order)

1. `tools/sysfs-survey.sh` → paste into an issue; fix any path in `hw.py` that differs.
2. Sideload the CI zip; Setup tab: every check shows a real value.
3. Turn on **Dry run**, apply each profile, read the log; then off, and measure fps/W in two games (Benchmark).
4. Verify the items in docs/DESIGN.md → "Open items" one by one.
5. `tools/soak.py --hours 2` with autotune on, then `--hours 4` for the leak soak.
6. `sudo tools/suspend-cycle.sh 100` before saying anything about sleep.

## Needs the maintainer

- Pin tested versions in `assets/versions.json` (all are `pending-verification`, so installs ask "latest, untested?").
- The fork publishes no releases, so the installer downloads HHHHanasak1's. To ship from the fork, publish a release
  there (its `tools/make-release.py` builds the zip) and set `"use_fork": true`.
- Decide how ClawDeck relates to Bazzite's own OptiScaler and Lossless Scaling Decky plugins (defer to them, or
  replace them).
- Decide the release name on the Decky store (ClawDeck, per the 2026-10-21 decision) and the banner image
  (`assets/banner.png` is referenced in plugin.json but not in the repo yet).
