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
| Setup | Health check + one-tap fixes | Kernel, Mesa, Handheld Daemon, XeSS driver, lsfg-vk, GPU/CPU/battery controls, sleep; a button for each fixable item | Know in 5 seconds why something doesn't work | none |
| Setup | Dry run | Logs every value ClawDeck would write (sysfs and Handheld Daemon) instead of writing it | Test safely | nothing changes |
| Setup → Advanced | Sleep | Sleep mode, suspend failures, deep-idle (S0ix) time, wake sources, known fixes | Find out why the Claw drains or wakes in the bag | none (read-only) |
| Power | Autotune | Learns, per game, the lowest power that holds your target fps and whether the game is CPU- or GPU-bound; applies it next launch | Longer battery, same smoothness | first minutes in a new game may stutter |
| Power | Profiles: Battery / Performance / Dock | TDP + CPU ceiling + GPU clocks in one tap; save per game, separately for battery and plugged in | Dock or unplug without fiddling: the game's profile follows the charger | reverts when the game closes unless saved |
| Power | TDP | Package power limit through Handheld Daemon | 17–25 W is the Claw 8 EX sweet spot on battery | heat and fan noise |
| Power → Advanced | CPU ceiling, turbo, GPU floor / ceiling | Split the shared power budget between CPU and GPU | GPU-bound: cap the CPU; clock-dip stutter: raise the GPU floor | CPU-heavy scenes slow down; floor costs battery |
| Power → Advanced | Benchmark | Records avg fps, 1 % low, watts and fps/W for a fixed time; CSV export | Numbers for battery vs dock, Linux vs Windows | needs the launch wrapper |
| Power → Advanced | Export / import profiles | Game profiles as JSON on the Desktop | Share tested settings | import never overwrites your games |
| Graphics | XeSS on XMX | Installs/updates xess-xmx-linux, shows driver mode, probe, default Mesa driver | Native XeSS games get Windows-level upscaling and frame generation | ~230 MB; first launch compiles kernels |
| Graphics | XMX injector | OptiScaler (DLSS/FSR → XeSS on XMX, optional XeSS FG swap) or lsfg-vk frame generation, per game | Higher fps in games without native XeSS | some input delay; anti-cheat games refuse it |
| Graphics | Proton version | Per-game GE-Proton / Proton through Steam's own setting | Newer games and video fixes sooner | may rebuild the shader cache |
| Graphics → Advanced | HDR output, XeSS driver per game | `DXVK_HDR=1`; Mesa 26.1 vs 26.3 for one game | HDR on the dock; work around a driver issue | washed-out colours without an HDR screen |
| Display | VRR, refresh range, HDR | Game Mode settings written on Save (`~/.config/environment.d/60-clawdeck-display.conf`) | Smooth 40–120 Hz, HDR on a TV | applies after Game Mode restarts |
| Controller | Mode, gyro, buttons, stick RGB | Everything Handheld Daemon offers for the Claw, read from HHD itself | One place, in Quick Access | none |
| Battery & Fans | Charge limit 50/60/70/80/90/100 % | Stops charging at the chosen percent, re-applied at boot | 80 % roughly doubles battery lifespan | less runtime per charge |
| Battery & Fans | Fan curve | Auto / Quiet / Balanced / Cool on Handheld Daemon's MSI fan curve | Quiet on battery, Cool on the dock | Quiet runs hotter |

Not done by ClawDeck, on purpose: games with kernel anti-cheat (they don't run on Linux), native PC Game Pass titles
(no Linux client; use Xbox Cloud Gaming), anything that changes kernel arguments for you.

## Install

Not released yet. When it is: download `ClawDeck.zip` from Releases, Decky → settings → **Install plugin from zip**.
Until then, the CI build artifact on every commit is the same zip.

Requirements: Bazzite (deck image) or any distro with Decky Loader; Handheld Daemon running (Bazzite ships it; run
`sudo rpm-ostree upgrade` once if the MSI buttons don't work — the Claw 8 EX was added to HHD in 2026); MangoHud for
autotune's frame times.

Autotune, the benchmark, the injector and per-game HDR need the launch wrapper once per game. Steam → game →
Properties → Launch options:

    ~/homebrew/plugins/ClawDeck/tools/clawdeck-launch.sh %command%

Tools that work without the plugin (in `tools/`): `power-profile.sh` (battery / performance / dock from a terminal),
`sysfs-survey.sh` (what a new model exposes), `suspend-cycle.sh` (sleep soak), `soak.py` (CPU/memory budget meter).

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

1. **0.1** — every module written and unit-tested (see [STATUS.md](STATUS.md)). ← now: needs its first run on a Claw 8 EX
2. **0.2** — paths and HHD keys verified on the Claw; power profiles measured (fps and watts in three games).
3. **0.3** — XMX injector and xess-xmx-linux installer verified; versions pinned in `assets/versions.json`.
4. **0.4** — Autotune validated on Dragon's Dogma 2 and Wuthering Waves with published before/after numbers; 2-hour
   budget and 4-hour leak soak passed (`tools/soak.py`); sleep passes 100 cycles.
5. **1.0** — Decky store submission.

Credits: [CREDITS.md](CREDITS.md).

## Support

If ClawDeck saves you time or battery: [ko-fi.com/gamervault](https://ko-fi.com/gamervault). It is and stays free; tips fund test hardware and coffee.

## License

MIT. Upstream projects keep their own licenses; see each link above.
