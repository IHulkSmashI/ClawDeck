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
   injector.py  per-game plan (native XeSS -> OptiScaler -> lsfg-vk), OptiScaler install like setup_linux.sh +
                OptiPatcher / REFramework / INI fixes, exact undo, lsfg-vk layer install
   gamescan.py  what a game ships: upscalers, frame generation, engine, anti-cheat (file names only)
   compat.py    OptiScaler's compatibility list + OptiPatcher's supported exes, cached a day
   updates.py   newest OptiScaler / OptiPatcher / REFramework / lsfg-vk / Moonlight VRR, daily, checksum-verified where upstream publishes sums,
                previous version kept for rollback
   streaming.py Moonlight VRR (Nonary/moonlight-qt) unpacked once, one launcher, stream shortcut args
   changes.py   what changed since the last start (kernel, Mesa, OS, controller service, XeSS, Decky)
   launchenv.py per-game <runtime>/launch/<appid>.env: injector, DXVK_HDR, MESA= (read by clawdeck-launch.sh)
   health.py    checks + one-tap bug report (fixes run through main.run_fix)
   buttons.py   Claw 8 EX face buttons: InputPlumber override (claw8ex map) for MS-1T91 (apply / remove / status)
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

## Per-game upscaling (OptiClick for the Claw)
For the running game the backend scans the install folder, looks the game up in OptiScaler's compatibility list
and in OptiPatcher's source, and returns one plan with its reasons. Order: the game's own XeSS (nothing to install;
XMX with the xess-xmx-linux driver) → OptiScaler when the game has DLSS or FSR and the list doesn't mark it broken
(XeSS SR + XeFG; FG input = the game's own DLSS-FG / FSR-FG / XeFG, else the upscaler; Dxgi spoofing only for
DLSS-only games without OptiPatcher; InterpolationCount 3x with the XeSS driver, 2x without; no XeFG in Vulkan
games) → lsfg-vk for everything else. Anti-cheat folders block it. The install copies the whole release next to the
main exe, takes the first free proxy name, writes WINEDLLOVERRIDES through the launch wrapper (plus dinput8 for
REFramework), and lists every file in .clawdeck-inject.json for an exact undo. Updates: upstream is asked at most
once a day; a newer release is downloaded after a game closes and that game is reinstalled with the same options.
lsfg-vk needs Lossless Scaling (Steam 993090): the frontend checks ownership with appStore, installs it with
Installs.OpenInstallWizard or opens its store page. lsfg-vk runs on shader cores; only XeSS uses XMX.

## Streaming
Moonlight VRR's AppImage is verified against the release's SHA256SUMS, unpacked as the user with --appimage-extract
(no FUSE needed) into ~/.local/share/clawdeck/moonlight/<version>/, and started through one launcher script that
reads CURRENT, so Steam shortcuts (SteamClient.Apps.AddShortcut) survive updates. Two versions are kept. A saved
host adds a shortcut with launch options `stream <host> "<app>"` (moonlight-qt's CLI). Pairing stays in Moonlight.

## Who owns what
Handheld Daemon keeps its own state (TDP, fans, controller, RGB, its charge limit) and persists it; ClawDeck changes
HHD values only when the user asks, and restores HHD's TDP mode/value when a game closes. Steam owns launch options
and compatibility tools; ClawDeck never edits config.vdf. gamescope-session reads environment.d; ClawDeck writes only
its own file there.

## Who sets the TDP
Bazzite stopped shipping Handheld Daemon on handhelds in 2026: it enables InputPlumber (controller) and PowerStation,
and Steam's performance slider sets the Claw's TDP through steamos-manager, which writes MSI's firmware attributes
(`msi-wmi-platform` `ppt_pl1_spl` / `ppt_pl2_sppt` / `ppt_pl3_fppt`, watts). `stack.py` detects which services run.
ClawDeck's TDP order: HHD when it runs (`tdp.msi.tdp.mode=custom` + `tdp.msi.tdp.custom.tdp`, read back, HHD's
mode restored at game exit); else the MSI firmware attributes, written like HHD does (PL3, PL2, PL1 with a short
pause; boost = TDP × sppt_max / ceiling, PL3 = PL2, all reverted at game exit); else RAPL, MMIO domain first as
PowerStation does. With the firmware path, Steam's slider and ClawDeck write the same limits and the last change wins.
ClawDeck never starts HHD while InputPlumber runs (two controller daemons fight).

## Autotune controller (one lever per step, hysteresis)
- frame time ≤ target×1.03 for `settle` samples → TDP −1 W (until floor, or until confidence ≥ 3)
- frame time > target for 2 samples → GPU-bound: GPU floor +100 MHz; CPU-bound: CPU ceiling +10 %; else TDP +2 W
- 60 unchanged samples → confidence +1 (max 5); a miss costs 1. Saved per game and per power source.
- never fights the user: a manual change pauses learning for the session.

## Open items before 0.1 ships
- sysfs survey from a Claw 8 EX on Bazzite (paths assumed from PowerControl/HHD; verify `tile0/gt0/freq0`, powercap
  names, `charge_control_end_threshold` presence).
- Which services run on the Claw 8 EX install (`tools/sysfs-survey.sh` "services"): InputPlumber + PowerStation
  (current Bazzite) or HHD; the firmware attributes writable and whether steamos-manager re-asserts its own TDP.
- HHD key names, only if HHD is used (`hhdctl get | grep -E 'tdp.msi|charge'`). Checked 2026-10-21 in HHD's
  source: `tdp.msi.*`, `gamemode.battery.charge_limit` p70/p80/p85/p90/p95, fan points `st0`..`st88` titled 40C..90C.
- MangoHud CSV column order for the installed version (fps, frametime first two columns assumed).
- Verified 2026-10-21 against @decky/ui 4.16's Steam typings: `GameSessions.RegisterForAppLifetimeNotifications`
  (`unAppID`, `bRunning`), `System.RegisterForBatteryStateChanges` (`eACState`), `System.RegisterForOnResumeFromSuspend`,
  `Apps.SpecifyCompatTool(appId, strToolName)`, `Apps.GetAvailableCompatTools` (`strToolName`, `strDisplayName`),
  `Router.MainRunningApp` (`appid`, `display_name`). There is no install-dir field: the backend reads
  `steamapps/appmanifest_<appid>.acf`.
- HHD's controller/RGB keys on the installed version: the tab shows what the schema offers; check gyro and RGB
  appear under `controllers.claw.*` / `rgb.handheld.*` on the EX.
- HHD fan curve point titles ("40C" ... "90C") on the EX; presets are computed from them.
- gamescope-session: verified 2026-10-21. Bazzite installs gamescope-session-plus from Terra (ChimeraOS's), which
  sources ~/.config/environment.d and reads `ADAPTIVE_SYNC`, `CUSTOM_REFRESH_RATES`, `ENABLE_GAMESCOPE_HDR`,
  `GAMESCOPE_HDR_NITS`; its device-quirks has no Claw entry. Still to see on the Claw: that the Steam refresh slider
  offers the range.
- lsfg-vk layer manifest name (detection matches `*lsfg*.json` in the implicit layer dirs) and env mode on the
  installed version.
- Release assets verified 2026-10-21: xess-xmx-linux `xess-xmx-linux-20261018.zip` (upstream; the fork has no
  releases), OptiScaler `Optiscaler_0.9.4-final.20260718._MM.7z`, XeSS SDK `XeSS_SDK_3.0.2.zip`. OptiScaler.ini keys
  checked against 0.9.4 (`FGInput`/`FGOutput=xefg`, no `FGType`). Still open: whether the XeSS SDK 3.x DLLs work
  with the xess-xmx-linux shim (its kernel map is for 2.0.2.68) — pin 2.x if not.
- Bazzite Portal offers OptiScaler (Decky Framegen) and Lossless Scaling (decky-lsfg-vk) plugins: detected and
  named; a game they set up is left alone.
- On the Claw: the OptiScaler overlay (Insert) shows XeSS + XeFG in Dragon's Dogma 2 with REFramework; OptiPatcher
  shows "(OP)" in a supported UE game; lsfg-vk in a game without an upscaler; 7z or bsdtar present on Bazzite.
- Intel's official Proton path: the built-in `libxess.dll` location inside a Proton build that carries PR 10289.
- `/sys/devices/system/cpu/cpuidle/low_power_idle_system_residency_us` present on Panther Lake.
