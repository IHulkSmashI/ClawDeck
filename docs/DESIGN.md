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
   injector.py  OptiScaler / lsfg-vk per game, backups, INI editing, launch env
   health.py    checks + one-tap bug report
tools/
   clawdeck-launch.sh   %command% wrapper: MangoHud log for autotune + per-game injector env
   sysfs-survey.sh      read-only survey for a new model
```

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
