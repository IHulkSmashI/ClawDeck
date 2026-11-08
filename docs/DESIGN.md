# ClawDeck design notes

## Scope (reset 2026-10-21)
ClawDeck does only what Bazzite and Steam's Quick Access do **not** already do. Gone from the UI: VRR on/off,
refresh rate, HDR, per-game performance settings, fan presets, GPU clock windows, CPU ceiling, EPP, charge limit,
sleep, dock readouts, benchmark, Proton picker, streaming (Steam's own panels, InputPlumber and PowerStation cover
them). Priority, in order; nothing lower starts until the line above works on the maintainer's Claw:

1. **XeSS XMX + low latency** — the one thing missing from Bazzite. Installed from upstream's release (20261104+:
   low latency with frame generation, 2–8x, FSR on XeSS); its per-game launch options are one tap each. Our fork's
   XeLL driver mode is retired: upstream's low latency (#41) does the same job.
2. **Controller and VRR fixes** — face buttons (InputPlumber override), the 30–120 Hz EDID fix (install / remove).
3. **ClawDeck's own OptiScaler per game** — `optiscaler.py` downloads the OptiScaler release and OptiPatcher build
   pinned in `assets/versions.json` (`optiscaler`, sha256 checked) once, puts them into a game's folder with the
   game's settings and the Intel presets (XeSS on XMX, FSR 4 on XMX, XeSS frame generation) and takes them out again
   exactly (manifest + backups). The version is ClawDeck's to choose and test. Decky Framegen is compatibility only:
   a game it patched (fgmod in the launch options, or its files) is detected and left alone with a warning.
   Capcom RE Engine games get the Capcom setup instead (`optiscaler_v10` in the `OptiScaler\` subfolder,
   onehoon's REFramework, the game's DLSS-G turned into XeSS-FG; `launchenv.capcom`); every other game 0.9.4.
   Lossless Scaling stays decky-lsfg-vk's.
4. **Power + autotune** — profiles through RAPL-MMIO, as a dropdown plus the Autotune switch.

## Architecture
```
Steam Quick Access (React, @decky/ui)        src/          four panels; every control explains itself in its description
        │  callable()                         src/api.ts    one typed function per backend call (wiring test)
Decky backend (Python, root)                  main.py       thin dispatch, game lifecycle, settings
        │
py_modules/clawdeck/
   hw.py        model table (one row per Claw) + sysfs path discovery
   sysfs.py     every write recorded → revert_all(); last_error() for the UI's red rows
   power.py     levers (TDP via RAPL-MMIO on the EX, HHD / MSI firmware elsewhere; CPU max_perf_pct; GPU window)
                + the four profiles (hw.PROFILES)
   battery.py   plugged detection only (profiles differ on battery / charger)
   profiles.py  per-game JSON: base profile, what autotune found, launch settings
   thermal.py   temperature guard while thermald is paused: >95 °C → TDP −2 W / 10 s until <90 °C (§21)
   autotune.py  1 Hz sampler (MangoHud CSV, RAPL, /proc/stat, PowerStation GPU busy), 10 s window, CPU/GPU balance
   xess.py      xess-xmx-linux: install (asset sha256 checked), state, per-game low latency from the shim's log,
                OptiScaler-present detection (read-only)
   optiscaler.py ClawDeck's OptiScaler: pinned install (sha256), patch / unpatch a game with manifest + backups
                (flat 0.9.4, or v10's OptiScaler/ subfolder for Capcom games), owner() (ClawDeck's, Decky
                Framegen's or someone else's; only ClawDeck's is touched)
   reframework.py onehoon's REFramework fork next to a Capcom game's exe (never over a dinput8.dll not ours), its
                menu key moved to Delete
   framegen.py  Decky Framegen compatibility: is it installed, fgmod state (read-only; ClawDeck doesn't install it)
   launchenv.py per-game <runtime>/launch/<appid>.env: XMX off, XESS_FG, IGDEXT_XELL_CAP=off, XESS=latest, FSR routes,
                OptiScaler presets (read by the wrapper)
   edid.py      the panel list's VRR range fix for Linux (validated panels only; drm.edid_firmware: rpm-ostree + initramfs-etc on Bazzite,
                the bootloader config on CachyOS, /etc/default/grub + update-grub + a keep-list entry on SteamOS)
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
   sysfs-survey.sh      read-only survey for a new model (the health check's "report this model" fix)
   soak.py              CPU % / RSS of the backend against the battery budget (maintainer)
```

## The four panels
- **Power**: profile dropdown (six, short label, what/why/cost under it), the TDP slider (model range, = Custom,
  autotune's ceiling), one read-back line after every change ("RAPL reads 17 W / 24 W": PL1 / PL2 from the backend
  that enforces them), Autotune with the set TDP, the TDP it uses, rendered fps against the most the set TDP gives,
  frames per watt, the bottleneck, its phase and the last action.
  Profile and slider writes are debounced in the UI (300 ms, only the newest toasts) and serialised in the backend
  (one lock). Under an Advanced expander: CPU boost, CPU ceiling, GPU floor/ceiling, GPU efficiency mode (xe
  `power_profile`, §22.3), EPP, autotune floor (§19).
- **Per-game**: for the running game: XeSS on XMX (off = the stock driver for this game), frame generation
  multiplier, low latency (status from `igdext_xell.log` in the prefix, rotated per launch by the wrapper), latest
  XeSS libraries, FSR games on XeSS; then OptiScaler: whose it is in this game, "Install OptiScaler in this game"
  (ClawDeck's own, with the recommended setup) and, when in use, its upscaler and XeSS frame generation. OptiScaler's
  XeSS runs on XMX through the same shim. A game Decky Framegen patched shows a warning and "Stop using Decky
  Framegen here" instead (compatibility; ClawDeck doesn't change that game's OptiScaler).
- **Fixes**: VRR fix install / remove, face-button fix install / remove, the health check in one line (the full list
  on the page).
- **Setup**: install / update XeSS XMX (then the package's driver check, `session-check.sh --dry-run`, so the line is
  right without a re-login; "up to date" when the newest release is installed), ClawDeck's OptiScaler (version,
  how many games use it; notes Decky Framegen when it is installed too), run the self-test (PASS/FAIL), export the log.

## Upscaling and frame generation: what runs where
1. **Native XeSS** in the game → the xess-xmx-linux driver runs it on XMX, XeLL on. ClawDeck's job.
2. **DLSS or FSR, no XeSS** → ClawDeck's own OptiScaler (Per-game) swaps the inputs to XeSS (or FSR 4 on XMX),
   which the same driver then runs on XMX. ClawDeck's job.
3. **No upscaler at all** → Bazzite's decky-lsfg-vk (Lossless Scaling, shader cores). Bazzite's job.

## Events instead of timers
The backend never polls. The frontend registers Steam's own notifications and forwards them:
`GameSessions.RegisterForAppLifetimeNotifications` (game start/stop), `System.RegisterForBatteryStateChanges`
(the running game switches profile slot), `System.RegisterForOnResumeFromSuspend` (re-apply the running game).
Timers exist only while autotune runs for a game, and the temperature guard's 10 s check while thermald is paused
(which includes the menus while ClawDeck holds a limit the user picked).

## Who sets the TDP
On the Claw 8 EX the MSI firmware attributes are accepted but ignored (docs/HARDWARE-FINDINGS.md §12); the SoC
enforces `intel-rapl-mmio:0`, so ClawDeck writes RAPL (PL2 before PL1 when raising, PL1 first when lowering). HHD's
TDP when HHD runs; the MSI attributes only where RAPL is absent. Steam's own slider may do nothing on this chip.
Every write answers with the value read back (§14).

thermald (Bazzite, polling mode) re-applies the platform's DPTF table to that RAPL domain every few seconds (§21), so
`power._set_rapl` stops it (never disables it) the first time ClawDeck sets a TDP and `revert_all` starts it again.

## Your limit and Autotune (main.py `_pick`)
The limit is what the user picked for the power source the Claw is on: the running game's own pick (a pick made while
it ran, saved per game, battery and plugged in apart), else the last pick on that power source, else the default.
`power_slot` returns it as `pick`, and the profile list and the slider show it, in a game and in the menus. With
Autotune the slider stays that limit; the line under it reads "Autotune: using 22 W of your 35 W · saves 13 W · 58
fps" from `get_autotune` (polled every 3 s while the panel is open in a game).
- **Game start, a pick in a game, Use default, unplugging, resume:** `revert_all(keep_thermald=True)` (back to boot
  values, thermald stays stopped because a TDP follows at once), then the pick, then a fresh Autotune session at that
  limit. A pick in a game drops what was learned at the old limit, so nothing pulls the new limit down until
  Autotune has measured it.
- **Game exit:** what Autotune learned is saved, everything is reverted, then the pick for the power source the Claw
  is on now goes back on the chip. With no pick on that power source the chip keeps the firmware's limits and thermald
  starts again. Unplugging with no game switches to the other power source's pick the same way.
- **Unload:** `revert_all()` as always: every value back to boot, thermald started.

## Temperature guard (§21)
While thermald is stopped, `thermal.Guard` takes over its temperature job for ClawDeck's own TDP. main.py runs it
every 10 s, only while `power.thermald_paused()`; it reads the package temperature (`x86_pkg_temp` thermal zone,
coretemp's "Package id 0" as the fallback; `hw.find_pkg_temp`). Above 95 °C it trips and steps the TDP −2 W per
check until the package is under 90 °C, never below the floor (autotune's, else the user's, else the model's). With
autotune running the step is `Autotune.thermal_step`: it becomes a cap autotune works under (a re-measure goes to
the cap, a lever on trial is undone). The step stays until the user picks a profile or moves the slider (both reset
the guard) or the power is reverted. The status line reads "thermal: stepped to N W". The SoC's TCC limit and the EC
fan control are untouched; the guard only keeps ClawDeck's TDP from leaning on them.

## Autotune controller (v3, HARDWARE-FINDINGS §22)
No target fps. Every decision uses a 10 s window of **rendered** fps that restarts after every change, one change per
window. Rendered, not presented: with XeSS frame generation the screen shows 2–4 frames per rendered one and
MangoHud counts them all; XeLL's `xellSleep` runs once per rendered frame and the xess-xmx-linux shim logs it every
100 frames (`xellSleep stats: 100 calls in W ms (P ms/frame)`, `IGDEXT_XELL_LOG=1`, exported by the launch wrapper
while autotune is on, read from `drive_c/igdext_xell.log`). Rendered = 1000 / P; presented / rendered = the
frame-generation multiplier. No XeLL line = no XeSS frame generation, and MangoHud's rate is the rendered one.
1. **measure**: at the set TDP (profile or slider), the median of three windows = the most it gives (`max_fps`);
2. **balance**: one lever toward the bottleneck (GPU-bound: CPU ceiling −10 % to hand the GPU the shared budget,
   then GPU floor +100 MHz; CPU-bound: CPU boost on, CPU ceiling 100 %, drop a GPU floor). Kept only with ≥2 % more
   fps (and then it may take another step); otherwise undone and balancing ends;
3. **lower**: TDP −1 W while fps ≥ (100 − keep) % of `max_fps` (keep = 5 % by default, 2–15 in Advanced); the first
   miss is measured again, the second goes back up 1 W. Every new TDP gets one balance pass (the bottleneck moves as
   watts drop);
4. **hold**: below the line twice → +1 W; a sustained ±15 % shift, a frame-generation multiplier change or 5 min →
   measure again.
GPU efficiency mode (§22.3): xe's `power_profile` (`base` / `power_saving`). Saver and Battery set power_saving, the
other profiles base. In autotune it is the first GPU-bound lever (off before any CPU trade or GPU floor, and before
+1 W when short in hold), and at the TDP floor on the line it is tried on for one window and kept only if the game
stays on the line. The file reads "[base] power_saving" and takes one word, so `sysfs.write(original=)` records the
active word for the revert.
PL1 window (§22.2): RAPL averages PL1 over tau (56 s on the EX), so for most of a minute after a change the package
can still boost and a 10 s window would judge the boost. While learning (any phase but hold, or fewer than 3 quiet
windows in hold) autotune sets tau to 8 s; once settled the firmware's tau comes back so play keeps its bursts.
Paused → firmware's tau; every write revertible.
The floor is the model minimum (8 W) or the user's. Saved per game and power source: TDP, CPU ceiling, GPU floor,
boost, `max_fps` and the ceiling it was measured at (a different set TDP is measured again). Frames per watt is
shown, never chased: it keeps improving down to the minimum TDP. A manual change (Advanced) pauses it; picking a
profile or moving the slider measures again and hands control back. ~70 µs of CPU per step.

## Open items (need the device)
- Low latency log lines ("low latency: on", "auto: waited in N of 100 frames") come from upstream's
  `shim/src/dll/XellHook.cpp`; confirm on the Claw with a game that uses XeSS frame generation.
- ClawDeck's OptiScaler: whether `FrameGen_FGOutput=xefg` generates frames with each frame generation input
  (the game's DLSS FG, FSR FG, the upscaler).
- Steam's refresh / VRR panel after the EDID fix: the 120 Hz mode still offered, 30–120 shown by `edid-decode`.
