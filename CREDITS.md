# Credits

ClawDeck orchestrates other people's work. Read, learned from, and called — not copied wholesale:

- **[Handheld Daemon](https://github.com/hhd-dev/hhd)** (LGPL-2.1) — MSI buttons, TDP through MSI firmware,
  fan curves, charge limit, controller and RGB. ClawDeck talks to its local API and reads its settings schema; the
  Claw model ids (MS-1T41/42/52/91/8K), the EX's 35/45 W limits and the fan curve points come from its source.
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
  XMX under Proton; ClawDeck installs, probes and switches its drivers.
- **[OptiScaler](https://github.com/optiscaler/OptiScaler)** — DLSS/FSR → XeSS and XeSS frame generation injection.
  ClawDeck installs it the way its `setup_linux.sh` does and reads its community
  [compatibility list](https://github.com/optiscaler/OptiScaler/wiki/Compatibility-List) for per-game notes and fixes.
- **[OptiPatcher](https://github.com/optiscaler/OptiPatcher)** — DLSS inputs on Intel and AMD without spoofing, in
  supported games.
- **[REFramework](https://github.com/praydog/REFramework)** — needed by OptiScaler in Capcom RE Engine games.
- **[OptiClick](https://github.com/onehoon/OptiClick)** — the Windows one-click OptiScaler installer whose approach
  (newest release, per-game modules and fixes, Intel XeFG at 3x) ClawDeck follows. No code is shared.
- **[lsfg-vk](https://lsfg-vk.dev)** — Lossless Scaling frame generation as a Vulkan layer on Linux.
- **[Lossless Scaling](https://store.steampowered.com/app/993090/Lossless_Scaling/)** by THS — the frame generation
  lsfg-vk runs. Users need their own copy; ClawDeck checks for it and never bundles it.
- **[gamescope-session](https://github.com/ChimeraOS/gamescope-session)** — the environment variables the Display
  tab writes (`ADAPTIVE_SYNC`, `CUSTOM_REFRESH_RATES`, `ENABLE_GAMESCOPE_HDR`, `GAMESCOPE_HDR_NITS`).
- **[Bazzite](https://github.com/ublue-os/bazzite)** / Universal Blue — the image this is built and tested on.
- **Decky Framerate** — an early reference for reading frame times in Game Mode.

- **[Moonlight](https://github.com/moonlight-stream/moonlight-qt)** (GPL-3.0) — the game streaming client.
- **[Moonlight with VRR](https://github.com/Nonary/moonlight-qt)** by Nonary — the Moonlight fork ClawDeck installs
  and keeps updated: VRR-aware frame pacing on Linux.
- **[Vibepollo](https://github.com/Nonary/Vibepollo)** by Nonary, built on **[Apollo](https://github.com/ClassicOldSong/Apollo)**
  and **[Sunshine](https://github.com/LizardByte/Sunshine)** — the streaming host on the PC.
- **[ClawTweaks](https://github.com/enterTheVoidCode/ClawTweaks)** and **[GoTweaks](https://github.com/corando98/GoTweaks)**
  — the Windows tools whose feature list (single TDP slider with optional boost, per-game AC/DC profiles, energy
  mode, charge limiter, fan presets, Lossless Scaling integration) shaped what ClawDeck offers on Linux. No code is
  shared.
- **[MangoHud](https://github.com/flightlessmango/MangoHud)** — frame-time logging for autotune and the benchmark.

ClawDeck checks every project above for new releases every day (`.github/workflows/upstream-watch.yml`) and updates
the ones it installs for you; see CONTRIBUTING.md → Keeping up with upstream.

ClawDeck is not affiliated with MSI, Valve, Intel or Microsoft. "Claw" is an MSI trademark.
