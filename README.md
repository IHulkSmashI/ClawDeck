# ClawDeck

**The MSI Claw on Bazzite / SteamOS, in one Quick Access panel.** Power profiles, per-game autotune, XeSS on the
XMX cores for games that don't ship it, battery charge limit, and a health check that tells you what's missing and
fixes it in one tap.

Built and tested on the **MSI Claw 8 EX AI+** (Panther Lake, Arc B390) on Bazzite. Lunar Lake and Meteor Lake Claws
are in the hardware table; AMD handhelds can follow. Status: **early development** — see [Roadmap](#roadmap).

> Everything here is open source and goes upstream where it belongs: the MSI buttons, TDP and fans are
> [Handheld Daemon](https://github.com/hhd-dev/hhd)'s, the XeSS XMX driver is
> [xess-xmx-linux](https://github.com/HHHHanasak1/xess-xmx-linux)'s, the clock paths come from
> [PowerControl](https://github.com/mengmeet/PowerControl). ClawDeck orchestrates them and adds what nobody had:
> autotune, the XMX injector, per-game profiles and the health check.

## What it does (module by module)

| Tab | Toggle / control | What it does | Why you'd turn it on | Cost |
|---|---|---|---|---|
| Setup | Health check | Shows kernel, Mesa, Handheld Daemon, XeSS driver, GPU/CPU/battery controls, with the fix for each missing item | Know in 5 seconds why something doesn't work | none |
| Setup | Dry run | Logs every value ClawDeck would write instead of writing it | Test safely | nothing changes |
| Power | Autotune | Learns, per game, the lowest power that holds your target fps and whether the game is CPU- or GPU-bound; applies it next launch | Longer battery, same smoothness | first minutes in a new game may stutter |
| Power | Profiles: Battery / Performance / Dock | TDP + CPU ceiling + GPU clocks in one tap | Dock or unplug without fiddling | reverts when the game closes unless saved |
| Power | TDP | Package power limit through Handheld Daemon | 17–25 W is the Claw 8 EX sweet spot on battery | heat and fan noise |
| Power | CPU ceiling | Caps CPU clocks so the GPU gets the budget | GPU-bound games: same fps, less heat | CPU-heavy scenes slow down |
| Power | GPU floor / ceiling | Clock window for the GPU | Floor fixes clock-dip stutter; ceiling helps CPU-bound games | floor costs battery |
| Graphics | XeSS on XMX | Status of the xess-xmx-linux driver and XeLL driver mode | Native XeSS games get Windows-level upscaling and frame generation | first launch compiles kernels |
| Graphics | XMX injector | OptiScaler (DLSS/FSR → XeSS on XMX, optional XeSS FG swap) or lsfg-vk frame generation, per game | Higher fps in games without native XeSS | some input delay; anti-cheat games refuse it |
| Battery | Charge limit 50/60/70/80/90/100 % | Stops charging at the chosen percent, re-applied at boot | 80 % roughly doubles battery lifespan | less runtime per charge |
| Controller | Handheld Daemon status | Where the MSI buttons, gyro, RGB live; update hint when they don't work | — | — |
| Display | (next milestone) | VRR range, dock 4K120 HDR flags, per-game refresh | — | — |

## Install

Not released yet. When it is: download `ClawDeck.zip` from Releases, Decky → settings → **Install plugin from zip**.
Until then, the CI build artifact on every commit is the same zip.

Requirements: Bazzite (deck image) or any distro with Decky Loader; Handheld Daemon running (Bazzite ships it; run
`sudo rpm-ostree upgrade` once if the MSI buttons don't work — the Claw 8 EX was added to HHD in 2026); MangoHud for
autotune's frame times.

Autotune and the injector need the per-game launch wrapper once per game. Steam → game → Properties → Launch options:

    ~/homebrew/plugins/ClawDeck/tools/clawdeck-launch.sh %command%

## Engineering rules (non-negotiable)

- **Battery first.** No timers unless autotune is on for a running game; then one asyncio task at 1 Hz. Budget:
  < 0.5 % CPU and < 30 MB RSS over a 2-hour run, flat RSS over a 4-hour soak, measured before every release.
- **Fail safe.** Every sysfs write is recorded and revertible; a failed write reverts; everything reverts when the
  game closes; nothing persists across reboot except the charge limit (that's its job).
- **Dry run.** `CLAWDECK_DRY_RUN=1` or the Setup toggle prints what would be written.
- **No duplication.** TDP, fans, buttons: Handheld Daemon. Clock paths: PowerControl's discovery. XeSS: xess-xmx-linux.
- **Tests before hardware.** `python3 -m pytest -q tests` runs the backend against a fake sysfs tree; the hardware
  table is one row per model, so a new Claw is one line plus a survey (`tools/sysfs-survey.sh`).

## Contributing

Open an issue with the output of `tools/sysfs-survey.sh` for a model that isn't in the table. Bug reports: Setup →
"Save a bug report to Desktop" and attach the JSON. Profiles for your games: Power → export, and share the JSON.

## Roadmap

1. **0.1** — hardware table, health check, power (HHD TDP + clocks), profiles, charge limit, autotune core. ← now
2. **0.2** — XMX injector verified on the Claw (OptiScaler + XeSS 2.x set, lsfg-vk), xess-xmx-linux installer.
3. **0.3** — Display and dock (VRR, 4K120 HDR), per-game Proton picker, profile import/export UI.
4. **0.4** — Autotune validated on Dragon's Dogma 2 and Wuthering Waves with published before/after numbers.
5. **1.0** — Decky store submission.

## Support

If ClawDeck saves you time or battery: [ko-fi.com/gamervault](https://ko-fi.com/gamervault). It is and stays free; tips fund test hardware and coffee.

## License

MIT. Upstream projects keep their own licenses; see each link above.
