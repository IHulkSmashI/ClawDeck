# ClawDeck design notes

## Scope (reset 2026-10-21)
ClawDeck does only what Bazzite and Steam's Quick Access do **not** already do. Gone from the UI: VRR on/off,
refresh rate, HDR, per-game performance settings, fan presets, GPU clock windows, CPU ceiling, EPP, charge limit,
sleep, dock readouts, benchmark, Proton picker, streaming (Steam's own panels, InputPlumber and PowerStation cover
them). Priority, in order; nothing lower starts until the line above works on the maintainer's Claw:

1. **XeSS XMX + XeLL** — the one thing missing from Bazzite. Installed from our fork (PRs #23/#24: XeLL driver
   mode, GPU timestamps, review fixes), upstream's release as the fallback while the fork has none.
2. **Controller and VRR fixes** — face buttons (InputPlumber override), the 30–120 Hz EDID fix (install / remove).
3. **OptiScaler injection per game** with XeSS XMX + XeLL DLLs; lsfg-vk as the fallback. Bonus, not core.
4. **Power + autotune** — profiles through RAPL-MMIO, as a dropdown plus the Autotune switch.

## Architecture
```
Steam Quick Access (React, @decky/ui)        src/          four panels; every control explains itself in its description
        │  callable()                         src/api.ts    one typed function per backend call (wiring test)
Decky backend (Python, root)                  main.py       thin dispatch, game lifecycle, settings
        │
py_modules/clawdeck/
   hw.py        model table (one row per Claw) + sysfs path discovery
   sysfs.py     every write recorded → revert_all(); dry-run; last_error() for the UI's red rows
   power.py     levers (TDP via RAPL-MMIO on the EX, HHD / MSI firmware elsewhere; CPU max_perf_pct; GPU window)
                + the four profiles (hw.PROFILES)
   battery.py   plugged detection only (profiles differ on battery / charger)
   profiles.py  per-game JSON: base profile, target fps, what autotune learned, launch settings
   autotune.py  1 Hz sampler (MangoHud CSV, RAPL, /proc/stat, PowerStation GPU busy), 10 s window, CPU/GPU balance
   xess.py      xess-xmx-linux: fork-first install with upstream fallback, state, per-game XeLL mode from the shim's logs
   launchenv.py per-game <runtime>/launch/<appid>.env: injector, XeSS XMX off, XeLL driver mode, MESA= (read by the wrapper)
   injector.py  per-game plan (native XeSS → OptiScaler → lsfg-vk), OptiScaler install like setup_linux.sh, exact undo
   gamescan.py  what a game ships: upscalers, frame generation, engine, anti-cheat (file names only)
   compat.py    OptiScaler's compatibility list + OptiPatcher's supported exes, cached a day
   updates.py   newest OptiScaler / OptiPatcher / REFramework / lsfg-vk, daily, checksum-verified where published
   edid.py      ClawLab's VRR range fix for Linux (validated panels only; drm.edid_firmware + initramfs-etc)
   buttons.py   Claw 8 EX face buttons: InputPlumber override (claw8ex map) for MS-1T91
   health.py    checks + one-line summary + the bug report
   changes.py   what changed since the last start (kernel, Mesa, OS, controller service, XeSS, Decky)
   stack.py     which services run (InputPlumber / PowerStation / HHD) and which other Decky plugins are installed
   shadowblip.py  busctl reads of PowerStation (GPU busy) and InputPlumber
   downloads.py pinned GitHub releases (assets/versions.json), safe extraction;  tls.py  the CA bundle Decky's Python lacks
   userexec.py  run user-session installers as the desktop user, never as root
   hhd.py       Handheld Daemon client (only when it runs)
tools/
   clawdeck-launch.sh   %command% wrapper: MangoHud log for autotune + per-game env file + XeLL log rotation
   selftest.py          every control written, read back, measured, reverted (Setup → Run self-test)
   sysfs-survey.sh      read-only survey for a new model;  power-profile.sh  profiles from a terminal
   suspend-cycle.sh, soak.py   device tests the maintainer runs
```

## The four panels
- **Power**: profile dropdown (short label, full name in the description), one read-back line after every change
  ("RAPL reads 17 W / 24 W": PL1 / PL2 from the backend that enforces them), Autotune on/off with a green/red status
  and, while it runs, what it is doing (target fps, current TDP, last adjustment). Profile changes are debounced in
  the UI (300 ms, only the newest change toasts) and serialised in the backend (one lock), so a fast flick through
  the list never shows a stale value.
- **Per-game**: for the running game, which tier of the ladder is active and three switches: XeSS on XMX (off = the
  stock driver for this game), XeLL driver mode (the fork's shim; status read from `igdext_trace.log` /
  `igdext_xell.log` in the prefix, rotated per launch by the wrapper), OptiScaler injection (status: hook active +
  version). A dropdown steps down a tier.
- **Fixes**: VRR fix install / remove, face-button fix install / remove, the health check in one line (the full list
  on the page).
- **Setup**: install / update XeSS XMX, download OptiScaler, run the self-test (PASS/FAIL), Dry run, export the log.

## The upscaling and frame-generation ladder (per game, automatic, user can step down)
1. **Native XeSS** in the game → the xess-xmx-linux driver runs it on XMX, XeLL on. Nothing injected.
2. **DLSS or FSR, no XeSS** → OptiScaler swaps the inputs to XeSS XMX and the game's frame generation for XeSS FG.
3. **No upscaler at all** → lsfg-vk (Lossless Scaling's frame generation as a Vulkan layer). It runs on the shader
   cores, not the XMX units, and needs the user's own copy of Lossless Scaling; the UI says both.
Anti-cheat folders block injection. The install copies the release next to the main exe, takes the first free proxy
name, writes WINEDLLOVERRIDES through the launch wrapper, and lists every file in .clawdeck-inject.json for an exact
undo. Updates: upstream asked at most once a day; a newer release is applied after a game closes.

## Events instead of timers
The backend never polls. The frontend registers Steam's own notifications and forwards them:
`GameSessions.RegisterForAppLifetimeNotifications` (game start/stop), `System.RegisterForBatteryStateChanges`
(the running game switches profile slot), `System.RegisterForOnResumeFromSuspend` (re-apply the running game).
Timers exist only while autotune runs for a game.

## Who sets the TDP
On the Claw 8 EX the MSI firmware attributes are accepted but ignored (docs/HARDWARE-FINDINGS.md §12); the SoC
enforces `intel-rapl-mmio:0`, so ClawDeck writes RAPL (PL2 before PL1 when raising, PL1 first when lowering). HHD's
TDP when HHD runs; the MSI attributes only where RAPL is absent. Steam's own slider may do nothing on this chip.
Every write answers with the value read back (§14).

## Autotune controller
Profile = the ceiling. Decisions on a 10 s window (average and worst second) that restarts after every change:
below target → feed the bound side (CPU ceiling or GPU floor) before adding watts; on target → trim the unbound side,
then one watt less; learned games are rechecked every 5 min and relearn when fps sit far above target (graphics
lowered). A manual change pauses it for the session. ~70 µs of CPU per step.

## Open items (need the device)
- The fork publishes no release: `tools/make-release.py` there needs the CI-built binaries (its Mesa 26.3 matrix job
  currently fails on a Mesa tag that no longer exists, see PR #24). Until a release exists the installer uses
  upstream's 20261018 and XeLL stays in cross-vendor mode.
- XeLL log markers (`XELLPROBE LatencyReductionExt`, `RenderSubmitStart`, `xellSetSleepMode`) come from the fork's
  shim source; confirm on the Claw with a game that uses XeSS frame generation.
- Steam's refresh / VRR panel after the EDID fix: the 120 Hz mode still offered, 30–120 shown by `edid-decode`.
