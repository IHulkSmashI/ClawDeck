# MSI Claw 8 EX on Bazzite — migration guide (for people who have never used Linux)

Nothing on your Windows drive changes. Bazzite goes on a second drive and you pick which one boots.

## Before you start
- Back up anything on Windows you care about (we don't touch it, but back up anyway).
- Get a drive: an external NVMe enclosure (reference: Sabrent EC-SNVE, USB 3.2 Gen 2) with any NVMe SSD, 256 GB
  minimum, or a second internal SSD. Wipe it in Windows: `diskpart` → `list disk` → `select disk N` → `clean` →
  `convert gpt` (double-check N is the new drive; disk 0 is Windows).
- Windows: Settings → Power → turn **Fast Startup** off. BIOS (hold **Vol−** at power-on): **Fast Boot** off.
  **Secure Boot stays on.**
- A 16 GB USB stick and [Rufus](https://rufus.ie). Image: `bazzite-deck-stable-live-amd64.iso` from
  [bazzite.gg](https://bazzite.gg) ("deck" = handheld Gaming Mode; "amd64" just means x86-64). Rufus: pick the stick,
  the ISO, START; choose **DD image mode** if asked.

## Install
1. Power off. Hold **Vol−**, press Power, pick the USB stick.
2. Installer: Installation Destination → tick **only** the new drive → Storage: Automatic → Done → Begin.
3. Reboot. A blue **MOK Manager** screen appears: **Enroll MOK** → Continue → password `universalblue` → reboot.
   That is the key that lets Bazzite boot with Secure Boot on.
4. From now on, hold **Vol−** at power-on to choose Windows or Bazzite. With the external drive unplugged, Windows
   boots as before.

## First boot
1. Bazzite Portal: install **Decky Loader** (and **Proton Plus** if you want GE-Proton). MangoHud is already built
   in. Do **not** add Handheld Daemon: current Bazzite runs the Claw's controller with InputPlumber, and two
   controller services fight each other.
2. Desktop Mode (power menu → Switch to Desktop) → Konsole: `sudo rpm-ostree upgrade` → reboot.
3. Press the two MSI buttons: left opens Steam, right opens the quick menu. If they do nothing, post
   `tools/sysfs-survey.sh` output in an issue (it shows which controller service is running).
4. Power: Steam's own quick-menu performance slider (TDP) works on the Claw. ClawDeck sets the same MSI firmware
   limits, so whichever you change last wins.

Bazzite's Portal also offers OptiScaler and Lossless Scaling Decky plugins. They do the same job as ClawDeck's
injector; use one or the other for a given game, not both.

## ClawDeck
1. Decky (the plug icon in the quick menu) → settings gear → **Install plugin from zip** → `ClawDeck.zip` from
   Releases.
2. ClawDeck → Setup: every line should be green. Red lines say what's missing and how to fix it.
3. For each game you want autotune or the XMX injector on: start the game, open ClawDeck and tap
   **Add to launch options** (Power or Graphics tab). By hand it is
   `~/.local/share/clawdeck/clawdeck-launch.sh %command%` in the game's Properties → Launch options.
4. Power tab: pick Battery / Performance / Dock, set a target fps, turn Autotune on, play.

## Power profiles without the plugin (optional)
Desktop Mode → Konsole, from the unpacked plugin folder:
`sudo tools/power-profile.sh battery` (or `performance`, `dock`, `reset`; `status` shows what is set; add
`--dry-run` to see what it would change). Nothing survives a reboot.

## XeSS XMX and XeLL (optional, native XeSS games)
ClawDeck → Graphics → **Install driver** downloads [xess-xmx-linux](https://github.com/HHHHanasak1/xess-xmx-linux)
and runs its installer for you; log out of Game Mode and back in afterwards. Or by hand: unpack the release,
`./install.sh`, re-login. The Graphics tab shows whether the driver is active and has a **Probe driver** button. Run `tools/xell-probe.sh %command%` once on a game with
frame generation to confirm XeLL is in driver mode.

## Going back / getting help
- Windows: hold Vol− at power-on and pick it. Nothing was removed.
- An update broke something: Desktop Mode → `rpm-ostree rollback` → reboot.
- Bugs: ClawDeck → Setup → "Save a bug report to Desktop", attach the JSON to a GitHub issue.
- Sleep problems: `sudo ~/homebrew/plugins/ClawDeck/tools/suspend-cycle.sh 20` and attach `~/clawdeck-suspend.csv`.

## What you give up
Games with kernel anti-cheat (most competitive shooters) will not run. Peak frame rate on the open Mesa driver is a
few percent behind Intel's Windows driver in some titles. We post real numbers, not promises.
