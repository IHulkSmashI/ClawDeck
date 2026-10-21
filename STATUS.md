# ClawDeck status

Updated 2026-10-21 (second pass). **Nothing has been tested on real hardware yet.** Everything below passes the backend tests against
fake sysfs trees and a fake Handheld Daemon, and the UI typechecks and builds into the Decky zip.

## Done (code, tests)

| Module | Backend | UI | Notes |
|---|---|---|---|
| Hardware table + discovery | `hw.py` | Setup | one row per Claw; EX = MS-1T91 (matches HHD's table) |
| Revertible sysfs + dry run | `sysfs.py` | Setup → Dry run | also covers HHD writes now (nothing sent in dry run) |
| Health check + one-tap fixes | `health.py`, `main.run_fix` | Setup | fixes: turn on HHD, install XeSS driver, save a hardware survey, map the Claw 8 EX face buttons via an InputPlumber override (`buttons.py`) |
| Power + profiles | `power.py` | Power | TDP via HHD, else MSI firmware limits (current Bazzite), else RAPL (MMIO first) |
| Which services run | `stack.py` | Setup | InputPlumber / PowerStation / HHD; never offers HHD next to InputPlumber |
| Per-game profiles, AC follow | `profiles.py`, `main.power_source_changed` | Power | Steam's battery-state event switches battery ↔ plugged slot mid-game |
| Profile import/export | `profiles.py` | Power → Advanced | JSON on Desktop; import from Desktop/Downloads, never overwrites |
| Autotune | `autotune.py` | Power | pauses on any manual change; saved TDP clamped to the current ceiling |
| Benchmark | `benchmark.py` | Power → Advanced | avg fps, 1 % low, watts, fps/W; CSV export |
| Per-game recommendation | `gamescan.py`, `compat.py`, `injector.plan` | Graphics | native XeSS → OptiScaler → lsfg-vk, with reasons; anti-cheat blocked |
| OptiScaler, OptiClick-style | `injector.py`, `updates.py` | Graphics | newest release, setup_linux.sh layout, OptiPatcher, REFramework, per-game INI fixes |
| Lossless Scaling (lsfg-vk) | `injector.py`, `updates.py` | Graphics | lsfg-vk v2 installed/updated; Lossless Scaling ownership checked in Steam |
| Always up to date | `updates.py`, `main._after_game` | Graphics → Updates | daily check, checksum-verified where upstream publishes sums, one-tap rollback; games refreshed after they close |
| Streaming | `streaming.py` | Display | Moonlight VRR (Nonary) installed/updated, added to Steam, one-tap stream to a Vibepollo PC |
| System change check | `changes.py` | toast at start | kernel / Mesa / controller / XeSS / Decky changes re-run the health check |
| Upstream watch | `tools/upstream_watch.py`, workflow | GitHub issue | daily versions + live parser canaries; Dependabot for the build |
| ClawTweaks extras | `power.py` | Power → Advanced | CPU energy mode (EPP) in profiles, TDP boost switch |
| Per-game launch env | `launchenv.py`, `tools/clawdeck-launch.sh` | Graphics | injector, `DXVK_HDR`, `MESA=` choice; merges user DLL overrides |
| XeSS driver | `xess.py`, `downloads.py` | Graphics | xess-xmx-linux state, install from fork release, probe, default Mesa |
| Proton picker | `proton.py` + `SteamClient.Apps.SpecifyCompatTool` | Graphics | Steam stays owner of config.vdf |
| Display and dock | `display.py` | Display | VRR, refresh range, HDR, SDR nits → `~/.config/environment.d/60-clawdeck-display.conf` on Save |
| Controller | `controller.py` | Controller | with HHD: everything under `controllers.claw.*` / `rgb.handheld.*`; with InputPlumber: status only (next) |
| Fans | `fans.py` | Battery & Fans | presets need HHD; without it the firmware curve runs (direct hwmon curve: next) |
| Sleep | `sleep.py`, `tools/suspend-cycle.sh` | Setup → Advanced | read-only checks; cycle soak tool |
| Battery | `battery.py` | Battery & Fans | 50–100 % charge limit |
| Budget meter | `tools/soak.py` | — | CPU %, peak RSS, RSS slope against the release budget |
| Browser preview | `preview/` (`pnpm preview`) | all tabs | the real tab code with example data, no Claw needed |
| Profiles without the plugin | `tools/power-profile.sh` | — | battery / performance / dock / reset / status, `--dry-run` |

## Checked against upstream sources (2026-10-21, no hardware needed)

- Steam client calls against @decky/ui 4.16 typings; fixed: no install-dir field exists (now read from Steam's
  appmanifest), `Router` imported from @decky/ui.
- Current Bazzite: no Handheld Daemon; InputPlumber + PowerStation + Steam's TDP slider (steamos-manager → MSI
  firmware attributes). ClawDeck now sets TDP the same way when HHD is absent and no longer suggests installing HHD.
- gamescope-session variables (Bazzite uses ChimeraOS's via Terra); no Claw device quirk.
- Release assets and OptiScaler 0.9.4 INI keys (fixed `FGType` → `FGInput`/`FGOutput=xefg`, DX11 → `xess_12`).
- The IHulkSmashI xess-xmx-linux fork has no releases: the installer uses upstream's (`use_fork: false`).

## Tested on real hardware

2026-10-21, Claw 8 EX on Bazzite deck 44.20261012.1: plugin loads in Decky 3.3.0, hardware detected, health check and power/battery readouts run. Findings and the resulting task list: `docs/HARDWARE-FINDINGS.md` (the big one: this Bazzite image has no Handheld Daemon; InputPlumber + PowerStation instead).

## Next (on the Claw 8 EX, in this order)

1. `tools/sysfs-survey.sh` → paste into an issue; fix any path in `hw.py` that differs.
2. Sideload the CI zip; Setup tab: every check shows a real value.
3. Turn on **Dry run**, apply each profile, read the log; then off, and measure fps/W in two games (Benchmark).
4. Verify the items in docs/DESIGN.md → "Open items" one by one.
5. `tools/soak.py --hours 2` with autotune on, then `--hours 4` for the leak soak.
6. `sudo tools/suspend-cycle.sh 100` before saying anything about sleep.

## Gaps the README now states honestly (to fix in code)

- Fan presets need Handheld Daemon, which current Bazzite doesn't run: a direct hwmon curve is the next step.
- The README download button points at Releases, which stays empty until the first tested release.

## Needs the maintainer

- New UI (2026-10-21): check on the Claw that Quick Access shows ClawDeck's short panel, that **All settings** opens
  the full-screen page, and that every page can be read and used with the D-pad alone (sidebar left/right, each
  explanation scrolls into view with its control). Report anything you still have to touch.

- Face buttons on the Claw 8 EX (`buttons.py`, Setup → "Map the face buttons"): the same InputPlumber override was
  applied by hand on the maintainer's Claw on 2026-10-21 and both buttons work in Game Mode. To test the plugin's
  path: `sudo rm /etc/inputplumber/devices.d/50-msi_claw8_ex_ai_cg3em.yaml /etc/inputplumber/capability_maps.d/msiclaw_claw8ex.yaml /etc/udev/hwdb.d/61-msi-claw-8-ex.hwdb; sudo systemd-hwdb update; sudo systemctl restart inputplumber`,
  confirm the health check goes red, tap the fix, confirm green and both buttons (left = Steam menu, right = Quick
  Access). Upstream PR to ShadowBlip/InputPlumber: `docs/upstream/inputplumber-claw8ex.md`.

- 30–120 Hz VRR (Display tab, `edid.py`): run the new survey first and post its "panel EDID" line. Turn it on only
  if the sha256 matches (the switch only appears then), restart, and check: the 120 Hz mode is still offered, the
  Display tab shows 30–120 Hz, games at 35–45 fps no longer judder. If anything is wrong: previous boot entry, then
  turn it off.

- Test the hardware-findings fixes on the Claw (docs/HARDWARE-FINDINGS.md → "Done in code"): Setup shows "Claw 8 EX
  AI+ (MS-1T91)" and a Mesa version; Battery shows Wh and, after a minute on battery, watts; Controller shows
  InputPlumber's device; with Dry run off, set 20 W and read back `ppt_pl1_spl` and `intel-rapl-mmio:0`; then set
  the charge limit to 80 % and read back `charge_control_end_threshold`.
- TDP revert: the firmware attributes start at 0 (unset) and their min is 8, so check what reverting to 0 does when a
  game closes (stays at the last value, or goes back to the firmware default). If writing 0 is refused, revert must
  write the boot value read from `intel-rapl-mmio:0` instead.
- Does Steam's own TDP slider change anything on the EX? PowerStation reports no TDP range for this chip, so it
  probably does nothing; the README and the Power tab now say "may do nothing".

- Check the install guide's Claw steps on the 8 EX: the boot-menu buttons (the guide links MSI's FAQ instead of naming
  them), the Bazzite image picker wording, and the Decky "Install plugin from ZIP file" path.

- After the first upstream-watch run: test the listed versions on the Claw, then `python3 tools/upstream_watch.py --update-lock`.

- Pin tested versions in `assets/versions.json` (all are `pending-verification`, so installs ask "latest, untested?").
- The fork publishes no releases, so the installer downloads HHHHanasak1's. To ship from the fork, publish a release
  there (its `tools/make-release.py` builds the zip) and set `"use_fork": true`.
- Decide how ClawDeck relates to Bazzite's own OptiScaler and Lossless Scaling Decky plugins (defer to them, or
  replace them).
- Look over the store banner (`assets/banner.png`, source `assets/src/banner.html`) and the first post draft
  (`docs/posts/01-claw-8-ex-on-bazzite.md`); neither is published anywhere.
