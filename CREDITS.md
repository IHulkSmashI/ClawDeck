# Credits

ClawDeck orchestrates other people's work. Read, learned from, and called — not copied wholesale:

- **[Handheld Daemon](https://github.com/hhd-dev/hhd)** (LGPL-2.1) — MSI buttons, TDP through MSI firmware,
  fan curves, controller and RGB. ClawDeck sets TDP through its local API when it runs; the Claw model ids
  (MS-1T41/42/52/91/8K) and the EX's 35/45 W limits come from its source.
- **[InputPlumber](https://github.com/ShadowBlip/InputPlumber)** and **[PowerStation](https://github.com/ShadowBlip/PowerStation)**
  by ShadowBlip — the controller and power services on current Bazzite. ClawDeck detects them, works alongside them
  instead of replacing them, and reads PowerStation's GPU load and InputPlumber's controller status over D-Bus.
- **[Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader)** — the plugin system ClawDeck runs in.
- **[PowerControl](https://github.com/mengmeet/PowerControl)** — Intel xe/i915 GPU clock paths, RAPL package domain,
  intel_pstate levers, the AC-state listener pattern, reading PowerStation through `busctl`. Not forked.
- **[SimpleDeckyTDP](https://github.com/aarron-lee/SimpleDeckyTDP)** — Steam client listeners (battery state,
  suspend/resume) and Decky patterns.
- **[decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template)** — plugin layout and build.
- **[xess-xmx-linux](https://github.com/HHHHanasak1/xess-xmx-linux)** — XeSS super resolution and frame generation on
  XMX under Proton, with low latency; ClawDeck installs it, probes it, sets its per-game launch options and reads the
  shim's logs (low latency per game, rendered frames for Autotune).
- **[Decky Framegen](https://github.com/xXJSONDeruloXx/Decky-Framegen)** by xXJSONDeruloXx — OptiScaler per game on
  SteamOS / Bazzite. Its v0.17 OptiScaler + OptiPatcher pins (same release, same sha256) are where ClawDeck's own
  install started from, and its `Section_Key` ini form and Proton defaults (OptiPatcher loading, HQ font off) are
  followed. Games it patched are detected and left alone; nothing of its code is copied.
- **[OptiScaler](https://github.com/optiscaler/OptiScaler)** — the upscaler / frame generation swap ClawDeck installs
  per game (its pinned release, unchanged). Capcom RE Engine games get the v10 build from the
  **[OptiScaler nightly](https://github.com/optiscaler/OptiScaler-nightly)** releases (pinned with its sha256), installed
  in the `OptiScaler\` subfolder layout the v10 package ships.
- **OptiPatcher** — the OptiScaler plugin ClawDeck puts in a game's `plugins/` folder; the pinned build (sha256
  checked) comes from [OptiScaler-Bleeding-Edge](https://github.com/xXJSONDeruloXx/OptiScaler-Bleeding-Edge)'s releases.
- **[OptiClick](https://github.com/onehoon/OptiClick)** by onehoon — the Windows tool whose one-tap, per-game OptiScaler
  setup ClawDeck's "Use the recommended setup" follows (match by exe, REFramework for RE Engine games, per-game ini
  values). ClawDeck's Capcom setup is OptiClick's working Onimusha install used as the reference (its files, its
  OptiScaler.ini lines, its `OptiScaler\` layout). OptiClick's own game data is private; no code or data is shared.
- **[REFramework fork](https://github.com/onehoon/REFramework)** by onehoon (MIT, from praydog's REFramework) — the
  build OptiClick installs for Capcom games and ClawDeck installs as `dinput8.dll` (release 15, pinned with its
  sha256), downloaded from its releases, not shipped with ClawDeck.
- **OptiScaler's per-game settings** (`OptiScaler/misc/GameConfigs.cpp`, `Config.cpp`) — the source of
  `assets/games.json` (`tools/gen_games.py`), which is therefore GPL-3.0 like OptiScaler.
- **[REFramework](https://github.com/praydog/REFramework)** by praydog (MIT) — the mod framework for Capcom RE Engine
  games that onehoon's fork (above) builds on; ClawDeck installs the fork next to the game's exe, as OptiClick does.
- **[wimlib](https://wimlib.net)** by Eric Biggers (LGPL-2.1+) — its LZMS decoder is inside `tools/cabinet-lzms`, the
  open `cabinet.dll` that lets the FSR 4 on XMX builds unpack their kernels under Proton without Windows' copy.
- **FSR 4 on XMX** by nitec and the OptiScaler community — the upscaler build ClawDeck sets up; not shipped with ClawDeck.
- **[Bazzite](https://github.com/ublue-os/bazzite)** / Universal Blue — the image this is built and tested on.
- **Decky Framerate** — an early reference for reading frame times in Game Mode.



- **[ClawTweaks](https://github.com/enterTheVoidCode/ClawTweaks)** and **[GoTweaks](https://github.com/corando98/GoTweaks)**
  — the Windows tools whose feature list shaped ClawDeck's first version. No code is shared.
- **[ClawLab Intel VRR Range Fix](https://github.com/Zendo-GIT/Intel-Handheld-and-ARC-GPU-laboratory)** (MIT) — the
  30–120 Hz VRR fix for the Claw's screens on Windows. ClawDeck's Linux version uses ClawLab's list of validated
  panels, their factory EDID checksums and the exact bytes ClawLab changes, and the daily watch checks that list.
- **[MangoHud](https://github.com/flightlessmango/MangoHud)** — frame-time logging for autotune.

ClawDeck checks every project above for new releases every day (`.github/workflows/upstream-watch.yml`) and updates
the ones it installs for you; see CONTRIBUTING.md → Keeping up with upstream.

ClawDeck is not affiliated with MSI, Valve, Intel or Microsoft. "Claw" is an MSI trademark.
