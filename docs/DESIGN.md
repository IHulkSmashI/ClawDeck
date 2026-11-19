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
   autotune.py  the frames (mangoapp's per-frame log, XeLL), RAPL, the session of a game, the loop, the writes
   governor.py  the deciding: step(state, obs, cfg), no clock and no I/O
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
- **Power**: profile dropdown (six, short label, what/why/cost under it), the TDP slider (model range, = Custom;
  greyed out where Autotune sets the limit), one read-back line after every change ("RAPL reads 17 W / 24 W": PL1 / PL2 from the backend
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
Timers exist only while autotune runs for a game, the temperature guard's 10 s check while thermald is paused
(which includes the menus while ClawDeck holds a limit the user picked), and the diagnostics log's 1 Hz row while
the user has it on (`diaglog.py`: its reads run off the event loop, the controller's reports come through
`add_reader`). An emulator frontend's games (ES-DE) come
from its custom event scripts: ClawDeck's script writes the game to a file, and while a game runs an inotify watch on
that file's folder wakes the backend (`frontend.py`).

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
`power_slot` returns it as `pick`, and the profile list and the slider show it, in a game and in the menus. The
line under it reads "Autotune: using 22 W of your 35 W · saves 13 W · 58 fps" from `get_autotune` (polled every 3 s
while the panel is open in a game).

**Two modes.** Autotune off: the TDP setting is the limit. Autotune on: a target frame rate under **Autotune's own
maximum** (`autotune_max`, one value for both power sources, clamped to each one's range where it is used; none set:
the TDP setting, so nothing changes for who never moves it). Where Autotune governs (`_autotune_governs`: switched on,
not off for the game, and plugged in only with "Autotune when plugged in") the maximum goes on the chip as the limit
in place of the TDP setting (`_limit_overrides`): in a game from its start, so PL2 and the temperature guard follow it
as they follow the TDP setting, also where Autotune then stays out (xemu, no frame rate); and in the menus. No
Autotune session runs in the menus, and none will (decided 2026-11-16): with no game there is no frame
rate to hold, and the menus draw about 4 W, far under any limit. Handheld Companion and rog-ally-cachyos-auto-tdp put
a manual TDP back when no game has frames; here the TDP setting does nothing while Autotune is on, so there is no
manual TDP to put back. `set_autotune_max` in a running game starts the game over under it, like a new pick.
`get_power` returns `autotune_max` (the watts that apply here) and `autotune_governs`; the panel shows the maximum's
slider only while Autotune is on, and greys the TDP slider out while Autotune governs, with the note "Autotune is on,
so set an FPS target and a wattage limit in the Autotune section." Where Autotune does not govern (on the charger
without the option, a game with Autotune off, a game it was switched on in: it starts at the next launch) the TDP
setting is on the chip and its slider moves. Switched off, in a game or in the menus, the TDP setting goes back on the
chip. The maximum is never saved as a TDP setting, and Autotune never changes it. Precedent: an own maximum of the
automatic TDP in three of four projects read; its default from the manual TDP is Handheld Companion's rule; none greys
the manual TDP out (the user's design).

**A TDP setting and what Autotune learned are two records, and only the user writes a TDP setting.** A game's slot (per
power source) has `base` and `tdp` (the setting) and `learned` (Autotune's record: `start`, the limit and target it was
found under). `base` and `tdp` are written in three places, each a user action: `set_tdp` (the slider), `apply_profile`
and `reset_power_slot` (Use default). Autotune writes `learned` only (`_save_learned`). `_pick` reads the limit from
`base`/`tdp` and never from `learned`; `learned.start` is only where a session begins under that limit, and it is
dropped when the limit or the target is another one. So a level that held can never become the limit of a later
session, and the slider never shows a value nobody set. (A build that saved the TDP that held as the game's own setting
left 8 W settings on a Claw that its user had not made, and a frontend's 8 W menu result became the limit of its games,
2026-11-15/16; tests `test_what_autotune_learned_is_never_saved_or_shown_as_your_pick` and
`test_what_a_frontends_menus_hold_is_never_the_limit_of_its_games`.)
- **Game start, a pick in a game, Use default, unplugging, resume:** `revert_all(keep_thermald=True)` (back to boot
  values, thermald stays stopped because a TDP follows at once), then the pick, then a fresh Autotune session under that
  limit: at the level the game held last time under it, else (unplugging, plugging in, also during a sleep) where it
  held just before (in a step down the level before it; not under the temperature guard's cap), else mid-range. After
  a pick in the game the session starts at the pick itself, and what was learned at the old limit is dropped.
- **Game exit:** what Autotune learned is saved, everything is reverted, then the pick for the power source the Claw
  is on now goes back on the chip. With no pick on that power source the chip keeps the firmware's limits and thermald
  starts again. Unplugging with no game switches to the other power source's pick the same way.
- **Unload:** `revert_all()` as always: every value back to boot, thermald started.
- **Emulator frontends (ES-DE):** Steam sees only the frontend, so each game it starts is kept under its own key,
  `<frontend appid>#<system>/<ROM file name>` (`running_key`; `running_appid` stays Steam's, which the panel compares).
  At game-start what the last one learned is saved and the new one starts like a game launch; a game without its own
  pick starts under the frontend's pick, never with another game's learning. At game-end the frontend's own pick and
  session come back.

## Temperature guard (§21)
While thermald is stopped, `thermal.Guard` takes over its temperature job for ClawDeck's own TDP. main.py runs it
every 10 s, only while `power.thermald_paused()`; it reads the package temperature (`x86_pkg_temp` thermal zone,
coretemp's "Package id 0" as the fallback; `hw.find_pkg_temp`). Above 95 °C it trips and steps the TDP −2 W per
check until the package is under 90 °C, never below the floor (autotune's, else the user's, else the model's). With
autotune running the step is `Autotune.thermal_step`: it becomes a cap the governor works under. A cap on the CPU
clock (`thermal_clock`) makes it forget which levels failed (the same watts give other frames); when the cap comes
off, all that was measured under it goes. Watts the guard
gives back are not raised to: the governor climbs when the frames ask for them. The step stays until the user picks a
profile or moves the slider (both reset the guard) or the power is reverted. The status line reads "thermal: stepped to N W".
The SoC's TCC limit and the EC fan control are untouched; the guard only keeps ClawDeck's TDP from leaning on them.

## Autotune governor
`governor.py` is one function, `step(state, obs, cfg)`: the frames since the last look, the package power, the guard's
cap in; a limit to write (or none), when to look again and a line for the panel out. No clock, no I/O: the tests run it
on frames written by hand. `autotune.py` is the rest: the
frames (`Sampler`), the session, the loop, the writes, the status and the log.

It asks one question all the time: would more watts, up to your limit, give frames it has to deliver? Only the full
limit answers that: an emulator that spins draws the whole limit at every limit, and a heavier scene and a limit that
is too low look the same in the frames.

What it knows:
- **The reference** is what the full limit gives: the pace there, or the pace while the package is 2 W or more under
  the limit (the median of the last five looks, so one dip does not move it). A pace within 5 % of your target at a
  lowered limit says only that the full limit gives that pace at least (your target, while nothing is known).
- **The band** is the pace at the held limit when the limit was last known to cost no more than the margin: by a look
  at the full limit, by the step that came here and held, or all the time while the limit can't hold the game back.
  Lowest and highest, at most 15 % apart. A band belongs to one reference: a pace further away, or a look at which
  the full limit gives another frame rate than at the look before, starts a new band. The one before is kept once,
  for 30 s, when it is the faster one or had stood for 30 s (the game comes back to it after a menu); the band of a
  short visit to a slower place is not (another place, where the last watts count for more, has that pace too). Up
  to the pace at which the full limit last gave no more at this limit, more frames are no news, for 30 s after
  the pace was last there (then it may be another place's). A band
  taken while the package drew 2 W or more under the limit (also by a step watched at such a draw, and by a step down
  from such a limit while the package draws under the new one, and by a look in another place while the package drew
  less than all of that limit at the full one) is dropped when the package draws all of that limit,
  also when it was set aside and came back, or the governor went back to it (a video's band, and a place with the
  video's frame rate); a clear fall there gets the full limit at once (in a game that dips by itself, once it has
  lasted a second, as any other fall). In a game that does not dip by itself, the limit is not booked as failed, and is
  tried once more only where the full limit gives the pace it gave before (a dip of the game's own); it is no longer
  the level to go back to.
  **The ratio** is what this limit gave over what the full limit gave when both were last measured. After that
  the reference is the pace over the ratio: it moves with the game's own frame rate.
- **The goal** is your target less 5 %. When the band's lower edge is under that (the full limit gave less than your
  target at the band's look), it is what the full limit gives less 0.5 %, or less the game's own wobble (of the rate
  of ten frames) where that is more: a watch can't tell less from the limit's (at 0.3 %, steps that cost no frames
  failed at a 60 fps frame limit; a game whose frame times spread 3 % by itself kept 22 to 30 W where 20 W gave the
  same frames): no watts are saved with frames under a target out of reach, and a lower limit holds only where it
  gives the full limit's frames (the game's own frame limit, a scene the limit does not hold back). For a step down
  it is what the full limit gives now, and while a limit is held what it gave when the band was taken (the band's
  lower edge over the ratio). A pace that falls from the band and stays over that goal is no loss. There is always a
  target: none set is the screen's highest refresh rate, so the most frames the limit gives up to it, less the same
  margin.
- **The game's wobble** widens what counts as a loss: 2.5 times what its frame rate does by itself, at most fourfold.
  Frame rates are frames over their time, without frames over 2.5 times the median.

The rules:
1. **A look** is the full limit for a second (30 frames at least; longer while the draw or the frame rate still
   climbs), from a held limit under it, and what it gives against what the limit gave just before. It starts when
   - the last ten frames are 3 % under the goal, or the pace is 1 % under it and stays (a fall: at once);
   - the pace is 3 % over the band at a limit the package draws all of, and under your target (the game back from a
     video, a lighter place);
   - your target was in reach, and the pace over the ratio is 1 % under it (at least) at a limit the package draws all
     of: a heavier place where the full limit may give 95 to 99 % of your target (a limit held in a place at your
     target then gave 1.5 to 2.5 % fewer frames than the full limit for 590 s);
   - the limit has 3 % more slow frames than the game's own (frames 10 % over the median of their neighbours; not
     three or more in a row, and not when they all lie in one burst, within a third of the frames: those are the
     game's) at the third level in 2
     minutes: the first two get one watt more and no look (the edge of an emulator's limit is a watt wide). The look
     then lasts 240 frames, at most 10 s;
   - nothing is known at this limit (the start, after a loading screen, watts back from the guard), the pace is
     under your target and the package draws all of the limit: a fall. Where the package draws 2 W less, the limit
     holds nothing back, and the band comes from those frames.
   - the goal is what the full limit gives (your target is out of reach), the limit is 2 W or more under the full
     one, and the full limit was last measured 10 minutes ago, or before a menu (a stretch with the package 2 W
     under the limit, after which the band from before it is back): a scene that got lighter under a step, or
     another place with the same frame rate at this limit, shows in no frame at the held limit (0.2 changes a
     minute, and one look for each visit to a menu or a map); or the full limit was never measured and the pace is
     1 % under your target (a game with no history held mid-range at your target less 5 %, 1.4 to 3 % of the frames
     under what the full limit gave).
   Under the guard's clock cap only a clear fall (the last ten frames 3 % under the goal) starts one at a held
   limit, and nothing known is dropped (a pause, watts back); the limit a failed step went back to still gets one
   for a small fall on all frames of its watch. None starts because a step held or failed: on a Claw those looks
   were 9 of 30 changes in 3 minutes of Enshrouded and each found the frames it had, and the auto-TDP governors of
   other projects have none.
   One verdict. The limit gave the goal of what the full limit gives and has no more slow frames: back, and the
   ratio is this look's. The band takes that pace in when the full limit gives what it gave at the look before; when
   it gives another frame rate this is another place, and its band is this look's alone (what a look found in a
   slower place says nothing of a loss in the place after it). Else the limit failed and the full limit stays; steps
   down follow. One exception: a level that had held for 20 s (or a game that dips by itself) is tried once more,
   watched, before it is given up; so is a level at every loss that the full limit had too (its frame rate climbed
   there from frames as slow as the level's) while the package then draws less than all of the level (a game that
   dips at every limit, every 20 or 30 s); a loss that the full limit answers at once, or in part, is the limit's. And no way back while the place still gets
   heavier: when the frame rate falls during the look, or the limit loses again within 5 s of a look that sent it
   back and the full limit gives less than at that look, the full limit stays (walking into a heavier place, the
   limit went back and forth 8 times in 7 s).
2. **A step down** starts 5 s after the last write, from a pace that is in the band, that the last ten frames agree
   with and that stood still for 3 s, and it is judged against the pace of those 3 s. It goes to the level that held
   for 20 s in this scene; else halfway to the highest level under it that failed; else 2 W down, or straight to
   just over what the package draws. It is watched for 2.5 s on every frame (10 s when the level failed for slow
   frames before), from when the package draws no more than 2.5 W over it. A step has to leave 3 % over the goal,
   unless it gives what the full limit gives: with less, the game's own wobble crosses the goal. A clear loss on the
   last ten frames, a small one on all frames of the watch, or more slow frames than before (not in one burst) takes
   it back at once; the level failed and is not tried again for 2 minutes, twice that after each further failure (up
   to 10 minutes), and steps wait 5 s, twice that after each failure in a row (up to 5 minutes; a level's second try
   after a look starts the count over). A step that loses 15 % gets the full limit at once: when that gives what the
   limit before gave, the level failed; when it gives fewer frames, or so many more that the limit before is under
   their goal, the scene changed under the step and nothing is booked. A step that held with 3 % more frames than
   before it gets a look at once: a lower limit gives no more frames, so the scene changed under the step and its
   frames say nothing of its cost. After three steps in a row the rules of holding run.
3. **A scene has to stay.** The pace 15 % away for 5 s is another scene (frames over your target are not), and so is
   a package draw that falls 2 W under a limit it filled before (a menu at nearly the game's frame rate); no step
   down is tried in it for 30 s (a menu, a map, a video), except back to the level that held in the scene the game
   returns to. What failed counts in the
   scene it failed in and where the full limit gives a little more; in a slower one (a video, a cutscene) for 10
   minutes; not where the full limit gives 15 % more (a lighter place: it may hold there).
4. **The game's own dips:** a loss at a limit that can't be its cause that is over within 2 s, and a second try that
   holds. Not booked in the 10 s after a write (the wobble is measured over those). After two of them in 5 minutes a
   loss counts only when it is still there a second later, and a step is judged at the end of its watch on its
   typical frame.
5. **Not judged:** no frames for 1.5 s, or a pace under 35 % of the usual one (a loading screen, a pause). A write
   being watched is taken back (a step down that leaves so few frames is judged as a step: it failed); a lowered
   limit goes to the full one while frames still come. When such frames stay for 5 s they are the game, and nothing
   known before applies; where the package then draws 2 W under the limit (a video), no step down is tried for
   30 s, as in a new scene. After 5 s of them the scene may be another one with the same frame rate at this limit:
   nothing is known at the limit any more, and the pace starts again from the frames after them.
6. **The guard:** its cap is obeyed at once and a write being watched is dropped unjudged; under your floor the cap
   wins. With watts given back, nothing that was measured under the cap is known any more.

Also: a log with a row a second (the game's own MangoHud log while mangoapp gives nothing) hands the governor the
frames of the whole time since its row before. No frames for 30 s: your limit is applied again until frames
come. A refused write: the governor rests for 30 s.

Frames: every frame of every game from mangoapp's log (Steam's overlay; `Sampler` turns its logging on through its
control socket and deletes the log at the game's end). The launch wrapper's MangoHud log (a row a second) is read only
while mangoapp gives nothing, and that second's frames are taken at their average. Rendered, not presented: with XeSS
frame generation the screen shows 2–4 frames per rendered one; XeLL's `xellSleep` runs once per rendered frame and the
xess-xmx-linux shim logs it every 100 frames (`IGDEXT_XELL_LOG=1`, exported by the launch wrapper while autotune is
on, read from `drive_c/igdext_xell.log`). Presented / rendered is the multiplier the governor gets; without XeLL,
ClawDeck's frame generation multiplier for the game.

The loop looks twice a second, and ten times a second while a write of its own is watched (1–2.5 s). A row a second
goes to `autotune.csv`: every frame since the row before (number, median, 95th percentile, slow share), the limit, the
governor's step, the goal, the frame generation multiplier, and in `note` what was decided since the row before (each
change of the limit with its watts and the reason, a pause, a refused write), so a run on a device shows why the limit
moved.

What Autotune owns while it governs: PL1; PL2 at PL1 + 2 W, also over a PL2 set by hand (`Power.governed`; yours is back
with your limit); the PL1 window at 8 s from the game's start to its end (RAPL averages PL1 over 56 s on the EX: for
most of a minute after a change the package can run up to PL2, and a step down watched for 2.5 s would be judged on
frames it didn't cost yet). The CPU and GPU levers stay as your profile set them.

Start: where the game held last time under the same limit and for the same target (`Learned.start`: the level under
which 80 % of the holding time lay; the session counts the seconds per watt, `State.held_s`), else, after a change of
power source in the game, the level Autotune held just before (under the new limit; not saved as this power source's
level), else mid-range between floor and limit. A TDP set while the game runs (slider, profile, Use default) is applied as set and
Autotune works down from it. A manual change (Advanced) or switching it off in a game pauses it: your limit is applied
again and the change made by hand stays. xemu (`OWN_PACE`, looked for every 3 s for the whole game) and a game with
no frame rate for 30 s get your limit and are left alone.

## Open items (need the device)
- Low latency log lines ("low latency: on", "auto: waited in N of 100 frames") come from upstream's
  `shim/src/dll/XellHook.cpp`; confirm on the Claw with a game that uses XeSS frame generation.
- ClawDeck's OptiScaler: whether `FrameGen_FGOutput=xefg` generates frames with each frame generation input
  (the game's DLSS FG, FSR FG, the upscaler).
- Steam's refresh / VRR panel after the EDID fix: the 120 Hz mode still offered, 30–120 shown by `edid-decode`.
