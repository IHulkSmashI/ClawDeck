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
