# Hardware findings — Claw 8 EX on Bazzite 44 (2026-10-21)

First run of the backend on the real device (`docs/surveys/claw8ex-bazzite-44.20261012.1.txt` has the raw survey).
The plugin loads in Decky and the health check runs. These are the facts that differ from what the code assumed, in
priority order. Each one is a task; tick it off in STATUS.md when done and tested on the device.

**Done in code (2026-10-21), waiting for a test on the device:** 1 (TDP clamped to the firmware's own min/max, no
PL3; Controller tab shows InputPlumber's device and profile over D-Bus), 2 (autotune reads PowerStation's
`GpuBusyPercent`), 3 (gt0 picked explicitly), 4 (board_name first; the EX now gets its own profiles, before it got
the defaults), 5 (Intel ICD json → library → `Mesa X.Y.Z`, rpm as fallback; the health check passes when the driver
exists), 6 (Wh from µAh × µV, power from `charge_now` change, `current_now` only when plausible, health capped at
100 %), 7 (threshold 0 shown as 100 %), 10 (quiet log; tools already run through `bash`). Still open, all need the
device: fan curves without Handheld Daemon (1; the survey now lists `msi_wmi_platform`'s fan, pwm and temp files),
charge-limit write test (7), sleep soak (8), and 11 below.

1. **Handheld Daemon is not on this image.** Bazzite deck 44 ships InputPlumber (controller) + PowerStation (power)
   + steamos-manager. There is no `hhd` package and no ujust recipe. Every HHD code path is a fallback at best.
   - TDP: write MSI's firmware attributes directly (`ppt_pl1_spl` 8–35 W, `ppt_pl2_sppt` 9–45 W, no PL3 on the EX).
     The effective limits visible to the kernel are `intel-rapl-mmio:0` (25 W / 37 W at boot); PowerStation reads
     those as TDP 25 / Boost 12 but reports MinTdp = MaxTdp = 0, so Steam's own slider cannot set anything on this
     chip. Verify after a write whether `intel-rapl-mmio:0` follows the firmware value (it should: the EC applies it).
   - Controller: InputPlumber already has `50-msi_claw8_ex_ai_cg3em.yaml` and builds the composite device with the
     MSI-button keyboard (event4). The Controller tab should read InputPlumber's D-Bus (`org.shadowblip.InputPlumber`),
     not HHD. Whether the MSI buttons reach Steam in Game Mode is still untested (Desktop Mode shows nothing by
     design; evtest cannot see the device while InputPlumber holds it).
   - Fans: `hwmon7 msi_wmi_platform` is present; check which `pwm*` / `fan*` files it exposes before offering curves.
2. **GPU busy % exists** — not in sysfs (xe has no `gpu_busy_percent`) but as PowerStation's `GpuBusyPercent`
   property on `/org/shadowblip/Performance/GPU/card0`. Autotune's classifier should read it over D-Bus (`busctl` or
   the `dbus`/`jeepney` module) instead of inferring from clocks. PowerStation also exposes writable GPU clock
   limits; direct sysfs (`tile0/gt0/freq0/{min,max}_freq`) works too and is what we use.
3. **Two GTs.** `gt0` is graphics (100–2300 MHz, efficient 1000), `gt1` is media (100–1200). Only gt0 is the lever;
   never touch gt1. `power_profile` on gt0 is `[base] power_saving` — note it, leave it.
4. **DMI**: `product_name` is "Claw 8 EX AI+ CG3EM Launch Pack", not the board id. Detection must match on
   `board_name` (MS-1T91) first. (Current code falls back to board_name; make it primary.)
5. **Mesa detection failed** ("no libvulkan_intel.so found") although `/usr/lib64/libvulkan_intel.so` exists and the
   ICD json points at it. Fix `mesa_version()`: read `/usr/share/vulkan/icd.d/intel_icd.x86_64.json` → `library_path`,
   then regex `Mesa \d+\.\d+\.\d+` on that file; our old regex matched "Mesa 26.f" (a different string).
6. **Battery units**: BAT1 reports `charge_*` in µAh with `voltage_now` in µV, no `energy_*`, no `power_now`, and
   `current_now` is not credible (−65 A idle). Compute Wh = charge × voltage / 1e12, and power from the change of
   `charge_now` over time (µAh/s × V), never from `current_now`. Health % = charge_full / charge_full_design = 103 %
   (new pack); cap display at 100.
7. **Charge limit**: `charge_control_end_threshold` exists (0 = unset). Writing it is untested; test 80 first and read
   it back; also check whether the EC honours it (battery stops at 80 % on the charger).
8. **Sleep**: s2idle is the default and one suspend cycle succeeded with the S0ix counter advancing — a good sign;
   keep the read-only checks, run `tools/suspend-cycle.sh` before any claim.
9. **Tools present**: mangohud, gamescope. Missing: hhdctl (expected), ocloc (xess-xmx-linux brings its own).
10. **Decky**: v3.3.0 via `ujust setup-decky`. The plugin folder is made immutable by Decky, so `chmod` of tools at
    start logs "Operation not permitted" — harmless, but make that log line quieter and use `bash tools/x.sh` instead
    of relying on the executable bit.
11. **VRR range** (added 2026-10-21): the screen's EDID says 48–120 Hz although the panel does 30–120 Hz; ClawLab's
    Windows fix overrides the EDID. Display → "VRR fix 30–120 Hz" does the same on Bazzite (`edid.py`: only
    ClawLab's validated factory EDIDs, only their range bytes, plus the DisplayID checksum Linux checks), through
    `drm.edid_firmware` and `rpm-ostree initramfs-etc`, as Bazzite does for the AOKZOE A1. Needs the device: the
    survey's "panel EDID" line (does its sha256 match ClawLab's `e49bc570…`, does Linux show 48–120?), then turn it on,
    restart, check `edid-decode`/the Display tab shows 30–120 and the 120 Hz mode is still there, play at 35–45 fps.

Open, needs the maintainer at the device: MSI buttons in Game Mode (left = Steam, right = Quick Access), each tab's first use
with Dry run on, then TDP write-and-read-back, then the GPU window, then charge limit 80 %.

## 12. Face buttons dead in Game Mode (found 2026-10-21, fixed in `buttons.py`)

Measured with evtest on every input device (InputPlumber stopped), BIOS E1T91IMS.10D:
right face button → KEY_F15 from "MSI WMI hotkeys" (plus scancode b9 on the AT keyboard); left face button →
KEY_LEFTMETA + KEY_G (Win+G) from "AT Translated Set 2 keyboard". InputPlumber's config for MS-1T91 uses map
`claw2`: Guide = chord F15+G, Guide(long) = F15+Tab, QuickAccess = F16. None of those ever occur on this firmware,
so both buttons are dropped. (A first attempt mapped scancode b9→F15 through hwdb; it was a red herring: F15 already
arrives from the WMI device, the chord is what fails.) Fix: `/etc/inputplumber/devices.d` copy of the device config
pointing at a `claw8ex` map (Guide = KeyG, QuickAccess = KeyF15, M1/M2 unchanged). Upstream: InputPlumber.

## 12. MSI firmware TDP attributes are ignored on the Claw 8 EX; RAPL-MMIO is the real control (2026-10-21, fixed in `power.py`)

Measured with an all-core busy loop and `intel-rapl:0/energy_uj` (BIOS E1T91IMS.10D, AC):

| written to | PL1/PL2 | package power |
|---|---|---|
| `msi-wmi-platform` ppt_pl1_spl / ppt_pl2_sppt | 17/24 | 36 W |
| same | 35/45 | 37 W |
| same | 10/12 | 37 W |
| `intel-rapl-mmio:0` constraint_0/1 | 17/24 | 23 W |
| same | 10/12 | 11 W |
| same | 25/37 (defaults) | 37 W |

So the firmware attributes accept values but the EC does not apply them (on this BIOS, outside MSI Center's "user"
mode, or at all); `intel-rapl-mmio:0` (defaults 25 W / 37 W, `max_power_uw` 25 W) is what the SoC enforces, and the
MSR domain `intel-rapl:0` sits at 35/50 W above it. Writing 0 back to the firmware attributes (their initial value)
is refused with EINVAL, so they cannot even be reverted. ClawDeck now writes RAPL (PL2 before PL1 when raising,
PL1 first when lowering), raises the other RAPL domain only if it would otherwise cap lower, and never touches the
MSI attributes while RAPL exists. Open question for the maintainer: does a RAPL PL1 above `max_power_uw` (25 W) take
effect for the 35 W plugged profile? Measure with `tools/power-profile.sh` or the same busy loop.
Also: the dry-run tour of every tab wrote only in-range values and the revert list was complete (good).

## 13. First live run: every write refused — the plugin was not running as root (2026-10-21, fixed)

`plugin.json` had the Decky template's `"flags": ["_root"]` (underscore = off), so the backend ran as the user and
every sysfs write (RAPL 17 W, max_perf_pct, EPP, GPU window) and the VRR initramfs rebuild failed with
`Permission denied`. Fixed to `["root"]`, with a test. Dry-run mode never showed this because it does not write.

## 14. "I can't tell whether anything changed" (maintainer, first live run) → self-test + read-back

`tools/selftest.py` (run as root) writes each control through the backend, reads it back from sysfs, measures the
effect (package power under load, GPU ceiling, charge threshold), reverts, and prints PASS/FAIL. For the UI
(Claude Code): every control must show the value **re-read from sysfs** after the write, and the toast must say
"set 17 W → hardware reads 17 W" (or the error), never just "applied". A failed write must surface as red text in
the tab, with the log line, not a silent no-op.

Done in the UI (2026-10-21): every write endpoint (`set_tdp`, `set_cpu_max_pct`, `set_epp`, `set_cpu_turbo`,
`set_gpu_window`, `reset_gpu_window`, `apply_profile`, `revert_power`, `set_charge_limit`) answers
`{ok, wanted, read, summary, error}`; `read` is re-read from sysfs after the write (`power.read_tdp()` from the
backend that enforces it: RAPL on the EX) and `ok` is false when it differs from `wanted`. `sysfs.last_error()` keeps
the OSError text ("Permission denied") for `error`. The toast shows `summary`; each tab keeps the last `error` in a
red `Problem` row under the control; sliders and the profile description show the value the hardware reads now.
The same red row covers the fixes in Setup, fan presets, display save/reset, the VRR fix, Moonlight, streaming
host, OptiScaler / lsfg-vk / XeSS installs and Handheld Daemon controller settings.

## 15. Every download failed: Decky's Python has no CA store (2026-10-21, fixed in `tls.py`)

`update check for optiscaler/optipatcher/reframework/lsfg/moonlight failed: CERTIFICATE_VERIFY_FAILED ... unable to
get local issuer certificate`. Decky Loader's bundled Python does not find the system certificates. All HTTPS now
goes through `tls.urlopen`, which loads `/etc/ssl/certs/ca-certificates.crt` (Bazzite) or the first bundle that
exists; verification stays on. The VRR switch's exception was `Permission denied: '/etc/firmware'` (§13, root).

## 16. UI: profile names truncated in the Power tab's picker (maintainer, 2026-10-21) — for Claude Code

The dropdown shows a cut-off profile name. Use short labels in the control (e.g. "Battery 12 W") with the full
description in the Hint below it, and verify at 1280×800 in Game Mode.

Done (2026-10-21): the picker's options are "Battery 12 W", "Balanced 17 W", "Performance 25 W", "Dock 35 W",
"Custom 17 W"; what each profile is for, and the watts the hardware reads now, are in the control's description.
Still to verify at 1280×800 in Game Mode (STATUS → Needs the maintainer).

## 17. Scope reset (2026-10-21 evening, project brief) — done in code 2026-10-22, for the device

The plugin keeps only what Bazzite and Steam's Quick Access do not do: four panels (Power, Per-game, Fixes, Setup).
Three bugs from the brief, and what changed:

- **XeSS XMX install fails with "file not found"**: the fork (IHulkSmashI/xess-xmx-linux) has no release, so with
  `use_fork` the GitHub API answers 404 "Not Found". The installer now tries the fork first and, on 404, upstream's
  release (`xess-xmx-linux-20261018.zip`, verified asset name) with a note; every other failure names the step
  (download / archive / install.sh) and the real reason in the toast, the red row and the log. Needs the device to
  confirm the original failure is this one.
- **TDP toast shows the previous value when the dropdown is changed quickly**: the profile picker is debounced
  (300 ms) and only the newest change may toast; the backend serialises profile writes behind one lock and reads
  back after the write; the line under the control shows "RAPL reads 17 W / 24 W" from that read-back.
- **Profile label truncated in Quick Access**: short labels ("Battery 12 W"), the full name under the control.

XeLL per game: the fork's shim logs into the prefix (`drive_c/igdext_trace.log`, `igdext_xell.log`) when
`IGDEXT_XELL_PROBE` / `IGDEXT_XELL_LOG` / `IGDEXT_TRACE` are set; the launch wrapper sets them when "XeLL driver
mode" is on for the game and rotates the logs per launch, and `xess.game_xell` reads them: `LatencyReductionExt(`
/ `RenderSubmitStart(` = driver mode, `xellSetSleepMode` only = cross-vendor. Markers come from the branch's
source (`shim/src/dll/XellDriver.cpp`, `XellHook.cpp`); confirm with Wuthering Waves or Cyberpunk 2077 with XeSS FG on.

## 17. Power panel: full-range TDP slider + autotune ceiling (maintainer, 2026-10-22) — for Claude Code

Why ClawDeck sets TDP at all (keep this in the Hint): Steam's TDP slider is a no-op on the Claw 8 EX, because
PowerStation reports MinTdp = MaxTdp = 0 for Panther Lake (survey §1); RAPL-MMIO is the only working control (§12).

Wanted:
- A **TDP slider over the full hardware range** (8–35 W on the EX, from `hw.MODELS[...]["tdp"]` and the RAPL
  `max_power_uw`), 1 W steps, next to the profile dropdown. Moving the slider = "Custom" profile; picking a profile
  moves the slider to that profile's watts. Read-back line stays ("RAPL reads 17 W / 24 W").
- **More profiles**: Battery saver 10 W, Battery 12 W, Balanced 17 W, Performance 25 W, Plugged 30 W, Max 35 W —
  short labels, watts in the label, full description in the Hint (what/why/cost, as everywhere).
- **Autotune uses the slider as its ceiling**: the current TDP (slider or profile) is the maximum autotune may use;
  its floor is the model minimum (8 W) unless the user sets one. Show both in the autotune status line
  ("Autotune on: 8–25 W, now 19 W, target 60 fps, last change −1 W 12 s ago"). Changing the slider while autotune
  runs re-clamps immediately.
- Boost (PL2) stays derived as today (`_sppt_for`), no separate control.

## 18. Autotune must say *what* is short and act on it (maintainer, 2026-10-22) — for Claude Code

Autotune today only moves the TDP. It already classifies frames as CPU- or GPU-bound (`autotune.classify`:
frame time vs GPU busy % from PowerStation / RAPL power). Use that:
- **GPU-bound and below target** → raise the GPU floor (min_freq) before raising TDP; if at the ceiling, raise TDP.
- **CPU-bound and below target** → make sure CPU boost (`no_turbo = 0`) is on and the CPU ceiling (`max_perf_pct`)
  is 100 before raising TDP; log which one it changed.
- **Above target with margin** → lower TDP first (battery); only lower the GPU floor / CPU ceiling if TDP is at
  the floor.
- Status line names the bottleneck and the last action: "GPU-bound · 52/60 fps · raised GPU floor to 1300 MHz".
  Every learned per-game result keeps TDP, GPU floor, CPU ceiling and boost together.
- Hysteresis as today; never more than one change per sampling window; all writes revertible.

## 19. Advanced power controls, because Bazzite has none for this chip (maintainer, 2026-10-22) — for Claude Code

Steam's Performance panel offers no CPU boost, CPU ceiling, GPU clock window or EPP on the Claw 8 EX
(PowerStation: no TDP range; the GPU clock entries exist but Steam does not expose them for Intel), so put them
back — but under an **"Advanced" expander** in the Power panel, off the main view:
- CPU boost on/off (`intel_pstate/no_turbo`), CPU ceiling % (`max_perf_pct`), GPU floor/ceiling MHz
  (`gt0/freq0/min_freq`/`max_freq`, from `rpn`..`rp0`), EPP (performance / balance_performance / balance_power /
  power).
- Each with the what/why/cost Hint and the read-back value; profiles may set them; autotune (§18) may move them.
- The main view keeps only: profile, TDP slider (§17), autotune, read-back line.

## 20. VRR fix wrote the EDID but never set the kernel arguments: Decky's LD_LIBRARY_PATH (2026-10-22, fixed)

`rpm-ostree initramfs-etc --track=…` died with `libsolv.so.1: undefined symbol: EVP_MD_CTX_dup, version
OPENSSL_3.1.0`. Decky Loader is a PyInstaller bundle and exports `LD_LIBRARY_PATH` to its private libraries; every
child of the plugin inherited it and rpm-ostree loaded Decky's OpenSSL. main.py now restores the `*_ORIG` values
(or drops the variables) at startup, and `sysfs.clean_env()` is used for the tools that matter most. The same leak
would have hit any other system tool with its own OpenSSL. Also: the XeSS status line is read from the session
check's file, which is only rewritten at login — right after an install the panel keeps showing the previous
verdict; the plugin should run the check itself after installing (see xess.py).
