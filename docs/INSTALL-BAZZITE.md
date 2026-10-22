# From Windows to Bazzite on the MSI Claw

A step-by-step guide for people who have never used Linux. Bazzite goes on a second drive, Windows stays on the
first, and you choose which one starts. Nothing on your Windows drive changes.

## What you need

- **A second drive** for Bazzite, 256 GB or more: an NVMe SSD in a USB enclosure (a fast USB 3.2 Gen 2 one, such as
  the Sabrent EC-SNVE), or a second internal SSD.
- **A USB stick**, 16 GB or more, for the installer.
- **[Rufus](https://rufus.ie)** on Windows, to put the installer on the stick.
- A backup of anything on Windows you care about. Bazzite won't touch it, but a backup is always a good idea.

## Get ready (in Windows)

1. **Wipe the new drive.** Open a Command Prompt as administrator and type `diskpart`, then `list disk`. Find the new
   drive by its size, then `select disk N` (N is its number), `clean`, `convert gpt`. Double-check the number: disk 0
   is Windows.
2. **Turn off Fast Startup:** Control Panel → Power Options → *Choose what the power buttons do* → untick
   *Turn on fast startup*.
3. **Download Bazzite** from [bazzite.gg](https://bazzite.gg): pick **Handheld/HTPC** and your Claw. You get one
   `.iso` file.
4. **Make the USB stick:** open Rufus, pick the stick and the `.iso`, press START. If Rufus asks, choose
   **DD image mode**.
5. **In the Claw's BIOS** ([MSI's guide to opening it](https://us.msi.com/faq/faq-10336)): turn **Fast Boot** off.
   Leave **Secure Boot** on.

## Install

1. Plug in the USB stick and the new drive, restart, and start from the USB stick using the Claw's boot menu (MSI's
   guide above shows the buttons).
2. In the installer: *Installation Destination* → tick **only the new drive** → *Storage: Automatic* → Done →
   Begin Installation.
3. Restart. A blue **MOK Manager** screen appears once: choose **Enroll MOK** → Continue → type the password
   `universalblue` → restart. This lets Bazzite start with Secure Boot on.
4. From now on, use the boot menu to choose Windows or Bazzite. With the external drive unplugged, Windows starts as
   before.

## First start

1. **Bazzite Portal** opens on first start. Tick **Decky Loader**.
   Don't add **Handheld Daemon**: current Bazzite already runs the Claw's controller with InputPlumber, and two
   controller programs fight each other.
2. **Update:** switch to Desktop Mode (power menu → Switch to Desktop), open **Konsole**, type `ujust update`, then
   restart.
3. **Check the MSI buttons:** the left one opens Steam, the right one opens Quick Access. If they do nothing,
   [open an issue](https://github.com/IHulkSmashI/ClawDeck/issues/new/choose).

## Add ClawDeck

1. Download **ClawDeck.zip** from [Releases](https://github.com/IHulkSmashI/ClawDeck/releases).
2. Quick Access → **Decky** (plug icon) → gear icon → turn on **Developer mode**. Open the new **Developer** section,
   choose **Install plugin from ZIP file** and pick ClawDeck.zip.
3. Open **ClawDeck**. **Fixes** shows what the hardware gets wrong (VRR range, face buttons) with an Install button
   each; **Setup** installs the XeSS XMX driver and runs the self-test.
4. For each game you want Autotune, XeSS XMX or XeLL in: start the game, open ClawDeck and tap
   **Add to launch options** (Per-game).
5. **Power**: pick Battery, Balanced, Performance or Dock, turn Autotune on, set a target fps, and play.

Power: leave Steam's own TDP slider (Quick Access → Performance) off while ClawDeck manages power. On the Claw 8 EX it
may do nothing at all; ClawDeck sets the limit directly.

For games that only ship DLSS or FSR, use Bazzite's own **Decky Framegen** (OptiScaler) from the Portal; for games
with no upscaler, **decky-lsfg-vk**. ClawDeck's driver then runs their XeSS output on the XMX cores.

## XeSS on the XMX cores (optional)

ClawDeck → **Setup** → **Install XeSS XMX** sets up [xess-xmx-linux](https://github.com/HHHHanasak1/xess-xmx-linux)
(our fork's release when it has one, for XeLL driver mode; otherwise upstream's). Afterwards, log out and in once.
Setup then shows whether the driver loads, and the Per-game panel shows what XeSS and XeLL do in the running game.

## Going back and getting help

- **Back to Windows:** choose it in the boot menu. Nothing was removed.
- **A Bazzite update broke something:** Desktop Mode → Konsole → `rpm-ostree rollback` → restart. You're back on the
  version from before the update.
- **A ClawDeck bug:** ClawDeck → Setup → **Save a bug report to Desktop**, then attach the file to a
  [GitHub issue](https://github.com/IHulkSmashI/ClawDeck/issues/new/choose).

## What you give up

Games with kernel-level anti-cheat (most competitive shooters) don't run on Linux;
[Are We Anti-Cheat Yet?](https://areweanticheatyet.com) lists them. In some games, the frame rate is a few percent
lower than with Intel's Windows driver.

## For testers: power profiles from a terminal

Without the plugin, from the unpacked ClawDeck folder in Konsole: `sudo tools/power-profile.sh battery` (or
`performance`, `dock`, `reset`; `status` shows what's set; add `--dry-run` to only show what it would change).
Nothing survives a restart. To test sleep, run `sudo ~/homebrew/plugins/ClawDeck/tools/suspend-cycle.sh 20` and
attach `~/clawdeck-suspend.csv` to an issue.
