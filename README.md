<p align="center">
  <img src="assets/logo.png" alt="ClawDeck logo" width="200">
</p>

<h1 align="center">ClawDeck</h1>

<p align="center"><b>Power, battery, graphics and controller settings for the MSI Claw on Linux,<br>right in Steam's Quick Access menu.</b></p>

<p align="center">
  <a href="https://github.com/IHulkSmashI/ClawDeck/releases/latest"><img src="assets/download-button.png" alt="Download" width="260"></a>
</p>

<p align="center">
  <a href="https://github.com/IHulkSmashI/ClawDeck/releases"><img src="https://img.shields.io/github/v/release/IHulkSmashI/ClawDeck?include_prereleases&label=version&color=3fa7ff" alt="version"></a>
  <a href="https://github.com/IHulkSmashI/ClawDeck/releases"><img src="https://img.shields.io/github/downloads/IHulkSmashI/ClawDeck/total?label=downloads&color=5cc98a" alt="downloads"></a>
  <a href="https://github.com/IHulkSmashI/ClawDeck/commits/main"><img src="https://img.shields.io/github/last-commit/IHulkSmashI/ClawDeck?label=last%20update" alt="last update"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/IHulkSmashI/ClawDeck?color=f0b429" alt="license"></a>
  <a href="https://github.com/IHulkSmashI/ClawDeck/stargazers"><img src="https://img.shields.io/github/stars/IHulkSmashI/ClawDeck?style=flat&color=8a7bff" alt="stars"></a>
</p>

<p align="center">
  <img src="docs/screenshots/setup.png" alt="Setup tab" width="32%">
  <img src="docs/screenshots/power.png" alt="Power tab" width="32%">
  <img src="docs/screenshots/graphics.png" alt="Graphics tab" width="32%">
</p>
<p align="center">
  <img src="docs/screenshots/display.png" alt="Display tab" width="32%">
  <img src="docs/screenshots/controller.png" alt="Controller tab" width="32%">
  <img src="docs/screenshots/battery.png" alt="Battery and Fans tab" width="32%">
</p>

---

## What is ClawDeck?

If you've used ClawTweaks or MSI Center M on Windows, ClawDeck is the same idea for Linux. It's a free plugin for
[Decky Loader](https://decky.xyz) that adds one ClawDeck panel to the Quick Access menu (the right MSI button). From
there you can:

- switch between **Battery, Performance and Dock** power with one tap,
- let **Autotune** find the lowest power each game needs,
- run **XeSS on the Claw's XMX cores**, even in games that only ship DLSS or FSR,
- set a **battery charge limit** so your battery lasts for years,
- see at a glance **what's working and what isn't**, with a one-tap fix.

Made for the **MSI Claw 8 EX AI+** on [Bazzite](https://bazzite.gg). The Claw 8 AI+, 7 AI+ and A1M are supported
too.

> **Early test version.** ClawDeck is brand new and hasn't been tested on real hardware yet. Everything it changes is
> undone when you close the game or remove the plugin, and **Dry run** (Setup tab) lets you try it without
> changing anything.

---

## What each tab does

### Setup
| Feature | What it does |
|---|---|
| **Health check** | Checks your Claw, graphics driver, controller, battery and more. Anything missing shows up first, with a button to fix it. |
| **Bug report** | Saves one file to your Desktop with everything we need to help you. Attach it to a GitHub issue. |
| **Dry run** | ClawDeck shows what it *would* change, without changing anything. Good for trying things safely. |

### Power
| Feature | What it does | Good to know |
|---|---|---|
| **Profiles** | **Battery** (best frames per watt), **Performance** (plugged in) and **Dock** (full power) in one tap. Save one per game: it switches by itself when you plug in or unplug. | Goes back to normal when the game closes, unless you saved it. |
| **Autotune** | Set a target fps for a game. Autotune learns the lowest power that keeps it there, and uses that every time you play. | The first few minutes in a new game can stutter while it learns. |
| **TDP and clocks** | Pick the power limit (17 to 25 W is the sweet spot on battery). Under **Advanced**: CPU and GPU limits. | Turn off Steam's own TDP limit (Quick Access → Performance) so the two don't fight. |
| **Benchmark** | Records fps, 1% lows and watts for a game, so you can compare profiles with real numbers. | Under **Advanced**. |
| **Share profiles** | Export a game's settings as a file, or import someone else's. | Never overwrites a game you've already set up. |

### Graphics
| Feature | What it does | Good to know |
|---|---|---|
| **XeSS on XMX** | Installs the [xess-xmx-linux](https://github.com/HHHHanasak1/xess-xmx-linux) driver in one tap, so games with XeSS get full-quality upscaling and frame generation, as on Windows. | The first launch of each game takes a minute or two to prepare, once. |
| **XMX injector** | For games without XeSS. **OptiScaler** makes DLSS or FSR games use XeSS. **lsfg-vk** adds frame generation to any game. | Adds a little input delay. Online games with anti-cheat refuse it. |
| **Proton version** | Pick which Proton runs a game, such as GE-Proton. | Same as the game's Properties → Compatibility, just closer to hand. |
| **Add to launch options** | One tap adds ClawDeck to a game's launch options, which Autotune, the injector and HDR need. | Keeps anything you already had there. |

### Display
| Feature | What it does |
|---|---|
| **VRR** | The screen refreshes as each frame is ready, for smooth play between 40 and 120 fps without tearing. |
| **Refresh range** | Sets which refresh rates Steam's own refresh slider offers. |
| **HDR** | Turns on HDR for a TV or monitor on the dock. |

These settings apply after Game Mode restarts. Docked 4K at 120 Hz needs no setup: the Claw uses your TV's best mode.

### Controller
Gyro, button layout and stick lighting, from Handheld Daemon. On new Bazzite, InputPlumber runs the controller
instead, and this tab shows its status. ClawDeck works with whichever your Claw has and never runs both.

### Battery & Fans
| Feature | What it does | Good to know |
|---|---|---|
| **Charge limit** | Stops charging at 50, 60, 70, 80, 90 or 100 %. | 80 % roughly doubles battery lifespan. Use 50 to 60 % if your Claw lives on the dock. |
| **Fan curve** | Auto, Quiet, Balanced or Cool. | Needs Handheld Daemon. Without it, the Claw's own fan curve runs. |

---

## Install

You need an MSI Claw running [Bazzite](https://bazzite.gg) (or another Linux with
[Decky Loader](https://decky.xyz)). New to Linux? Our [step-by-step guide](docs/INSTALL-BAZZITE.md) goes from Windows
to Bazzite, with Windows staying on your Claw.

1. Install **Decky Loader**: on Bazzite, open the **Bazzite Portal** and tick Decky Loader.
2. Download **ClawDeck.zip** with the button at the top of this page.
3. Open **Quick Access → Decky (plug icon) → gear icon**, turn on **Developer mode**, then choose
   **Install plugin from ZIP** and pick ClawDeck.zip.
4. Open **ClawDeck → Setup**. Fix anything the health check lists.
5. Start a game, open **ClawDeck → Power**, pick a profile and a target fps, and tap **Add to launch options**.

To remove it: Decky → gear → ClawDeck → Uninstall. Power, display and fan settings go back to how they were, and
games you added ClawDeck to keep starting normally. The charge limit resets at the next reboot.

---

## Questions

**Will it touch Windows?** No. ClawDeck only runs inside Bazzite. Your Windows install is never changed.

**Is it safe?** Power changes are undone when the game closes, when you tap *Revert to defaults*, or when you
remove the plugin. The only setting it keeps is the charge limit, because that's its job. It never
overclocks: every value stays inside what the Claw's hardware allows.

**What about Steam's own Performance menu?** Steam's TDP slider and ClawDeck set the same limit, so turn Steam's
TDP limit off while ClawDeck manages power. Steam's frame limit is fine to use: set Autotune's target to the same
number.

**Bazzite also offers OptiScaler and Lossless Scaling plugins. Which should I use?** Either one, but only one per
game. ClawDeck spots the others and won't overwrite a game they've set up.

**Will online games work?** Games with kernel anti-cheat (most competitive shooters) don't run on Linux at all.
That's not something a plugin can change.

**Does it work on SteamOS?** It's built for Linux in general and should work wherever Decky Loader runs. It's tested
on Bazzite first.

**I have a different Claw.** The Claw 8 AI+, 7 AI+, A1M and A8 are in the list. If yours isn't recognised, tap the
fix button in Setup. It saves a hardware report you can post in an issue.

---

## Help and feedback

- **Something broken?** ClawDeck → Setup → **Save a bug report to Desktop**, then
  [open an issue](https://github.com/IHulkSmashI/ClawDeck/issues/new/choose) and attach the file.
- **Ideas?** [Open a feature request](https://github.com/IHulkSmashI/ClawDeck/issues/new/choose).
- **Want to help build it?** See [CONTRIBUTING.md](CONTRIBUTING.md).

## Thanks

ClawDeck stands on the work of Handheld Daemon, InputPlumber, PowerStation, PowerControl, SimpleDeckyTDP,
xess-xmx-linux, OptiScaler, lsfg-vk, Decky Loader and Bazzite. The full list is in [CREDITS.md](CREDITS.md).

## Support

ClawDeck is free and always will be. If it saves you battery or time, you can buy the project a coffee at
[ko-fi.com/gamervault](https://ko-fi.com/gamervault).

## License

[MIT](LICENSE). ClawDeck is a community project and isn't affiliated with MSI, Valve, Intel or Microsoft. "Claw" is a
trademark of MSI.
