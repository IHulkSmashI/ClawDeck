# Upstream: InputPlumber — MSI Claw 8 EX AI+ face buttons

Target: https://github.com/ShadowBlip/InputPlumber (file `rootfs/usr/share/inputplumber/devices/50-msi_claw8_ex_ai_cg3em.yaml`
and a new `rootfs/usr/share/inputplumber/capability_maps/msiclaw_type3.yaml`).

## Issue text

**Title:** MSI Claw 8 EX AI+ (MS-1T91): face buttons never match the `claw2` capability map

On a Claw 8 EX AI+ (board MS-1T91, BIOS E1T91IMS.10D 2026-09-07, Bazzite deck 44.20261012.1, InputPlumber 0.82.0)
neither face button does anything in Game Mode. With InputPlumber stopped, evtest on every input device shows:

- right face button (MSI Center): `KEY_F15` from the "MSI WMI hotkeys" device (scancode 0x29) — and scancode 0xb9
  on the AT keyboard, which hwdb does not map for MS-1T91
- left face button (Quick Settings): `KEY_LEFTMETA` + `KEY_G` (Win+G) from "AT Translated Set 2 keyboard"

`50-msi_claw8_ex_ai_cg3em.yaml` uses `capability_map_id: claw2`, where Guide is the chord `KeyF15 + KeyG`,
Guide (Long) is `KeyF15 + KeyTab` and QuickAccess is `KeyF16`. On this firmware F15 and G come from different
buttons and F16 never appears, so nothing matches. The device config's include list for the AT keyboard already
passes `KeyG` and drops `KeyLeftMeta`, so a map with `KeyG` → Guide and `KeyF15` → QuickAccess makes both buttons
work (verified on the unit above; M1/M2 unchanged). If other EX units/BIOS versions really emit the chord, the
two maps could coexist via a BIOS-version match, but I could not find a unit that does.

## Patch

```yaml
# rootfs/usr/share/inputplumber/capability_maps/msiclaw_type3.yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/ShadowBlip/InputPlumber/main/rootfs/usr/share/inputplumber/schema/capability_map_v1.json
version: 1
kind: CapabilityMap
name: MSI Claw Type 3
id: claw3
mapping:
  - name: Guide
    source_events:
      - keyboard: KeyG
    target_event:
      gamepad:
        button: Guide
  - name: QuickAccess
    source_events:
      - keyboard: KeyF15
    target_event:
      gamepad:
        button: QuickAccess
  - name: M1
    source_events:
      - keyboard: KeyRightBrace
    target_event:
      gamepad:
        button: RightPaddle1
  - name: M2
    source_events:
      - keyboard: KeyLeftBrace
    target_event:
      gamepad:
        button: LeftPaddle1
filtered_events: []
```

```diff
--- a/rootfs/usr/share/inputplumber/devices/50-msi_claw8_ex_ai_cg3em.yaml
+++ b/rootfs/usr/share/inputplumber/devices/50-msi_claw8_ex_ai_cg3em.yaml
@@
-capability_map_id: claw2
+capability_map_id: claw3
```

## How to submit (from your GitHub account)

1. Fork ShadowBlip/InputPlumber on github.com, open the two files in the web editor, apply the patch above.
2. Open the PR with the issue text as its description. Mention it was tested on Bazzite 44.20261012.1.
3. Once merged, Bazzite picks it up at its next InputPlumber bump; ClawDeck's health check then reports the
   override as unnecessary.
