# ClawDeck design notes

## Architecture
```
Steam Quick Access (React, @decky/ui)        src/          six tabs; every control has a 2–3 bullet Hint
        │  callable()                         src/api.ts    one typed function per backend call
Decky backend (Python, root)                  main.py       thin dispatch, game lifecycle, settings
        │
py_modules/clawdeck/
   hw.py        model table (one row per Claw) + sysfs path discovery (PowerControl's xe/i915 paths, HHD's MSI paths)
   sysfs.py     every write recorded → revert_all(); dry-run
   hhd.py       Handheld Daemon over /run/hhd/api: TDP (custom mode), fan mode, charge limit 70–95
   power.py     levers (TDP via HHD, CPU max_perf_pct, GPU min/max) + named profiles with per-game overrides
   battery.py   status, plugged detection, charge limit 50–100 (HHD for 70–95, sysfs for the rest)
   profiles.py  per-game JSON, battery/plugged slots, import/export
   autotune.py  1 Hz sampler (MangoHud CSV, RAPL, /proc/stat), classifier, hysteresis controller, CSV log
   injector.py  OptiScaler / lsfg-vk per game, backups, INI editing, asset staging, lsfg-vk detection
   launchenv.py per-game <runtime>/launch/<appid>.env: injector, DXVK_HDR, MESA= (read by clawdeck-launch.sh)
   health.py    checks + one-tap bug report (fixes run through main.run_fix)
   controller.py  HHD controllers.claw.* / rgb.handheld.*, discovered from HHD's settings schema
   fans.py      fan curve presets on HHD's tdp.msi.fan.manual.st<N> points; RPM/temperature readout
   display.py   gamescope-session variables in ~/.config/environment.d/60-clawdeck-display.conf (on Save only)
   sleep.py     read-only suspend / S0ix / wake-source checks
   xess.py      xess-xmx-linux: state from its env file + driver-status, install, probe, default driver
   downloads.py pinned GitHub releases (assets/versions.json), safe extraction
   userexec.py  run user-session installers as the desktop user, never as root
   proton.py    installed compat tools (the choice itself goes through SteamClient.Apps.SpecifyCompatTool)
   benchmark.py fps / 1 % low / watts recorder
tools/
   clawdeck-launch.sh   %command% wrapper: MangoHud log for autotune + per-game env file
   sysfs-survey.sh      read-only survey for a new model
   power-profile.sh     profiles from a terminal, no plugin needed
   suspend-cycle.sh     N rtcwake cycles with S0ix residency per cycle
   soak.py              CPU % / RSS / RSS slope of the backend against the release budget
```

## Events instead of timers
The backend never polls. The frontend registers Steam's own notifications and forwards them:
`GameSessions.RegisterForAppLifetimeNotifications` (game start/stop), `System.RegisterForBatteryStateChanges`
(eACState 2 = AC, 1 = battery: the running game switches profile slot), `System.RegisterForOnResumeFromSuspend`
(re-apply charge limit and the running game). Timers exist only while autotune runs for a game or a benchmark records.

## Who owns what
Handheld Daemon keeps its own state (TDP, fans, controller, RGB, its charge limit) and persists it; ClawDeck changes
HHD values only when the user asks, and restores HHD's TDP mode/value when a game closes. Steam owns launch options
and compatibility tools; ClawDeck never edits config.vdf. gamescope-session reads environment.d; ClawDeck writes only
its own file there.

## Why Handheld Daemon owns TDP
HHD already drives the Claw's firmware (`msi-wmi-platform` PL1/PL2/PL3), re-asserts it when the EC fights back,
handles the MSI buttons and fan curves, and is maintained by people with every Claw model. Writing RAPL ourselves
would race it. ClawDeck sets `tdp.msi.tdp.mode=custom` + `tdp.msi.tdp.custom.tdp` and reads back what HHD accepted.
RAPL is only the fallback when HHD is absent.

## Autotune controller (one lever per step, hysteresis)
- frame time ≤ target×1.03 for `settle` samples → TDP −1 W (until floor, or until confidence ≥ 3)
- frame time > target for 2 samples → GPU-bound: GPU floor +100 MHz; CPU-bound: CPU ceiling +10 %; else TDP +2 W
- 60 unchanged samples → confidence +1 (max 5); a miss costs 1. Saved per game and per power source.
- never fights the user: a manual change pauses learning for the session.

## Open items before 0.1 ships
- sysfs survey from a Claw 8 EX on Bazzite (paths assumed from PowerControl/HHD; verify `tile0/gt0/freq0`, powercap
  names, `charge_control_end_threshold` presence).
- HHD key names verified against the installed version (`hhdctl get | grep -E 'tdp.msi|charge'`).
- MangoHud CSV column order for the installed version (fps, frametime first two columns assumed).
- Steam app-lifetime hook name in the current Steam client (`SteamClient.GameSessions.RegisterForAppLifetimeNotifications`).
- Game install dir from `appStore.GetAppOverviewByAppID(appid).m_strInstallDir` — verify field name.
- `SteamClient.Apps.SpecifyCompatTool(appid, name)` / `GetAvailableCompatTools(appid)` field names
  (`strToolName`, `strDisplayName`); the backend list is the fallback.
- HHD's controller/RGB keys on the installed version: the tab shows what the schema offers; check gyro and RGB
  appear under `controllers.claw.*` / `rgb.handheld.*` on the EX.
- HHD fan curve point titles ("40C" ... "90C") on the EX; presets are computed from them.
- gamescope-session on Bazzite reads `ADAPTIVE_SYNC`, `CUSTOM_REFRESH_RATES`, `ENABLE_GAMESCOPE_HDR`,
  `GAMESCOPE_HDR_NITS` from environment.d (verified in ChimeraOS gamescope-session; Bazzite's fork to be checked), and
  whether a device quirk for the Claw already sets the refresh list.
- lsfg-vk layer manifest name (detection matches `*lsfg*.json` in the implicit layer dirs) and env mode on the
  installed version.
- xess-xmx-linux release asset name on the fork (`xess-xmx-linux-<v>.zip`, from its make-release.py).
- Intel's official Proton path: the built-in `libxess.dll` location inside a Proton build that carries PR 10289.
- `/sys/devices/system/cpu/cpuidle/low_power_idle_system_residency_us` present on Panther Lake.
