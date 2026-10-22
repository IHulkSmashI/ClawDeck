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
  XMX under Proton; ClawDeck installs it (from our fork while the XeLL driver-mode PRs #23/#24 wait upstream),
  probes it, and reads the shim's logs to show XeLL's mode per game.
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
