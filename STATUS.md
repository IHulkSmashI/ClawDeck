# ClawDeck status

Updated 2026-10-22 (scope reset). The plugin does only what Bazzite and Steam's Quick Access do not already do, in
four panels: **Power**, **Per-game**, **Fixes**, **Setup**. Everything below passes the backend tests against fake
sysfs trees, and the UI typechecks, builds into the Decky zip and renders in the browser preview.

## Priorities (from the project brief; nothing lower starts until the line above works on the maintainer's Claw)

| # | What | Code | Needs the Claw |
|---|---|---|---|
| 1 | **XeSS XMX + XeLL** from the fork (PRs #23/#24) | done: fork-first install with upstream fallback and a note; Setup shows version, driver mode and source; Per-game shows XeSS on/off, XeLL driver / cross-vendor / off per launch | the fork has no release yet (below); a game with XeSS frame generation to confirm the XeLL log markers |
| 2 | **Controller and VRR fixes** | done: Fixes panel, install / remove buttons (no toggles) | install the VRR fix, restart, `edid-decode` shows 30–120 and the 120 Hz mode is still there |
| 3 | ~~OptiScaler injection per game~~ | dropped 2026-10-22: Bazzite ships Decky Framegen (OptiScaler) and decky-lsfg-vk in the Portal | nothing |
| 4 | **Power + autotune** | done: six profiles + 8–35 W TDP slider (= autotune's ceiling), read-back line, debounced; autotune acts on the bottleneck (§18); Advanced expander (§19) | see Needs the maintainer → Power |

## Done (code, tests)

| Module | Backend | Panel | Notes |
|---|---|---|---|
| Hardware table + discovery | `hw.py` | Setup | one row per Claw; EX = MS-1T91 (board_name first) |
| Revertible sysfs | `sysfs.py` | every write | `last_error()` feeds the red rows |
| Health check + one line | `health.py` | Fixes | fixes: install XeSS, face buttons, hardware survey |
| Power profiles | `power.py` | Power | RAPL-MMIO on the EX (the MSI attributes are ignored there), HHD when it runs |
| Autotune | `autotune.py` | Power | v3: no target; measure the most the set TDP gives → balance CPU/GPU (keep only +2 %) → lowest TDP within 5 % → hold; rendered fps from XeLL via the shim, not generated frames |
| XeSS XMX + XeLL | `xess.py`, `launchenv.py`, `tools/clawdeck-launch.sh` | Setup, Per-game | fork → upstream fallback; `xmx` / `xell` per game; shim logs read per launch |
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

- **Publish a release on the fork** (IHulkSmashI/xess-xmx-linux) so the installer can deliver XeLL driver mode: merge
  `xell-driver-mode-probe` (5 commits, PR #23) and `review-fixes` (PR #24) into one branch, build the binaries
  (its CI builds the shim, the ICD and the Mesa 26.1.2 driver; the Mesa 26.3 job fails on the vanished
  `mesa-26.3.0-rc1` tag, see PR #24's notes), run `tools/make-release.py <version>` there, attach the zip to a
  release. Until then `assets/versions.json` has `use_fork: true` and the installer falls back to upstream's
  20261018, saying so in Setup. Once HHHHanasak1 merges, set `use_fork` back to false.
- **XeSS install "file not found"** (brief): the backend now reports the exact step and reason (download: URL and
  HTTP code; archive: what it unpacked where; install.sh: exit code and its last lines) in the toast and the red
  row, and logs it. Tap Install XeSS XMX once more and post the red line if it still fails.
- **XeLL markers**: with the fork build installed and "XeLL driver mode" on for a game that uses XeSS frame
  generation, the Per-game line should say "driver mode" after a launch (from `igdext_trace.log` in the prefix).
  With upstream's build it says "cross-vendor mode".
- **Quick Access**: the four panels with the D-pad at 1280×800: nothing truncated in the profile picker, the read-back
  line changes after every pick, a fast flick through the profiles toasts the last one only.
- **VRR fix**: Fixes → Install, restart, `edid-decode` 30–120, 120 Hz mode still offered, games at 35–45 fps no
  longer judder. Remove puts the factory range back.
- **Self-test from the UI**: Setup → Run self-test with no game running; expect 12 PASS.
- Pin tested versions in `assets/versions.json` (all are `pending-verification`, so installs ask "latest, untested?").
- After the first upstream-watch run: test the listed versions, then `python3 tools/upstream_watch.py --update-lock`.
