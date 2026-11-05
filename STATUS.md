# ClawDeck status

Updated 2026-11-04 (0.2: xess-xmx-linux 20261104 options, Decky Framegen presets, battery-only Autotune). The plugin does only what Bazzite and Steam's Quick Access do not already do, in
four panels: **Power**, **Per-game**, **Fixes**, **Setup**. Everything below passes the backend tests against fake
sysfs trees, and the UI typechecks, builds into the Decky zip and renders in the browser preview.

## Priorities (from the project brief; nothing lower starts until the line above works on the maintainer's Claw)

| # | What | Code | Needs the Claw |
|---|---|---|---|
| 1 | **XeSS XMX + low latency** (upstream 20261104) | done: upstream install (the fork and its XeLL driver mode are retired: upstream shipped low latency in #41); Per-game: XMX on/off, frame generation 2–8x, low latency, latest XeSS libraries, FSR games on XeSS, each with a read-back line | see Needs the maintainer → 0.2 |
| 2 | **Controller and VRR fixes** | done: Fixes panel, install / remove buttons (no toggles) | install the VRR fix, restart, `edid-decode` shows 30–120 and the 120 Hz mode is still there |
| 3 | **OptiScaler through Decky Framegen** | done 2026-11-04: no injector of our own; Setup detects / installs Decky Framegen, Per-game adds its fgmod to the game and passes Intel presets (XeSS on XMX, FSR 4 INT8 experimental, XeSS frame generation) as fgmod env vars | see Needs the maintainer → 0.2 |
| 4 | **Power + autotune** | done: six profiles + 8–35 W TDP slider (= autotune's ceiling), read-back line, debounced; autotune acts on the bottleneck (§18); Advanced expander (§19) | see Needs the maintainer → Power |

## Done (code, tests)

| Module | Backend | Panel | Notes |
|---|---|---|---|
| Hardware table + discovery | `hw.py` | Setup | one row per Claw; EX = MS-1T91 (board_name first) |
| Revertible sysfs | `sysfs.py` | every write | `last_error()` feeds the red rows |
| Health check + one line | `health.py` | Fixes | fixes: install XeSS, face buttons, hardware survey |
| Power profiles | `power.py` | Power | RAPL-MMIO on the EX (the MSI attributes are ignored there), HHD when it runs |
| Autotune | `autotune.py` | Power | v3: no target; measure the most the set TDP gives → balance CPU/GPU (keep only +2 %) → lowest TDP within 5 % → hold; rendered fps from XeLL via the shim, not generated frames; battery only unless "Autotune when plugged in" |
| XeSS XMX | `xess.py`, `launchenv.py`, `tools/clawdeck-launch.sh` | Setup, Per-game | upstream release; `xmx` / `fg` / `low_latency` / `xess_latest` / `fsr` per game; shim log read per launch |
| Decky Framegen | `framegen.py`, `launchenv.py` (`opti`, `opti_xefg`), `steam.ts` (fgmod in launch options) | Setup, Per-game | install from its release zip only when missing, never overwrites; Decky restart scheduled through systemd-run |
| VRR range fix | `edid.py` | Fixes | ClawLab's validated panels only; `drm.edid_firmware` + initramfs-etc |
| Face buttons (EX) | `buttons.py` | Fixes | InputPlumber override, `claw8ex` map |
| Self-test | `tools/selftest.py`, `main.self_test` | Setup | PASS/FAIL per control; refuses while a game runs |
| System change check | `changes.py` | toast at start | kernel / Mesa / controller / XeSS / Decky changes re-run the health check |
| Browser preview | `preview/` (`pnpm preview`) | all four | the real panel code with example data |

Removed in the scope reset (2026-10-21): VRR on/off, refresh rate, HDR, display save, fan presets, GPU clock
window, CPU ceiling, EPP, turbo, charge limit, sleep readouts, dock readouts, benchmark, Proton picker, streaming
(Moonlight), profile import/export, the Controller and Battery pages, and (2026-10-22, Bazzite does them) OptiScaler
and Lossless Scaling per game with their updater. Their modules are gone too (`benchmark`, `controller`, `display`,
`fans`, `proton`, `sleep`, `streaming`, `injector`, `gamescan`, `compat`, `updates`); `battery.py` keeps `plugged()`.

## Tested on real hardware

2026-10-21, Claw 8 EX on Bazzite deck 44.20261012.1: plugin loads in Decky 3.3.0, hardware detected, health check and
power readouts run. `tools/selftest.py` 12/12 PASS on the device (TDP via RAPL measured 15 W under load at a 12 W
setting and 37 W after revert; CPU/GPU ceilings; charge threshold; face buttons; full revert). Blockers found and
fixed the same day: backend not root (§13), MSI firmware TDP attributes ignored (§12), no CA store (§15).
The four-panel UI has not been on the device yet.

## Needs the maintainer

- **Power: your limit and Autotune stay in step (power-sync, 2026-11-04):** in the menus pick Custom 20 W (battery)
  and Max (plugged in). Start CONTROL on battery: the slider shows 20 W and the line under it "Autotune: using N W of
  your 20 W · saves M W · fps", following Autotune every 3 s, never back to an old value. Pick Balanced in the game:
  the chip reads 17 W at once and Autotune measures again from 17 W; the next start of CONTROL comes back at Balanced.
  Quit the game: the chip reads 17 W again (RAPL PL1), not the firmware's 25 W; with nothing picked on a power source
  it reads the firmware's value and thermald is running again (`systemctl is-active thermald`). Plug in during and
  after the game: Max 35 W both times; unplug with no game: back to the battery pick. Unloading ClawDeck in Decky puts
  PL1 back to the firmware's value and starts thermald.
- **ClawDeck's own OptiScaler (writes into game folders; replaces Decky Framegen for new setups):** Games → a DLSS
  game (Wukong, after moving it off the NTFS card) → Use the recommended setup: OptiScaler 0.9.4 downloads (55 MB,
  sha256 checked), lands next to the exe as dxgi.dll (or the next free proxy name) with OptiScaler.ini, OptiPatcher in
  plugins/; the game's own files go to .clawdeck-optiscaler/backup. In the game: DLSS → XeSS on XMX (Insert shows
  OptiScaler's menu); frame generation "from the game's DLSS FG" gives XeSS-FG at the Per-game multiplier
  (XeFG InterpolationCount) with Reflex forced on through fakenvapi (XeLL). Switch the upscaler to FSR 4.1.1 XMX and
  Lite from Quick Access: OptiScaler.ini and the files change without opening OptiScaler. Undo leaves the folder as it
  was. A game Decky Framegen patched shows the warning and isn't touched.
- **Recommended setup per game (writes into game folders):** Onimusha → "Use the recommended setup" → REFramework
  (`dinput8.dll` + `.clawdeck-reframework.json`) next to `OnimushaWotS.exe`, OptiScaler through fgmod with
  `Hotfix_RestoreComputeSignature=true` and `Spoofing_Dxgi=false`; the game starts (no Capcom DRM stop), DLSS in its
  menu becomes XeSS on XMX, XeSS frame generation works. Check `WINEDLLOVERRIDES` in the game's environment reads
  `dinput8=n,b;,dxgi=n,b` and Wine loads both. Then pin the REFramework nightly in `assets/versions.json`. Undo
  removes exactly those files. CONTROL → the recommendation is "FSR on XeSS" (no OptiScaler).
- **Fan presets (Power → Fan, msi-wmi-platform hwmon):** Quiet / Cooling / Full speed / Custom each change the fans'
  rpm audibly (Quiet: off at idle), the "Fans now" line follows; Automatic puts MSI's own control back
  (pwm1/pwm2_enable read 2 again). The choice survives closing a game, a restart and sleep.
- **Charge limit (Power → Battery):** at 80 % the battery stops charging at 80 % on the charger
  (`charge_control_end_threshold` reads 80); Off writes back the value it had (0 = no limit). Survives a restart.
- **Autotune, efficiency (on by default, on the charger too):** CONTROL on battery and plugged in, from 35 W: note
  where it settles (Power shows "saves ~Z W"), the fps lost against the start (should be at most ~2–3 fps), and how
  long it takes to settle (3 W steps on a plateau, then 1 W). Check it holds a frame limit with the least power.
- **REFramework pinned (nightly 01433, sha256 b3ff4f49…):** installed with OptiScaler in RE Engine games without a
  question now. Onimusha: the game starts, REFramework loads, DLSS → XeSS on XMX.
- **FSR 4.1.2 picks index 6:** OptiScaler lists AMD's 4.0.2 first with the B Boost files; ClawDeck now writes
  UpscalerIndex 6 (4.1.2). In CONTROL 4.1.2 drew a black picture in gameplay (upscaler time 0.00 ms): see the
  OptiScaler log before offering it by default.
- **FSR 4.1.1 on XMX (nitec's B Boost), regular and Lite:** with the files in `~/fsr4-xmx`, Per-game → OptiScaler
  upscaler "FSR 4.1.1 on XMX" in a DLSS game through fgmod: the game folder gets the B Boost
  `amd_fidelityfx_upscaler_dx12.dll` after fgmod's copy, `fsr-original.dll`, `cabinet.dll` (LZMS) and
  `cabinet_wine.dll`, OptiScaler shows FSR 4 as the upscaler, the image is FSR 4. Lite vs regular: fps and image.
  Switching back to XeSS removes ClawDeck's files at the next launch (`.clawdeck-fsr4xmx`).
- **VRR in the menus (2026-11-04 report: menus still choppy, VRR couldn't be switched off):** the scope now decides
  "in a game" from Decky's Router.MainRunningApp, switches VRR off only in the menus and leaves a game's VRR to
  Steam's switch. Check ClawDeck's log (`ui: vrr: …` lines) while going menus → game → menus.
- **Watts in the overlay:** after switching it on, restart once: `/etc/tmpfiles.d/clawdeck-overlay-power.conf`
  opens the counters at boot, before mangoapp starts; the overlay (level 3+) shows CPU W.
- **First-run fixes:** on a fresh install the face-button fix and the split-lock log fix apply by themselves once;
  removing one in Fixes keeps it removed after a restart.
- **Before the next release, on the Claw (2026-11-04 build):** per-game XMX (Setup moves the session file aside; Game
  Mode comes up; CONTROL logs `clawdeck: XeSS XMX driver for this game`), "VRR only in games" (menus 120 Hz, games
  30–120, stands aside with the monitor on USB-C), "Silence the split-lock flood" (Fixes; `journalctl -u
  steamos-manager --since -60s | grep -c sls` drops to 0; steamos-manager's CPU drops), the NTFS warning in Per-game
  (Wukong on the SD card), watts in the overlay.

- Efficiency session (findings §22.1 + §22.4), with Black Myth: Wukong's benchmark: TDP sweep 8→35 W with
  `tools/efficiency-sweep.sh`, then the same at 10/12/17 W with GPU `power_saving`; watch intel_lpmd and per-core load
  during the run. Same session as the XeLL Windows-vs-Bazzite latency comparison.
  How to run it: from a ClawDeck checkout, `sudo tools/efficiency-sweep.sh --label wukong` (1080p preset fixed, XeSS
  on XMX, frame generation off, frame limit off, Autotune off, game started through the launch wrapper); start the
  benchmark at each prompt. It repeats 10/12/17 W with `power_saving` by itself. Commit
  `docs/measurements/ptl-efficiency-wukong.csv`; the knee it prints and the CSV set the six profile wattages.

- **Power (§17–§19), on the Claw:** move the TDP slider: the profile turns Custom and "RAPL reads N W" follows; pick
  Max 35 W and check whether PL1 above the chip's `max_power_uw` (25 W) takes effect (busy loop or
  `tools/selftest.py`). With Autotune on in a GPU-heavy game, the status line should say GPU-bound and the GPU
  floor should climb before the watts; in a CPU-heavy one (Dragon's Dogma 2 town), CPU boost / ceiling first. Open
  Advanced, change CPU boost, the CPU ceiling, both GPU clocks and the energy mode: each toast reads back, and
  Autotune shows "paused".
- **Autotune v3 (§22):** in a game with XeSS frame generation (Cyberpunk 2077, Wuthering Waves), the status line must
  say "rendered, 2x shown" (or 3x) and the fps must be the rendered rate; check `drive_c/igdext_xell.log` in the
  game's prefix has `xellSleep stats` lines and stays small over 10 minutes (the shim also routes XeLL's own debug
  messages there). In a game without frame generation: max at the set TDP, then lowering 1 W at a time, holding
  within 5 %. Does OptiScaler's XeFG output call `xellSleep` too (then its frames count right)? Note what you see.
- **Temperature guard (§21), on the Claw:** check the sensor: `cat /sys/class/thermal/thermal_zone*/type` should list
  `x86_pkg_temp` (the health check names the one ClawDeck uses). Then set Max 35 W, start a heavy game, cover the fan
  vents or block the airflow briefly, and watch the Power panel: past 95 °C the read-back line should say "thermal:
  stepped to 33 W", then 31, 29… every 10 s until under 90 °C, never below the floor; the TDP read back must
  follow. Moving the slider afterwards should clear it. Does 95 °C come before the chip's own throttle (TCC)? If TCC
  always wins first, note the temperature it settles at.
- **GPU efficiency mode (§22.3), on the Claw:** Setup → Run self-test: "GPU efficiency mode base / power_saving"
  and its revert must PASS. Pick Battery: `cat /sys/class/drm/card0/device/tile0/gt0/freq0/power_profile` reads
  `base [power_saving]`; pick Balanced: `[base] power_saving`. The Advanced toggle follows and reads back. In a light
  game at the TDP floor with Autotune on, the status should say "trying GPU efficiency mode", then "kept" or
  "undone"; watch for a stutter at scene changes with it on.
- **PL1 window (§22.2), on the Claw:** with Autotune on, start a game and read
  `cat /sys/class/powercap/intel-rapl-mmio:0/constraint_0_time_window_us` while it measures: 8000000 (or the
  firmware's nearest step). Once the status says "holding" for ~30 s it must read 55970000 again, and after closing
  the game too. If the firmware refuses it, the log says so once and autotune carries on with 56 s.
- **XeSS install / update (§20), on the Claw:** tap Install XeSS XMX (or Update): when it finishes the Setup line
  must say "driver loads" straight away, without a log-out (the toast still asks for one so games pick it up). Tap
  Update again: the toast says "XeSS XMX 20261018: up to date", no red row, nothing downloaded (the log says so).
  Check the driver check ran as you: `ls -l ~/.cache/xess-xmx/driver-status` is owned by your user and fresh.
- **XeSS XMX default:** after Install XeSS XMX and one log-out, Per-game should say "XeSS on XMX (default)" for any
  game. Turn it off for one game: ClawDeck adds itself to that game's launch options and the line says "stock
  driver"; the game's XeSS then runs as DP4a.

- **Found on the Claw 2026-11-04 (Onimusha, Proton 11.0, kernel 7.2.9):**
  - **Log flood (14:10, after a hard reboot from a sleep that didn't wake):** steamos-manager's root daemon forwards
    every `split_lock_warn` ftrace hit from Steam's `CHTTPClientThread`s (~30,000 lines in the first 3 minutes,
    `ServiceUnknown` for each: the SLS log receiver isn't there on Bazzite), at ~14 % of a core. The 50 MB journal
    rotates within minutes, so the boot that didn't wake left no log. `split_lock_mitigate` is already 0 (no
    throttling). **To do:** see how the daemon sets up the trace (`strings /usr/lib/steamos-manager`), then a
    revertible Fixes switch that stops the forwarding; then catch the next failed wake in the journal.
  - **Speakers:** not a driver problem. With the MO27Q2 on USB-C, audio went to the monitor's DisplayPort output
    (shown in Steam as "Core Ultra … HD Audio"); unplugged, the RT721 Speaker sink is default and works. Nothing to
    fix in ClawDeck; Steam's audio output picker chooses.
  - xess-xmx-linux's session-wide driver file (`~/.config/environment.d/50-xess-xmx.conf`) reached Steam's own screen
    and every game: Onimusha (no XeSS) lost much of its frame rate, games recompiled shaders at every switch, and the
    one boot that went black ran with it. ClawDeck now moves that file aside after installing and loads the XMX driver
    per game (`xmxscope.py`): only games that ship XeSS or OptiScaler, or Per-game forced on. **Test:** install
    (Setup), reboot, Game Mode comes up; a XeSS game logs `clawdeck: XeSS XMX driver for this game` (PROTON_LOG=1);
    Onimusha doesn't.
  - Capcom's DRM kills Onimusha when its FidelityFX DLL is swapped (`FSR=xess`, RaiseFailFastException). Capcom games
    need OptiScaler + REFramework instead.
  - Onimusha flashes on every driver tried (stock Mesa 26.2.4, the package's 26.1.2 and 26.3.0-rc3): not ours.
  - Overlay watts: `energy_uj` is 0400 and BAT1 `current_now` is 100x too high on the EX. Fixes → "Show watts in the
    overlay" (new); check the overlay shows CPU W and ClawDeck's battery line reads ~10 W idle.
  - Speakers silent although PCM runs (RT721 SDCA, sof-sdca-1amp): the vendor power gates stay off
    (stevedamnvan/msi-claw-8-ex-linux, EX-AUD-001/002). Not built yet: its exact sequence still has to be read.
  - VRR 30–120 makes Steam's menus choppy (the screen really runs at the game's 30 fps). New: Fixes → "VRR only in
    games" (src/vrr.ts): watches Steam's Perf state and sets is_vrr_enabled on while current_game_id ≠ 0, off
    otherwise, with skip_storage_update. **Test:** VRR fix installed; library scrolls sharp at 120 Hz; in a game the
    overlay's refresh follows the fps (30–120); Steam's own VRR toggle shows the change; the switch off = Steam's
    toggle in charge again. If Steam ignores the write, the Fixes line says "VRR switching failed".
- **0.2 on the Claw (none of this has run on hardware yet):**
  - Setup → Update XeSS XMX to 20261104, log out and in: "XeSS XMX 20261104: driver loads".
  - Per-game in a game with XeSS frame generation (Cyberpunk 2077): Frame generation 3x, restart the game, 3x on
    screen; Low latency line says "working" after a minute of play (`drive_c/igdext_xell.log` has "auto: waited in"
    lines); turn it off: the line says "off" next launch. Feel the difference.
  - Latest XeSS libraries in a game with an old XeSS; FSR games → "FSR on XeSS" in an FSR 3.1 game without XeSS.
  - Setup → Install Decky Framegen on a Claw without it: Decky restarts by itself and Decky Framegen shows up; tap its
    Setup OptiScaler Mod, then Setup shows "OptiScaler … ready". With it already installed: the button isn't offered.
  - In a DLSS game: Use OptiScaler in this game → launch options read `…clawdeck-launch.sh ~/fgmod/fgmod %command%`;
    OptiScaler upscaler = XeSS on XMX → OptiScaler's overlay (Insert) shows XeSS; FSR 4 INT8 → shows FSR 4, note the
    cost. OptiScaler frame generation: XeSS → frames generated? If not, which FG input does Decky Framegen set?
  - Autotune: on the charger it no longer starts unless "Autotune when plugged in" (Advanced) is on.
- **XeSS install "file not found"** (brief): the backend now reports the exact step and reason (download: URL and
  HTTP code; archive: what it unpacked where; install.sh: exit code and its last lines) in the toast and the red
  row, and logs it. Tap Install XeSS XMX once more and post the red line if it still fails.
- **Quick Access**: the four panels with the D-pad at 1280×800: nothing truncated in the profile picker, the read-back
  line changes after every pick, a fast flick through the profiles toasts the last one only.
- **VRR fix**: Fixes → Install, restart, `edid-decode` 30–120, 120 Hz mode still offered, games at 35–45 fps no
  longer judder. Remove puts the factory range back.
- **Self-test from the UI**: Setup → Run self-test with no game running; expect 12 PASS.
- Pin tested versions in `assets/versions.json` (all are `pending-verification`, so installs ask "latest, untested?").
- After the first upstream-watch run: test the listed versions, then `python3 tools/upstream_watch.py --update-lock`.
