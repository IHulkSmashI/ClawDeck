# Credits

ClawDeck orchestrates other people's work. Read, learned from, and called — not copied wholesale:

- **[Handheld Daemon](https://github.com/hhd-dev/hhd)** (LGPL-2.1) — MSI buttons, TDP through MSI firmware,
  fan curves, charge limit, controller and RGB. ClawDeck talks to its local API and reads its settings schema; the
  Claw model ids (MS-1T41/42/52/91/8K), the EX's 35/45 W limits and the fan curve points come from its source.
- **[PowerControl](https://github.com/mengmeet/PowerControl)** — Intel xe/i915 GPU clock paths, RAPL package domain,
  intel_pstate levers, the AC-state listener pattern. Not forked.
- **[SimpleDeckyTDP](https://github.com/aarron-lee/SimpleDeckyTDP)** — Steam client listeners (battery state,
  suspend/resume) and Decky patterns.
- **[decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template)** — plugin layout and build.
- **[xess-xmx-linux](https://github.com/HHHHanasak1/xess-xmx-linux)** — XeSS super resolution and frame generation on
  XMX under Proton; ClawDeck installs, probes and switches its drivers.
- **[OptiScaler](https://github.com/optiscaler/OptiScaler)** — DLSS/FSR → XeSS and XeSS frame generation injection.
- **[lsfg-vk](https://lsfg-vk.dev)** — Lossless Scaling frame generation as a Vulkan layer (needs the user's own
  Lossless Scaling purchase).
- **[gamescope-session](https://github.com/ChimeraOS/gamescope-session)** — the environment variables the Display
  tab writes (`ADAPTIVE_SYNC`, `CUSTOM_REFRESH_RATES`, `ENABLE_GAMESCOPE_HDR`, `GAMESCOPE_HDR_NITS`).
- **[Bazzite](https://github.com/ublue-os/bazzite)** / Universal Blue — the image this is built and tested on.
- **Decky Framerate**, **MangoHud** — frame-time logging that autotune and the benchmark read.

ClawDeck is not affiliated with MSI, Valve, Intel or Microsoft. "Claw" is an MSI trademark.
