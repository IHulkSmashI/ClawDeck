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
| Revertible sysfs + dry run | `sysfs.py` | Setup → Dry run | `last_error()` feeds the red rows |
| Health check + one line | `health.py` | Fixes | fixes: install XeSS, face buttons, hardware survey |
| Power profiles | `power.py` | Power | RAPL-MMIO on the EX (the MSI attributes are ignored there), HHD when it runs |
| Autotune | `autotune.py` | Power | slider/profile = ceiling, model min or user floor; 10 s window; GPU-bound → GPU floor, CPU-bound → boost + ceiling, then watts; on target → watts first |
| XeSS XMX + XeLL | `xess.py`, `launchenv.py`, `tools/clawdeck-launch.sh` | Setup, Per-game | fork → upstream fallback; `xmx` / `xell` per game; shim logs read per launch |
| VRR range fix | `edid.py` | Fixes | ClawLab's validated panels only; `drm.edid_firmware` + initramfs-etc |
| Face buttons (EX) | `buttons.py` | Fixes | InputPlumber override, `claw8ex` map |
| Self-test | `tools/selftest.py`, `main.self_test` | Setup | PASS/FAIL per control; refuses while a game runs or in dry run |
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

- **Power (§17–§19), on the Claw:** move the TDP slider: the profile turns Custom and "RAPL reads N W" follows; pick
  Max 35 W and check whether PL1 above the chip's `max_power_uw` (25 W) takes effect (busy loop or
  `tools/power-profile.sh`). With Autotune on in a GPU-heavy game, the status line should say GPU-bound and the GPU
  floor should climb before the watts; in a CPU-heavy one (Dragon's Dogma 2 town), CPU boost / ceiling first. Open
  Advanced, change CPU boost, the CPU ceiling, both GPU clocks and the energy mode: each toast reads back, and
  Autotune shows "paused".
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
