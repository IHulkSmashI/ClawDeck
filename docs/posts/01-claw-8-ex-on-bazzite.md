<!-- DRAFT. Not posted. Post only after every [fill in] below is filled from a real run on the Claw 8 EX, and only
     when the maintainer decides to announce (project rule: no promotion before the plugin has a working UI). -->

# Claw 8 EX on Bazzite: what works, what doesn't, and a plugin to tune it

**TL;DR:** Bazzite runs well on the Claw 8 EX next to Windows, with Secure Boot still on. ClawDeck is a free,
open-source Decky plugin that adds the things Linux was missing on this handheld: power profiles tuned for Panther
Lake, a per-game autotuner, XeSS on the XMX cores, and a battery charge limit.
Repo: https://github.com/IHulkSmashI/ClawDeck

**Tested on:** Claw 8 EX AI+ (MS-1T91), Bazzite [fill in: build, e.g. stable 44.20261020], kernel [fill in], Mesa
[fill in], [fill in: InputPlumber / Handheld Daemon] for the controller.

## Status

| Thing | Works? | Notes |
|---|---|---|
| Dual boot with Windows, Secure Boot on | [fill in] | MOK key, password `universalblue` |
| Both MSI buttons (Steam / Quick Access) | [fill in] | |
| Sleep and resume | [fill in] | [fill in: suspend-cycle.sh result, N cycles] |
| VRR 30–120 Hz on the panel | [fill in] | |
| Dock 4K120 HDR | [fill in] | |
| TDP control | [fill in] | Steam's slider and ClawDeck both work; last change wins |
| XeSS upscaling on XMX | [fill in] | via xess-xmx-linux |
| Kernel anti-cheat games | No | They don't run on Linux. Nothing we can do. |

## Numbers (same scene, same settings)

| Game | Profile | fps (1% low) | Watts | fps per watt |
|---|---|---|---|---|
| [fill in] | Battery 20 W | [fill in] | [fill in] | [fill in] |
| [fill in] | Dock 35 W | [fill in] | [fill in] | [fill in] |
| [fill in] | Windows, same settings | [fill in] | [fill in] | [fill in] |

Recorded with ClawDeck's Benchmark (Power → Advanced), 120 s per run.

## What ClawDeck does

- **Power:** Battery / Performance / Dock in one tap, per game, and it follows the charger. Cost: none; it reverts
  when the game closes unless you save it.
- **Autotune:** learns the lowest power that holds your target fps per game. Cost: the first minutes in a new game
  may stutter while it learns.
- **XeSS on XMX:** installs and checks the xess-xmx-linux driver; OptiScaler or lsfg-vk per game for games without
  XeSS. Cost: a little input delay; anti-cheat games refuse injected DLLs.
- **Battery:** charge limit 50–100 %; batteries wear slower when they don't sit at 100 %. Cost: less runtime
  per charge.

## Known broken

- [fill in from STATUS.md "Tested on real hardware"]

## How to help

- Bugs: GitHub issues, with the report from Setup → "Save a bug report to Desktop". Not Reddit comments, so they
  can be tracked.
- Feature requests: reply to the pinned comment.
- Got a different Claw (A1M, 7 AI+, 8 AI+)? Run `tools/sysfs-survey.sh` and post it in an issue.

## Credits

Handheld Daemon, InputPlumber and PowerStation, PowerControl, SimpleDeckyTDP, xess-xmx-linux, OptiScaler, lsfg-vk,
Bazzite / Universal Blue. Full list in CREDITS.md.

Not affiliated with MSI, Valve, Intel or Microsoft.
