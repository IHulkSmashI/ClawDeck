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
   governor.py  the deciding: step(state, obs, cfg), no clock and no I/O (tests, tools/autotune_sim.py)
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
`power_slot` returns it as `pick`, and the profile list and the slider show it, in a game and in the menus. With
Autotune the slider stays that limit; the line under it reads "Autotune: using 22 W of your 35 W · saves 13 W · 58
fps" from `get_autotune` (polled every 3 s while the panel is open in a game).

**A pick and what Autotune learned are two records, and only the user writes a pick.** A game's slot (per power source)
has `base` and `tdp` (the pick) and `learned` (Autotune's record: `start`, `held`, the limit and target it was found
under). `base` and `tdp` are written in three places, each a user action: `set_tdp` (the slider), `apply_profile` and
`reset_power_slot` (Use default). Autotune writes `learned` only (`_save_learned`). `_pick` reads the limit from
`base`/`tdp` and never from `learned`; `learned.start` is only where a session begins under that limit, and it is
dropped when the limit or the target is another one. So a level that held can never become the limit of a later
session, and the slider never shows a value nobody picked. (A build that saved the TDP that held as the game's own pick
left 8 W picks on a Claw that its user had not made, and a frontend's 8 W menu result became the limit of its games,
2026-11-15/16; tests `test_what_autotune_learned_is_never_saved_or_shown_as_your_pick` and
`test_what_a_frontends_menus_hold_is_never_the_limit_of_its_games`.)
- **Game start, a pick in a game, Use default, unplugging, resume:** `revert_all(keep_thermald=True)` (back to boot
  values, thermald stays stopped because a TDP follows at once), then the pick, then a fresh Autotune session under that
  limit: at the level the game held last time under it, else mid-range. After a pick in the game the session starts at
  the pick itself, and what was learned at the old limit is dropped.
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
clock (`thermal_clock`) makes it forget which levels failed (the same watts give other frames), and watts the guard
gives back are not raised to, only looked at for what they give. The step stays until the user picks a profile or moves the slider (both reset
the guard) or the power is reverted. The status line reads "thermal: stepped to N W". The SoC's TCC limit and the EC
fan control are untouched; the guard only keeps ClawDeck's TDP from leaning on them.

## Autotune governor (HARDWARE-FINDINGS §24)
`governor.py` is one function, `step(state, obs, cfg)`: the frames since the last look, the package power, the guard's
cap in; a limit to write (or none), when to look again and a line for the panel out. No clock, no I/O: the tests run it
on frames written by hand, and `tools/autotune_sim.py` on games built from recordings. `autotune.py` is the rest: the
frames (`Sampler`), the session, the loop, the writes, the status and the log.

It asks one question all the time: would more watts, up to your limit, give frames it has to deliver?
- **The goal** is your target less 5 %; with a target the full limit can't reach (and Auto FPS on), what the full limit
  gives less 5 %; with no target, what the full limit gives less the keep margin (5 % by default, at least 2 fps). What
  the full limit gives (the reference) is the pace whenever the limit can't be what holds the game back: at the full
  limit, or with the package 2 W or more under the limit.
- **No: try fewer.** A step down (2 W, or straight to just over what the package draws when that is far under the
  limit) is watched for 2.5 s on every frame. A clear loss (the median of 10 frames 3 % under the goal), a small one
  on all the frames of the watch, or more slow frames than before (frames more than 10 % over the pace: the edge of an
  emulator's limit shows there first) takes it back at once. The level failed: it is not tried again for 2 minutes,
  twice that after each further failure, and the next step goes halfway to it. Two measured points (the level that
  failed and the one above it) also rule out the levels a straight line puts under the goal.
- **Yes: all of them at once.** A clear loss while holding a lowered limit writes the full limit; a second later the
  frames say which it was. They came back: the limit was too low, it joins the failed levels, and the way down starts
  again. They didn't: the scene got heavier at every limit, the reference is what the full limit gives now, and the
  limit before comes back. A small loss that stays goes one level up instead.
- **What is known grows old.** The reference is checked at the full limit one second after steps down that began there,
  when the frame rate at the held limit moved 3 % since it was taken (not more often than once a minute), and every
  10 minutes. Failed levels are forgotten when the pace at one limit moved 15 % for 5 s (another scene), when the guard
  changed the CPU clock, and when the frame-generation multiplier changed.
- **Not judged:** no frames for 1.5 s, or a pace under 35 % of the usual one (a loading screen, a menu, a pause). A
  step being watched is taken back; a lowered limit goes to the full one while frames still come.

- **What is not the limit's doing** (added after an adversarial review, 2026-11-15, which broke the first version on
  each of these):
  - *A cutscene or a video* gives fewer frames at every limit. When the full limit gives 15 % less than it did, the
    level that held before stays the lowest one for as long as that lower reference stands, no step down is tried for
    20 s, and the full limit is looked at again every 30 s for 2 minutes. More frames at a held limit than the full
    limit is believed to give (5 % for 5 s: the game is back) has the full limit looked at; far more (15 %) is a
    menu's first, see below. A target counts as what the full limit gave only once it was held: a first look that
    shows less than a target never met is no drop.
  - *A menu, a map, fast-forward, a loading screen:* frames under 35 % or over 250 % of the usual rate are not judged.
    When they stay for 5 s they are the game (a 20 fps stretch, a target far above what the game does), and nothing
    known before applies.
  - *The game's own dips and hitches:* a loss at a limit that can't be its cause (the full limit, or the package well
    under the limit) that is over within 2 s is a dip of the game's own. After two of them a loss counts only when it
    is still there a second later, and a step is judged at the end of its watch on its typical frame. A level that had
    held for 20 s is tried once more before it is given up.
  - *A game whose frame rate wobbles* (the rates of its last six runs of ten frames differ): the thresholds widen to
    2.5 times that wobble, at most fourfold. Frame rates are frames over their time, without frames over 2.5 times the
    median; frames that alternate short and long (generated frames) are read as their rate.
  - *Slow frames* count against the mean frame time of their own stretch and against the share the game had before
    the step, with a margin for chance; not at all while the pace moves or in a game that dips by itself. More of
    them at a held limit: one watt more, at most every 10 s. A watt that changed nothing: one look at the full limit
    says whose they are. None there: the limit's, it stays up and comes down by steps judged against the share
    there. As many there: the game's own in this place, the limit stays. The share is taken at every such look.
  - *The reference* while a limit is held that can't hold the game back is the median of the last five looks, not the
    last one; a look at the full limit made on purpose (after a loss, or to check) sets it from that look's frames:
    at least a second and 30 frames of them (a 20 fps game takes longer), once the draw and the frame rate have
    stopped climbing.
  - A step down is watched from when the package draws under 2.5 W over the new limit (at most 3 s late); a raise is
    judged when the draw and the frame rate have stopped climbing.

- **After the second review (2026-11-15):**
  - *The floor after a drop of the reference* (the level that held before a video) stands for 2 minutes; then the lower
    reference is the game (a heavier place, a target that was never in reach) and the way down goes on. A reference
    taken while the package did not draw the whole of the limit that is held says nothing once the package does draw
    it all: after 2 s of that the full limit is looked at (the game after a long video, at a limit that gives it just
    the video's frame rate, showed no loss).
  - *The reference follows the pace* only once every frame of the pace came in a look in which the limit could not
    hold the game back: at the start and the end of a loading screen the draw is low while the pace is still that of
    frames the limit did hold back.
  - *Far more frames than the game gave* (15 %: a menu or a map over it) move nothing for 30 s: no step down, no
    reference from them, what failed is kept (a menu's 60 fps over a 30 fps game took the limit to the floor). Under
    the target at a limit that holds the chip there is one look at the full limit after 5 s (the game back from a
    long video); a look that finds far more starts the same wait. Back from any stretch that was not judged, the
    pace starts again from the game's own frames.
  - *Slow frames* count only while the frame rates of five parts of the last 10 s agree (a faster menu inside them
    made every frame of the game a slow one).
  - *A log with a row a second* (the game's own MangoHud log while mangoapp gives nothing) hands the governor the
    frames of the whole time since its row before. *No frames for 30 s* at any time, not only at the start: your limit
    goes back on the chip until frames come. *A refused write:* the governor rests for 30 s. *The guard's cap under
    your floor:* the guard wins.
  - *The look at the full limit a second after steps down from it* also comes when the steps took more than six in a
    row (the pause after the sixth dropped it: 30 W to the floor in eleven steps ended without that look).

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
frames it didn't cost yet). The CPU and GPU levers stay as your pick set them.

Start: where the game held last time under the same limit and for the same target (`Learned.start`: the level under
which 80 % of the holding time lay; `Learned.held` has the seconds per watt), else mid-range between floor and limit.
A limit picked while the game runs (slider, profile, Use default) goes on the chip as picked and Autotune works down
from it. A manual change (Advanced) or switching it off in a game pauses it: your limit goes back on the chip and the
change made by hand stays. xemu (`OWN_PACE`) and a game with no frame rate for 30 s get your limit and are left alone.

## Open items (need the device)
- Low latency log lines ("low latency: on", "auto: waited in N of 100 frames") come from upstream's
  `shim/src/dll/XellHook.cpp`; confirm on the Claw with a game that uses XeSS frame generation.
- ClawDeck's OptiScaler: whether `FrameGen_FGOutput=xefg` generates frames with each frame generation input
  (the game's DLSS FG, FSR FG, the upscaler).
- Steam's refresh / VRR panel after the EDID fix: the 120 Hz mode still offered, 30–120 shown by `edid-decode`.
