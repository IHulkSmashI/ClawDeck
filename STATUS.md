# ClawDeck status

Updated 2026-11-04 (0.2: xess-xmx-linux 20261104 options, ClawDeck's own pinned OptiScaler per game, battery-only Autotune). The plugin does only what Bazzite and Steam's Quick Access do not already do, in
four panels: **Power**, **Per-game**, **Fixes**, **Setup**. Everything below passes the backend tests against fake
sysfs trees, and the UI typechecks, builds into the Decky zip and renders in the browser preview.

## Priorities (from the project brief; nothing lower starts until the line above works on the maintainer's Claw)

| # | What | Code | Needs the Claw |
|---|---|---|---|
| 1 | **XeSS XMX + low latency** (upstream 20261104) | done: upstream install (the fork and its XeLL driver mode are retired: upstream shipped low latency in #41); Per-game: XMX on/off, frame generation 2–8x, low latency, latest XeSS libraries, FSR games on XeSS, each with a read-back line | see Needs the maintainer → 0.2 |
| 2 | **Controller and VRR fixes** | done: Fixes panel, install / remove buttons (no toggles) | install the VRR fix, restart, `edid-decode` shows 30–120 and the 120 Hz mode is still there |
| 3 | **ClawDeck's own OptiScaler per game** | done 2026-11-04: `optiscaler.py` installs the OptiScaler + OptiPatcher pinned in `assets/versions.json` (`optiscaler`, sha256 checked) into a game's folder, with the game's settings and the Intel presets (XeSS on XMX, FSR 4 on XMX, XeSS frame generation), and takes it out again exactly (manifest + backups). Decky Framegen is compatibility only: a game it patched is detected and left alone with a warning | see Needs the maintainer → 0.2 |
| 4 | **Power + autotune** | done: six profiles + 8–35 W TDP slider (greyed out where Autotune sets the limit; Autotune's max TDP is the TDP setting until moved), read-back line, debounced; autotune lowers the TDP while the frames hold and gives it back at the first loss; Advanced expander (§19) | see Needs the maintainer → Power |

## Done (code, tests)

| Module | Backend | Panel | Notes |
|---|---|---|---|
| Hardware table + discovery | `hw.py` | Setup | one row per Claw; EX = MS-1T91 (board_name first) |
| Revertible sysfs | `sysfs.py` | every write | `last_error()` feeds the red rows |
| Health check + one line | `health.py` | Fixes | fixes: install XeSS, face buttons, hardware survey |
| Power profiles | `power.py` | Power | RAPL-MMIO on the EX (the MSI attributes are ignored there), HHD when it runs |
| Autotune | `governor.py`, `autotune.py` | Power | one governor for a target fps (what the max TDP gives where the target is out of reach): a step down watched on every frame and taken back at the first loss, the full TDP at once when frames are lost; frames from mangoapp's log, rendered frames with XeLL; PL1, PL2 = PL1 + 2 W and an 8 s PL1 window while it governs, no CPU/GPU levers; battery only unless "Autotune when plugged in" |
| XeSS XMX | `xess.py`, `launchenv.py`, `tools/clawdeck-launch.sh` | Setup, Per-game | upstream release; `xmx` / `fg` / `low_latency` / `xess_latest` / `fsr` per game; shim log read per launch |
| ClawDeck's OptiScaler | `optiscaler.py`, `launchenv.py` (`opti`, `opti_xefg`), `assets/versions.json` (`optiscaler`) | Setup, Per-game | pinned release + OptiPatcher, sha256 checked; patch / unpatch with a manifest and backups; never touches OptiScaler it didn't install (`owner()`) |
| Decky Framegen compatibility | `framegen.py` (state), `optiscaler.owner()`, `steam.ts` (fgmod in launch options) | Setup, Per-game | detection only: a game Decky Framegen patched (fgmod in its launch options, or its files) shows a warning and "Stop using Decky Framegen here"; ClawDeck doesn't install or offer Decky Framegen |
| VRR range fix | `edid.py` | Fixes | the panel list's validated panels only; `drm.edid_firmware` + initramfs-etc |
| Face buttons (EX) | `buttons.py` | Fixes | InputPlumber override, `claw8ex` map |
| Self-test | `tools/selftest.py`, `main.self_test` | Setup | PASS/FAIL per control; refuses while a game runs |
| System change check | `changes.py` | toast at start | kernel / Mesa / controller / XeSS / Decky changes re-run the health check |
| Browser preview | `preview/` (`pnpm preview`) | all four | the real panel code with example data |
| Diagnostics log | `diaglog.py`, `main._diag_loop` | Setup | off by default; while on: 1 row/s (power, limits, °C, clocks, battery, fans, Autotune, busiest processes) and the controller's mode reports; 30 days, 200 MB; the export adds the last 10 minutes |

Removed in the scope reset (2026-10-21): VRR on/off, refresh rate, HDR, display save, fan presets, GPU clock
window, CPU ceiling, EPP, turbo, charge limit, sleep readouts, dock readouts, benchmark, Proton picker, streaming
(Moonlight), profile import/export, the Controller and Battery pages, and (2026-10-22, Bazzite does them) OptiScaler
and Lossless Scaling per game with their updater. Their modules are gone too (`benchmark`, `controller`, `display`,
`fans`, `proton`, `sleep`, `streaming`, `injector`, `gamescan`, `compat`, `updates`); `battery.py` keeps `plugged()`.

## Tested on real hardware

2026-10-21, Claw 8 EX on Bazzite deck 44.20261012.1: plugin loads in Decky 3.3.0, hardware detected, health check and
power readouts run. `tools/selftest.py` 12/12 PASS on the device (TDP via RAPL measured 15 W under load at a 12 W
setting and 37 W after revert; CPU/GPU ceilings; charge threshold; face buttons; full revert). Blockers found and
fixed the same day: backend not root (§13), MSI firmware TDP attributes ignored (§12), no CA store (§15).
The four-panel UI has not been on the device yet.

2026-11-05, Claw 8 EX, Bazzite, Proton 11.0: CONTROL Resonant at 3x on OptiScaler 0.9.4 (FSR 4.1.2 on XMX, XeFG,
about 146 fps shown) once DXVK showed the Intel GPU (`DXVK_CONFIG=dxgi.customVendorId = 8086`; Proton 11.0 hides it
from XeSS games and XeSS-FG then caps at 2x). Autotune reads CONTROL's base frame rate; Quick Access menus smooth;
sleep/wake several times fine.

## Needs the maintainer

**Autotune runs on a new governor (2026-11-15):** `governor.py` replaces both old controllers (measure / balance / lower
/ hold, and the target-fps governor). It watches every frame (mangoapp's log is now the first frame source for every
game), tries a lower TDP for 2.5 s and takes it back at the first loss, and writes the full limit at once when a heavier
scene loses frames. It owns PL1, PL2 (PL1 + 2 W, also over a PL2 set by hand) and an 8 s PL1 window while it governs;
it no longer touches the CPU/GPU levers. A game with no history starts mid-range under your limit. Tested on frames
written by hand and on games built from Claw 8 EX recordings.
Check on the EX, Autotune on:
- a light game on a frame limit (60 fps at about 9 W): the TDP is at the floor within half a minute, 60 fps throughout;
- a heavy game with no target set (the screen's highest refresh rate): "using N W of your M W" settles
  within a few watts and stops moving (after the first minutes: under one change a minute); frames stay within 0.5 %
  (or the game's own wobble) of what the full TDP gives;
- a target the game can't reach at the full TDP ("60 out of reach"): the TDP stays at the full TDP, or a few
  watts under it where those give the same frames (within the game's own wobble);
- walk from a light place into a heavy one: the full TDP is back within about a second;
- **the Steam menu over a game:** open Quick Access for half a minute and close it. mangoapp follows the focused
  window; what Autotune reads meanwhile is not known. The TDP must not end up lower than before, or must be back within
  a second of closing the menu;
- an emulator from ES-DE: `autotune.csv` rows end in four figures; in xemu "Autotune stays out of xemu";
- `tools/soak.py` over two hours with a game: the backend under 0.5 % CPU (it looks twice a second, and ten times a
  second while it watches a change of its own: 1 to 3 s, up to 10 s in a look for slow frames);
- cover the vents until the guard steps: the TDP never goes over the guard's step, and comes back when it lifts.
- on the charger, "Autotune when plugged in" switched in a running game: off, the TDP setting is the limit at once,
  under the guard's step, and a CPU boost set off stays off; on, the TDP goes down as in a game started on the charger.
- unplug or plug in during a game that has no level on the other power source (also while the Claw sleeps): the TDP
  starts where the game held, not mid-range; the firmware's own hold of 2 to 4 s at about 10 W after an unplug stays;
- the charger in or out, and before Steam reports it the TDP slider, a profile or PL2 moved: the change goes to the
  power source the panel shows;
- open the panel at Steam's shader dialog, before the game's process: Autotune starts with the game process, not before.
- a 30 fps cutscene or video in a 60 fps game, 20 s and a minute: the TDP does not go under what it was before the
  video, and the game is at its frame rate within a second of the video's end. A video of over 2 minutes: the TDP may
  go down in it; the game is at its frame rate within about 3 s of its end;
- a map or inventory that renders much faster than the game, for some seconds: the TDP is the same before and after;
- loading screens: after several of them the TDP is where it was (in simulated games each one lowered what Autotune took
  for the game's frame rate, and the TDP with it; fixed);
- a game whose frames mangoapp does not log (only its own MangoHud log): the TDP still goes down;
- frames stop for half a minute in a game (the status line says so): your limit is on the chip, and Autotune takes the
  game again when frames come;
- a game with XeSS frame generation: the status says "rendered, 2x shown" and the TDP follows the rendered frames
  (in the first version every loss was missed with XeLL; fixed, tested with made-up XeLL lines only);
- known from simulated games, to watch for: a game with very uneven frame rates (hitch runs every 2 s, or 9 % frame-time
  spread without a frame limit) may be held a watt or two under where it loses 5 % for up to a minute;
- if Decky kills the backend while a game runs, the limit Autotune held, PL2 = PL1 + 2 W, the 8 s PL1 window and the
  stopped thermald stay until the next plugin start reverts them (an old mechanism; the 8 s window is new in it).

**Autotune's log has the frame times (2026-11-15):** for a game read from mangoapp's per-frame log (no MangoHud log of
its own), each row of `autotune.csv` now also has the number of frames in that second, their median and 95th
percentile frame time and the share of slow frames (more than 10 % over the pace of the 60 frames before): a limit
that is just too low shows as a few slow frames while the frame rate still reads full (Midnight Club: Los Angeles on
RPCS3 at 15 W: 29.7 of 30 fps, 6.4 % slow frames). Nothing Autotune decides on changed, and a game with its own
MangoHud log (one row a second) leaves the four columns empty. An `autotune.csv` from an earlier build is kept as
`autotune.csv.1`. Check on the EX: Autotune on, a game from ES-DE for a minute: the newest rows of
`~/homebrew/logs/ClawDeck/autotune.csv` end in four figures (about 60, 16.7, 17, 0 for a 60 fps game), and
`autotune.csv.1` holds the earlier rows.

**Autotune reads frames from mangoapp (2026-11-14):** a game without its own MangoHud log (no launch wrapper: xemu's
Flatpak, a game started another way) gave Autotune no frame rate ("no frame rate after 30 s"). Now it follows
mangoapp's per-frame log (Steam's overlay, fed every frame of the focused app): a running one is only read; else
Autotune turns it on through mangoapp's control socket and off at the game's end, and asks again after 20 s without a
row (Steam's overlay-level change stops it). Check on the EX: Autotune on, a game without the wrapper: the Power line
shows fps within a minute; `~/mangoapp_*.csv` appears and stops growing after the game ends.

**A new Autotune target leaves xemu alone (2026-11-15):** a new target, the target switched off or the Auto FPS switch
ended every pause in the running game, also the one for xemu: Autotune governed the game again on xemu's 60 presented
fps and took the TDP from a 30 W pick to 8 W within two minutes. Now a game Autotune stays out of stays as it is, and
the new target counts from the next game. After a manual change a new target still hands control back, as before.
Check on the EX: Autotune on, an Xbox game from ES-DE until the Power line says "Autotune stays out of xemu", then
pick another target: the line and the TDP (the pick) stay.

**Autotune stays out of xemu from an AppImage too (2026-11-15):** xemu's own AppImage starts the program through
`AppRun`, a link to `usr/bin/xemu`, and the process is then called "AppRun". The check for xemu went by the process
name, so Autotune governed the game on xemu's 60 shown frames (Project Gotham Racing 2 with a 60 fps target: the TDP
went down to 8 W). Now the check also reads the file a process runs (`/proc/<pid>/exe`). The Flatpak (EmuDeck's) was
not affected. Check on the EX: Autotune on, an Xbox game started from xemu's AppImage: within 30 s the Power line says
"Autotune stays out of xemu".

**Autotune stays out of xemu (2026-11-14):** xemu presents 60 fps whatever the emulated game does (Project Gotham
Racing 2 at 16 W: 16-18 game fps, 60 shown), and under its own draw (~20 W) the game slows. Every 3 s of a game
Autotune looks for an `xemu` process; if one runs, the pick goes back on the chip and Autotune stays out. Check on the
EX: Autotune on, start an Xbox game from ES-DE: within 30 s the Power line says "Autotune stays out of xemu" and the
TDP is the pick.

**Temperature guard gives the TDP back while Autotune is out (2026-11-15):** a paused Autotune (xemu, no frame rate, a
manual change, Autotune switched off in the game) never raises the TDP, and the guard's steps back up only moved
Autotune's cap. With a target, the chip stayed at the guard's step until the game ended while the log said "back at
your 30 W". Now the guard's steps back up are written for a paused Autotune, 2 W every 30 s up to the pick, as for a
game without Autotune. Check on the EX: in a game, switch Autotune off, heat the package until the log says "thermal:
stepped to N W", let it cool: `tdp_w` in `~/homebrew/logs/ClawDeck/thermal.csv` goes back up to the pick in 2 W steps.

**Temperature guard: CPU clock for a hot spot, watts from the draw (2026-11-14):** in xemu two busy threads held the
package at 95-97 °C at ~20 W under a 30 W limit; the guard stepped the TDP 2 W every 10 s from 30 W, nothing happened
until it was under 20 W, PL1's window hid the next steps, and it reached 10 W in 90 s (the game at a third of its speed).
Now a hot spot under the limit gets the CPU's top clock (-10 % per check, not under 60 %), the TDP steps from what the
package draws, and the next watts step waits until the package came down to the last one; the clock comes back first.
Check on the EX: an xemu game (or two busy threads on cpu0-1) with Autotune off: over 95 °C the Power line says
"thermal: CPU clock 90 %" and the TDP stays at the pick; the game keeps its speed.

**Diagnostics log (2026-11-14):** Setup → Diagnostics log (off by default) writes one row a second to
`~/homebrew/logs/ClawDeck/diag/<date>.csv` and the controller's reports to `<date>-controller.txt`; Export log adds
the last 10 minutes. Check on the EX: switch it on, play a game for a few minutes, switch it off: the CSV has rows with
package W, GPU MHz and the game's appid; the controller file has a "mode xinput" line; the rows stop within a second;
`python3 tools/soak.py --hours 0.5` with it on stays inside the budget in CONTRIBUTING.md.

**Quick Access tabs (2026-11-14, #171):** the four folding sections are now Steam's own Tabs (Power, Game, Fixes,
Setup), sized as unifideck does in Quick Access. Released without an EX run on the maintainer's word. Check on the EX:
LB / RB switch tabs (also with the focus inside a tab), the Power tab scrolls to its end, a dropdown's menu closing
keeps the tab, and the first-run steps above the tabs still fit.

**Gyro: firmware in the initramfs and a udev rule (2026-11-14, #180):** on Bazzite testing (kernel 7.3) the gyro never
came up: intel_ish_ipc probes inside the initramfs before /etc/firmware exists and loads Intel's generic image ("ISH
loader: cmd 2 failed 10"), and nothing loaded the HID-BPF program (the distro rule only loads hwdb-listed ones). Install
now tracks the firmware into the initramfs and runs `udev-hid-bpf install --force` from the plugin's copy; a gyro from an
earlier beta gets both at plugin start; Remove untracks and deletes the rule. Check on the EX: after updating, the log
says "gyro repaired"; reboot, then cold boot: `ls /sys/bus/iio/devices/*/name` shows gyro_3d, Fixes says Gyro on.

**MangoHud's frame-time log starts again (2026-11-12):** the wrapper hid MangoHud with `no_display`, and MangoHud up to
0.8.4 never starts `autostart_log` then, so autotune had no frame rate in any game without XeLL. Now an empty layout at
zero alpha hides it. Check on the EX: autotune on, start a game without XeSS (Enshrouded): nothing is drawn, a CSV
appears in `~/homebrew/data/ClawDeck/mangohud/`, and the status line shows fps.

**Autotune reads only the running game's MangoHud log (2026-11-12):** it took the newest CSV in the log folder and
read it from the start, so after a plugin restart a game's first reading was another game's old log (Order of the
Sinking Star got Enshrouded's fps), and it looked for a new log only every 30 s. Now only a log written since the game
started counts, looked for every second until it appears. Check on the EX: autotune on, play one game, then start
another and restart the plugin: the status line shows the second game's fps, never the first one's.

**A paused Autotune gives its changes back (2026-11-15):** switching Autotune off in a game, or one manual change under
Advanced, only set a flag: Autotune's own TDP, CPU ceiling, boost and GPU floor stayed on the chip until the game
ended (a 60 fps target met: 9 W of a 30 W pick, also after the switch). Now the pick goes back at once (TDP, PL2, the
firmware's window, the levers Autotune moved); the change made by hand is written after that and stays, and what
Autotune had found is still saved for the game. Check on the EX: Autotune on with a target, a light game until the
Power line shows fewer watts than the limit, then switch Autotune off: the read-back line shows the limit within a
second (`constraint_0_power_limit_uw` = the pick, PL2 2 W over it).

**Autotune without a frame rate gives your limit back (2026-11-12):** a game whose frame rate autotune can't read
(no XeSS/XeLL, no MangoHud log) used to keep autotune's starting point after it gave up, so a target's mid-range start
(22 W of a 35 W pick) stayed for the whole game: Enshrouded ran at 30-40 fps instead of 60 until autotune was switched
off. Now the TDP, CPU ceiling, boost, GPU floor and GPU efficiency mode go back to what your pick set, and frames that
come later (Order of the Sinking Star shows its first one 54 s after launch) hand the game back to autotune.
Check on the EX: Custom 35 W, autotune on with a target, start a Vulkan game without XeSS (Enshrouded). After 30 s,
the status line says it stays out, the log line ends "(35 W, your limit)", and
`cat /sys/class/powercap/intel-rapl-mmio:0/constraint_0_power_limit_uw` reads 35000000.

**XeSS XMX package 20261115 (2026-11-15):** pinned without waiting (maintainer's word for HHHHanasak1's releases).
Low latency on the FSR routes lets the GPU's queue decide the wait (upstream: CONTROL Resonant 3x on an Arc B390 from
52.6 to 48.4 ms); a game's own XeSS-FG 30 % lower latency (Cyberpunk 2077 3x: 45.1 to 31.7 ms). Stock mode is now
also install.sh's default on Xe2 (Claw 8 AI+), and the session file adds `VK_EXT_descriptor_heap` to
`VKD3D_DISABLE_EXTENSIONS` (ClawDeck carries the session file's values). Nothing ClawDeck reads changed. Check on the
EX: Setup → Update XeSS XMX, then CONTROL Resonant with FSR 4.1.2 + XeFG 3x: picture right, "XMX cores used in the
last launch", the XeLL line on the Game page. On an AI+ if one is at hand: the same, now in stock mode.

**XeSS XMX package 20261113 (2026-11-14):** pinned without waiting (maintainer's word for HHHHanasak1's releases).
One low-latency path for XeSS frame generation: upstream reports CONTROL Resonant 3x on an Arc B390 (the EX's chip)
from 35.5 ms to about 23 ms. The archive is single-nested again. Check on the EX: Setup → Update XeSS XMX, then
CONTROL Resonant with FSR 4.1.2 + XeFG 3x: picture right, "XMX cores used in the last launch", and the XeLL line on
the Game page. The 20261111 checks below still apply (stock mode on Xe3).

**XeSS XMX package 20261111 (2026-11-10):** pinned without waiting (maintainer's word for HHHHanasak1's releases).
New on the EX (Xe3): install.sh's default is "stock mode", the package's unpatched Mesa 26.3 plus its Vulkan layer,
with five more session variables that ClawDeck carries per game. Check on the EX: Setup → Update XeSS XMX, then CONTROL
shows "XMX cores used in the last launch"; if not, the launch log's "XMX driver on" line and xmx.env should list
MESA=stock-26.3.0 and VK_LOADER_LAYERS_ENABLE=VK_LAYER_XESS_xmx.

**XeSS XMX package 20261107 (2026-11-09, #51):** the pin moved from 20261104 (driver, shim and hook hardening,
background compile of missing kernels, Mesa 26.3 patch 0006, session check from ~/.config/xess-xmx). The parts
ClawDeck reads (50-xess-xmx.conf, the hook mark, the shim path) are unchanged. Released in beta 13 without waiting (maintainer, 2026-11-09). Check on the EX: Setup → Update XeSS
XMX, then CONTROL (OptiScaler XeSS 2) and one game with its own XeSS show "XMX cores used in the last launch".

**REFramework fork release 18 (2026-11-09, #51):** the pin moved from release 15 (three small upstream syncs: Lua
JSON / LooseFileLoader fixes, HookManager thread safety, ARM64EC). Released in beta 13 without waiting. Check on the EX: Onimusha →
Game page → Install again (puts release 18 in), start it, REFramework's menu opens with Delete, play a minute.

**XeSS 2 through OptiScaler shows the Intel GPU (2026-11-09, #150):** Proton 11.0's DXVK hid the Intel GPU from
every OptiScaler XeSS game (OptiScaler loads libxess.dll before the game's DXGI factory), so XeSS ran DP4a; now every
`opti: xess` game gets `DXVK_CONFIG=dxgi.customVendorId = 8086`, and every launch file is rewritten at start. Check on
the EX: a DLSS game with XeSS 2 (no frame generation) shows "XMX cores used in the last launch", the game still offers
DLSS (OptiScaler's Nvidia spoof is above DXVK), and one game from the `Spoofing_Dxgi=false` list still starts.

**Debug logs for the next launch (2026-11-09, #150):** Per-game button arms one launch: the wrapper turns on
PROTON_LOG (into the runtime folder), XESS_LOG/IGDEXT_TRACE, ANV_CM_DEBUG and ClawDeck's OptiScaler's log
(LogToFile/LogLevel in the game's OptiScaler.ini, put back at the next start); the bug report carries the filtered
lines. Run once on the EX: arm, play CONTROL a minute, quit, Export log: `games[].debug` has the Proton log lines,
the shim trace and OptiScaler's lines; start again and check OptiScaler.ini is back to `LogToFile=auto`.

**Claw 8 EX gyro (beta 9, 2026-11-08):** Kisenir ran ClawDeck's one-button install on his EX: Install works at once,
Remove takes it out (no cold boot needed for him, but he already had MSI's firmware loaded). Still worth a run on the
maintainer's EX from stock (firmware never loaded before): Install -> cold boot -> gyro in Steam -> Remove.

**Autotune target fps + faster recovery (2026-11-08):** not run on the Claw yet. Target mode (Power → Autotune target,
Auto FPS) follows DeckyTDPTuner's governor; most-frames mode now raises at once when a heavy scene drops clearly under
its line (RE4 report: Autotune lowered the TDP in a calm scene, then took minutes to come back). Check: RE4 or CONTROL,
target 40 and 60 with Steam's frame limit at the same number: watts settle within a few minutes, a heavy fight gets
watts back within ~5 s, no saw-tooth; with frame generation (2x) the target counts rendered frames. An out-of-reach
target (60 at 12 W) drops to the closest after a minute with Auto FPS on; off, it stays at the full TDP.

**MSI BIOS check (2026-11-07):** Setup's checks show "MSI BIOS up to date" or "MSI has BIOS E1T91IMS.10E
(2026-10-30); this Claw runs E1T91IMS.10D …" with MSI's support link. The sandbox can't reach msi.com, so check the
request on the Claw once: `python3 -c "import sys;sys.path.insert(0,'$HOME/homebrew/plugins/ClawDeck/py_modules');
from clawdeck import msibios;print(msibios.refresh('MS-1T91'))"` shows latest E1T91IMS.10E (or newer). If MSI
refuses a non-browser client, it says so in `error`.

**VRR fix on SteamOS (boot setting, 2026-11-08) / CachyOS (beta 5):** a tester on SteamOS 3.9.5 showed the live debugfs
EDID override does nothing on Intel eDP (i915/xe cache the panel EDID), so beta 6 stopped offering it. Now SteamOS gets
the boot method, built from SteamOS's own sources (steamos-customizations: Holo's update-grub, the atomic-update keep
list) and the tester's output (/etc/default/grub present, /usr/bin/update-grub, /etc/atomic-update.conf.d): one marked
line in /etc/default/grub + update-grub, and a keep-list drop-in /etc/atomic-update.conf.d/clawdeck-vrr.conf naming only
/etc/firmware/edid/clawdeck-*.bin (never /etc/default/grub, so Valve's changes to it are never shadowed). After a SteamOS
update ClawDeck adds its line again at start; one restart. Not offered when xe/i915 is inside the running initramfs
(lsinitcpio), since the driver would start before /etc. A rollback to the other SteamOS slot after an update can carry
the fix too (both slots' grub.cfg get it); Remove there takes it out. If grub.cfg comes out without the argument, Install
fails with an error; started with the argument but the driver still at 48 Hz shows "didn't take". Check on SteamOS: Fixes → Install VRR fix → restart → 30–120 Hz;
after the next SteamOS update, start ClawDeck, restart once, 30–120 Hz again. CachyOS: Limine only when
/etc/default/limine sets KERNEL_CMDLINE[default] itself (and no limine-entry-tool.d override), systemd-boot only with
OVERWRITE_EXISTING=yes, GRUB only without grub.d; regenerate via systemd-run with no time limit; undo removes only marker
lines. Check on CachyOS: the range shows 30–120 Hz after a restart. Ask SteamOS/CachyOS testers for their Export log.

**XMX on any Proton + Update button (2026-11-07):**
- ~~GE-Proton with stock copies~~ **tested 2026-11-07 on the Claw 8 EX**: CONTROL Resonant (FSR 4.1.2 + XeFG 2x) on
  GE-Proton11-12 with Intel's stock igdext64.dll in the Proton and the prefix, watcher stopped: black screen before the
  fix; with it the launch log says "XMX shim put into this Proton's own igdext64.dll", XMX cores used 4 s after start,
  picture right.
- A prefix without the shim (new game) on Proton 11.0: the log shows the `Z:` path and the XMX check is green. If it is
  not, the Z: path doesn't work and must come out.
- Setup -> Updates on a build with build.json: Update hands the zip to Decky, Decky asks, ClawDeck reloads, and the XMX
  driver, OptiScaler in games and settings are all still there.

**Launch log + FSR 4.1.2 held back (2026-11-07, PR #126):** start CONTROL through ClawDeck, then
`ls -t ~/homebrew/data/ClawDeck/launch/*.log | head -1 | xargs cat`: lines for the Proton, the exe, the settings, "XeSS XMX driver for
this game" and "proton scope: XMX driver on". Setup → Export log: the report has a `games` section. A game whose
last launch ran without XMX shows no FSR 4.1.2 in Per-game.

**v0.2.0-beta.2 released (2026-11-06):** tested on the Claw 8 EX before release: FSR 4.1.2 files filled
`~/fsr4-xmx` from the plugin (no download, sha256 identical), device line and capabilities right. The old
`fsr4-xmx-4.1.2` release and tag are deleted, so beta 1 can no longer download the FSR files: beta 1 users update to
beta 2.

**Device-aware ClawDeck + ONEXPLAYER 3 beta (2026-11-06), needs testers on hardware we don't own:**
- Claw 8 EX: first run still shows both steps; Fan, Charge limit, Fixes unchanged; Setup shows the device.
- Claw A1M / A8 (no XMX): no XMX driver step, no FSR 4.1.2 offered; XeSS 2 still installs.
- ONEXPLAYER 3 (HHHHanasak1 / r/Bazzite owners): matched as "ONEXPLAYER 3 (beta)"; TDP 5-40 W through RAPL; Fan
  Automatic / Cooling 70 % / Full (needs the oxpec driver: hwmon `oxp_ec`); charge limit; Fixes → Screen brightness
  moves the picture in Game Mode; no face-button or VRR fix offered. Their Setup → Export log carries a `device`
  section (DMI, hwmon, backlight, batteries) to wire the rest from.

- **OptiScaler's Compatibility List per game (2026-11-05):** the third live list. Per-game shows "Loads as winmm.dll
  or dbghelp.dll (OptiScaler's entry for this game)" and up to two "Known: …" lines where the wiki has a detailed
  entry; Install puts OptiScaler in under that name. Check with a game whose entry says winmm.dll (Enshrouded, DOOM
  Eternal): the game's folder gets `winmm.dll`, the launch env `winmm=n,b`, and OptiScaler's overlay shows in game.

- **Live per-game lists and FSR 4.1.2 first (2026-11-05):** at start ClawDeck fetches its games.json from main and
  the public supported-games list, every 12 h, into
  `~/.local/share/clawdeck/lists`. Check on the Claw: that folder has `games.json` and `supported-games.md`;
  Per-game for CONTROL shows "In the game: DLSS, DLSS-G, Reflex" (or what it ships), "On the public supported-games list",
  and with nitec's files in place the upscaler starts on **FSR 4.1.2** (XeSS 2 one tap away). A game the list marks
  "Not Supported" on Intel shows a red line and "Install OptiScaler" refuses with that reason.

- **VRR menus left to gamescope (2026-11-05):** confirmed on the Claw (Bazzite 45, gamescope 3.17): Steam's menus
  are smooth at 120 Hz with VRR on. ClawDeck's "VRR only in games" switching is removed.
- **Bazzite update line (2026-11-05):** Fixes shows "Bazzite up to date (kernel, Intel graphics driver, Mesa,
  gamescope)" and, while an update waits, "Bazzite 45.x is available (running 44.x) …" (rpm-ostree upgrade --check, once
  per start, nothing downloaded). After updating: "… is downloaded: restart the Claw".

- **ClawDeck's own cabinet.dll (2026-11-05, `tools/cabinet-lzms`):** FSR 4.1.2 on XMX no longer needs Windows'
  `cabinet.dll`. The plugin zip carries `bin/cabinet.dll` (built and Wine-tested in CI), and ClawDeck asks only for
  nitec's `amd_fidelityfx_upscaler_dx12.dll` and `fsr-original.dll` in `~/fsr4-xmx`. Check on the Claw with the
  sideload build:
  1. Delete every Windows `cabinet.dll` you placed: `rm ~/fsr4-xmx/cabinet.dll ~/fsr4-xmx/cabinet_wine.dll`, and
     `find ~/.local/share/Steam/steamapps/common -iname cabinet.dll -not -path '*/Proton*'` lists nothing.
  2. Per-game for CONTROL Resonant: FSR 4.1.2 is offered (the note under Upscaler doesn't ask for `cabinet.dll`).
     Pick it, Apply. Next to CONTROL's exe: `cabinet.dll` with the same sha256 as
     `~/homebrew/plugins/ClawDeck/bin/cabinet.dll`, plus `cabinet_wine.dll` (the Proton's own). Both are listed in
     `.clawdeck-optiscaler/manifest.json`. The launch env has `cabinet=n,b`.
  3. Start the game: OptiScaler's menu shows FSR 4.1.2 as the upscaler, and the image is FSR 4 (not black, not
     XeSS). `OptiScaler.log` has no LZMS / Cabinet / CreateDecompressor error.
  4. Upscaler → the game's own (or Uninstall): `cabinet.dll` and `cabinet_wine.dll` are gone from the game folder.
     Uninstalling ClawDeck also removes `~/.local/share/clawdeck/cabinet.dll`.

- **PL2 by hand (2026-11-05):** Power → Custom → "Set PL2 yourself" on: a PL2 slider (TDP to 45 W),
  saved per power source; off: PL2 = TDP + 2 W. Check the chip reads it:
  `cat /sys/class/powercap/intel-rapl-mmio:0/constraint_1_power_limit_uw` (µW). Other profiles always use TDP + 2 W.
- **"Set PL2 yourself" reads the chip back (2026-11-15):** `set_pl2` looked for the PL2 read-back under a key that
  `power.status()` only has under `rapl`, so it never had one: the toast said "PL2 set (yours)" and a PL2 the chip
  did not take was reported as done. Now the toast carries RAPL's PL2 as read ("PL2 40 W (yours)"), and a different
  value fails the call. Check on the EX: Power → Custom → "Set PL2 yourself", 40 W: the toast reads
  "PL2 40 W (yours)".

- **XeFG even flips (xess-xmx-linux branch xefg-flip-metering, test build fg-meter-test-build, 2026-11-05):** the
  shim spaces XeFG's presents itself, since on Intel XeFG leaves pacing to the Windows driver and nothing does it on
  Linux. Off by default. With `IGDEXT_FG_METER=1` (added to `~/.local/share/clawdeck/xmx.env` for the test) the log
  `C:\igdext_xell.log` should show `fgmeter ... out: ... short 0%` and `added latency per real frame avg 0.0x`;
  `IGDEXT_FG_METER=measure` gives the same lines without pacing, for comparison. Install/undo:
  `fg-meter-install.sh` on that branch. Steam's frame-time graph flat with VRR on, CONTROL Resonant at XeFG 2x; no
  crash with OptiScaler (the 2026-11-04 build crashed patching OptiScaler's wrapper; it now only touches DXVK's).

- **E-cores only removed (2026-11-05):** the kernel's hybrid scheduler places threads (maintainer: "should just be
  automatic"); a saved per-game setting is dropped at start. XMX: automatic in every game with XeSS, XeSS-FG, XeLL or
  OptiScaler; "every game" is switched back to that once (games without any of them lost speed on the XMX driver).
  Check: Setup shows "XMX driver: in every game that can use the XMX cores".
- **Power, from the audit of proven tools (2026-11-05):** PL2 is now PL1 + 2 W (hhd, PowerControl, PowerStation), not
  scaled to 45 W; Autotune is off by default and marked beta; MangoHud is injected only while Autotune is on; Autotune
  no longer trims the CPU ceiling. Check: CONTROL at a 35 W pick, Autotune off, frame-time graph in Steam's overlay
  compared with before (it was noisy at 45 fps); the chip reads PL2 = 37 W (`cat /sys/class/powercap/intel-rapl-mmio:0/constraint_1_power_limit_uw`).
  Still to test on the Claw (audit): `platform_profile` → `custom` with the MSI firmware attributes (what steamos-manager
  and PowerControl do), MSR PL1 = MMIO PL1 with thermald left running, TCC offset / PTC instead of the software guard,
  `gamescope --version` (3.17 holds the refresh rate in the menus: the VRR scope's off/on flicks can go).

- **Temperature guard steps back up (2026-11-04):** after "thermal: stepped to N W", once the package is under 80 °C
  the TDP climbs 2 W every 30 s back to your limit and the line goes away (it used to stay stepped down until a new
  pick or the game closed: 21 W at 55 °C with a 35 W limit).

- **Autotune, quieter (2026-11-04):** play CONTROL for 10 min with Autotune on. The TDP changes only in the first
  2–3 min (no longer a full re-check every 5 min, now 20 min). A loading screen or the pause menu doesn't move the
  TDP (ClawDeck's log: "skipped a N fps window"). At exit the toast reads "Autotune saved X W of your Y W: A of B fps,
  saves Z W", or "still learning; nothing new saved" after a short session. `ps -L -o psr,ni -p $(pgrep -f ClawDeck)`
  in a game shows only cores 10-13 (the LP-E cores) and nice 10.

- **Onimusha's Capcom setup (2026-11-05, writes into game folders; test before the next release):** Capcom RE
  Engine games now get exactly what the tested setup installs on Windows: onehoon's REFramework fork (release 15, the
  23252480-byte `dinput8.dll`) and OptiScaler **v10** (nightly-20261030; 0.9.5-pre4 isn't on GitHub) in the
  `OptiScaler\` subfolder layout, with the tested ini (DLSS-G in, XeFG out). Every other game stays on 0.9.4.
  1. Start clean: in Onimusha's folder (`steamapps/common/<Onimusha>/`, next to `OnimushaWotS.exe`) there is no
     `dinput8.dll`, `dxgi.dll`, `OptiScaler.ini`, `OptiScaler\` or `re2_fw_config.txt` (move the files from tonight's
     hand test out first; ClawDeck leaves OptiScaler it didn't install alone and says so).
  2. ClawDeck → Games → Onimusha: the reasons read "The Capcom setup, tested on a Claw". Upscaler
     XeSS 2, Frame generation 2x → **Install OptiScaler in this game** (one tap; the first time downloads about 72 MB
     of OptiScaler and 13 MB of REFramework, both sha256-checked).
  3. The folder now has, next to the exe: `dxgi.dll` (OptiScaler), `OptiScaler.ini`, `dinput8.dll` (REFramework),
     `re2_fw_config.txt` with `REFrameworkConfig_MenuKey_V2=46` (only that line if the file was there before),
     `.clawdeck-optiscaler/`, `.clawdeck-reframework.json`, and `OptiScaler\` with libxess, libxess_fg, libxell,
     amd_fidelityfx_*, dlssg_to_fsr3_amd_is_better.dll, `fakenvapi.dll` (v1.4.1, 415232 bytes) with `fakenvapi.ini`
     (enable_logs=1, enable_trace_logs=0, force_latencyflex=0, force_reflex=0), `D3D12_OptiScaler\` and
     `plugins\OptiPatcher.asi`. No OptiScaler library and no fakenvapi next to the exe. (v10 has fakenvapi built
     in, `[fakenvapi] UseFakenvapi=true`, and doesn't load the file; it is there because the tested folder has it.
     Check OptiScaler.log for the low-latency line: the game's Reflex should become XeLL.) `OptiScaler.ini` has Dx11Upscaler=fsr31_12, Dx12Upscaler=xess,
     VulkanUpscaler=xess, [FrameGen] Enabled=true, FGInput=DLSSG, FGOutput=XeFG, FGNvngxReplacement=XeFG_NVNGX,
     [DLSSG] OverrideInterpolationCount=1 (2x; 3x writes 2, 4x writes 3; OptiScaler counts generated frames, so
     =2 ran 3x there on Windows), [NvngxFG] DisableHudless=true,
     OptiDllPath=.\OptiScaler, LoadAsiPlugins=true, RestoreComputeSignature=true, and [Spoofing] Dxgi=auto (the
     wiki's Dxgi=false is left out on purpose: DLSS-G needs the spoofing). The game's launch environment
     (`~/homebrew/data/ClawDeck/launch/2638890.env`) has `WINEDLLOVERRIDES=dxgi=n,b;dinput8=n,b;` and no
     `Spoofing_Dxgi`.
  4. Start the game. Graphics: DLSS on, DLSS Frame Generation on; quit and start again. Insert opens OptiScaler's
     menu: Upscalers shows XeSS with Input DLSS; Frame generation shows FG Input DLSSG, FG Output XeFG. Delete opens
     REFramework's menu (no longer Insert). The OptiScaler overlay (if picked) shows "(XeFG)".
  5. FSR 4.1.2 (with nitec's files in `~/fsr4-xmx`): pick it, Apply: nitec's `amd_fidelityfx_upscaler_dx12.dll` and
     `fsr-original.dll` land in `OptiScaler\`, ClawDeck's `cabinet.dll` / `cabinet_wine.dll` next to the exe, the env adds
     `cabinet=n,b`. In OptiScaler: FSR 3.X with FSR 4.1.2 under FFX Upscaler. Switch XMX off for the game: FSR 4.1.2
     disappears from the list (and the backend refuses it); it was a black screen without XMX.
  6. **Uninstall OptiScaler from this game**: the folder is exactly as in step 1 (also after the game wrote its logs:
     OptiScaler's, REFramework's `reframework_log.txt` and empty `reframework\` folders go too; a
     `re2_fw_config.txt` that was there before keeps every line, the menu key back as it was).
  7. An Onimusha set up by ClawDeck 0.2 (0.9.4 next to the exe): Per-game offers "Update OptiScaler to
     10.0.0-pre1-20261030"; Apply takes 0.9.4 out and puts v10 in `OptiScaler\`; praydog's nightly `dinput8.dll` is
     swapped for onehoon's.
  Not tested anywhere yet: Dragon's Dogma 2 (and its character creator) and the other RE Engine games get the same
  setup, marked "not tested on a Claw" in their reasons. Releases 16 and 17 of onehoon's REFramework are watched by
  `tools/upstream_watch.py`, not pinned.

- **Power: your limit and Autotune stay in step (power-sync, 2026-11-04):** in the menus pick Custom 20 W (battery)
  and Max (plugged in). Start CONTROL on battery: the slider shows 20 W and the line under it "Autotune: using N W of
  your 20 W · saves M W · fps", following Autotune every 3 s, never back to an old value. Pick Balanced in the game:
  the chip reads 17 W at once and Autotune measures again from 17 W; the next start of CONTROL comes back at Balanced.
  Quit the game: the chip reads 17 W again (RAPL PL1), not the firmware's 25 W; with nothing picked on a power source
  it reads the firmware's value and thermald is running again (`systemctl is-active thermald`). Plug in during and
  after the game: Max 35 W both times; unplug with no game: back to the battery pick. Unloading ClawDeck in Decky puts
  PL1 back to the firmware's value and starts thermald.
- **ClawDeck's own OptiScaler (writes into game folders; replaces Decky Framegen for new setups):** Games → a DLSS
  game (Wukong, after moving it off the NTFS card) → Use the recommended setup: OptiScaler 0.9.4 downloads (55 MB,
  sha256 checked), lands next to the exe as dxgi.dll (or the next free proxy name) with OptiScaler.ini, OptiPatcher in
  plugins/; the game's own files go to .clawdeck-optiscaler/backup. In the game: DLSS → XeSS on XMX (Insert shows
  OptiScaler's menu); frame generation "from the game's DLSS FG" gives XeSS-FG at the Per-game multiplier
  (XeFG InterpolationCount) with Reflex forced on through fakenvapi (XeLL). Switch the upscaler to FSR 4.1.1 XMX and
  Lite from Quick Access: OptiScaler.ini and the files change without opening OptiScaler. Undo leaves the folder as it
  was. A game Decky Framegen patched shows the warning and isn't touched.
- **Recommended setup per game (writes into game folders):** Onimusha: see "Onimusha's Capcom setup" above.
  CONTROL → the recommendation is "FSR on XeSS" (no OptiScaler).
- **Fan presets (Power → Fan, msi-wmi-platform hwmon):** Quiet / Cooling / Full speed / Custom each change the fans'
  rpm audibly (Quiet: off at idle), the "Fans now" line follows; Automatic puts MSI's own control back
  (pwm1/pwm2_enable read 2 again). The choice survives closing a game, a restart and sleep.
- **Charge limit (Power → Battery):** at 80 % the battery stops charging at 80 % on the charger
  (`charge_control_end_threshold` reads 80); Off writes back the value it had (0 = no limit). Survives a restart.
- **Autotune, efficiency (on by default, on the charger too):** CONTROL on battery and plugged in, from 35 W: note
  where it settles (Power shows "saves ~Z W"), the fps lost against the start (should be at most ~2–3 fps), and how
  long it takes to settle (3 W steps on a plateau, then 1 W). Check it holds a frame limit with the least power.
- **FSR 4.1.2 picks index 6:** OptiScaler lists AMD's 4.0.2 first with the B Boost files; ClawDeck now writes
  UpscalerIndex 6 (4.1.2). In CONTROL 4.1.2 drew a black picture in gameplay (upscaler time 0.00 ms): see the
  OptiScaler log before offering it by default.
- **FSR 4.1.1 on XMX (nitec's B Boost), regular and Lite:** with the files in `~/fsr4-xmx`, Per-game → OptiScaler
  upscaler "FSR 4.1.1 on XMX" in a DLSS game with ClawDeck's OptiScaler: the game folder gets the B Boost
  `amd_fidelityfx_upscaler_dx12.dll` in place of OptiScaler's own, `fsr-original.dll`, ClawDeck's `cabinet.dll` (LZMS) and
  `cabinet_wine.dll` (all in the manifest), OptiScaler shows FSR 4 as the upscaler, the image is FSR 4. Lite vs
  regular: fps and image. Switching back to XeSS puts OptiScaler's own FSR 4 file back.
- **VRR in the menus (2026-11-04 report: menus still choppy, VRR couldn't be switched off):** the scope now decides
  "in a game" from Decky's Router.MainRunningApp, switches VRR off only in the menus and leaves a game's VRR to
  Steam's switch. Check ClawDeck's log (`ui: vrr: …` lines) while going menus → game → menus.
- **Watts in the overlay:** after switching it on, restart once: `/etc/tmpfiles.d/clawdeck-overlay-power.conf`
  opens the counters at boot, before mangoapp starts; the overlay (level 3+) shows CPU W.
- **First-run fixes:** on a fresh install the face-button fix and the split-lock log fix apply by themselves once;
  removing one in Fixes keeps it removed after a restart.
- **Before the next release, on the Claw (2026-11-04 build):** per-game XMX (Setup moves the session file aside; Game
  Mode comes up; CONTROL logs `clawdeck: XeSS XMX driver for this game`), "VRR only in games" (menus 120 Hz, games
  30–120, stands aside with the monitor on USB-C), "Silence the split-lock flood" (Fixes; `journalctl -u
  steamos-manager --since -60s | grep -c sls` drops to 0; steamos-manager's CPU drops), the NTFS warning in Per-game
  (Wukong on the SD card), watts in the overlay.

- Efficiency session (findings §22.1 + §22.4), with Black Myth: Wukong's benchmark: TDP sweep 8→35 W with
  `tools/efficiency-sweep.sh`, then the same at 10/12/17 W with GPU `power_saving`; watch intel_lpmd and per-core load
  during the run. Same session as the XeLL Windows-vs-Bazzite latency comparison.
  How to run it: from a ClawDeck checkout, `sudo tools/efficiency-sweep.sh --label wukong` (1080p preset fixed, XeSS
  on XMX, frame generation off, frame limit off, Autotune off, game started through the launch wrapper); start the
  benchmark at each prompt. It repeats 10/12/17 W with `power_saving` by itself. Commit
  `docs/measurements/ptl-efficiency-wukong.csv`; the knee it prints and the CSV set the six profile wattages.

- **Power (§17–§19), on the Claw:** move the TDP slider: the profile turns Custom and "RAPL reads N W" follows; pick
  Max 35 W and check whether PL1 above the chip's `max_power_uw` (25 W) takes effect (busy loop or
  `tools/selftest.py`). With Autotune on in a GPU-heavy game, the status line should say GPU-bound, in a CPU-heavy one (Dragon's Dogma 2
  town) CPU-bound; Autotune changes the TDP only, the CPU and GPU levers stay as the profile set them. Open
  Advanced, change CPU boost, the CPU ceiling, both GPU clocks and the energy mode: each toast reads back, and
  Autotune shows "paused".
- **Autotune v3 (§22):** in a game with XeSS frame generation (Cyberpunk 2077, Wuthering Waves), the status line must
  say "rendered, 2x shown" (or 3x) and the fps must be the rendered rate; check `drive_c/igdext_xell.log` in the
  game's prefix has `xellSleep stats` lines and stays small over 10 minutes (the shim also routes XeLL's own debug
  messages there). In a game without frame generation: steps down from where it starts, each taken back within a
  second when frames are lost, holding within 0.5 % (or the game's own wobble) of what the set TDP gives. Does OptiScaler's XeFG output call `xellSleep` too (then its frames count right)? Note what you see.
- **Temperature guard (§21), on the Claw:** check the sensor: `cat /sys/class/thermal/thermal_zone*/type` should list
  `x86_pkg_temp` (the health check names the one ClawDeck uses). Then set Max 35 W, start a heavy game, cover the fan
  vents or block the airflow briefly, and watch the Power panel: past 95 °C the read-back line should say "thermal:
  stepped to 33 W", then 31, 29… every 10 s until under 90 °C, never below the floor; the TDP read back must
  follow. Moving the slider afterwards should clear it. Does 95 °C come before the chip's own throttle (TCC)? If TCC
  always wins first, note the temperature it settles at.
- **GPU efficiency mode (§22.3), on the Claw:** Setup → Run self-test: "GPU efficiency mode base / power_saving"
  and its revert must PASS. Pick Battery: `cat /sys/class/drm/card0/device/tile0/gt0/freq0/power_profile` reads
  `base [power_saving]`; pick Balanced: `[base] power_saving`. The Advanced toggle follows and reads back; Autotune leaves the
  mode as the profile set it.
- **PL1 window (§22.2), on the Claw:** with Autotune on, start a game and read
  `cat /sys/class/powercap/intel-rapl-mmio:0/constraint_0_time_window_us`: 8000000 (or the firmware's nearest step)
  for as long as Autotune governs the game. Paused (a manual change), in xemu, and after closing the game it must
  read 55970000 again. If the firmware refuses it, the log says so once and autotune carries on with 56 s.
- **XeSS install / update (§20), on the Claw:** tap Install XeSS XMX (or Update): when it finishes the Setup line
  must say "driver loads" straight away, without a log-out (the toast still asks for one so games pick it up). Tap
  Update again: the toast says "XeSS XMX 20261018: up to date", no red row, nothing downloaded (the log says so).
  Check the driver check ran as you: `ls -l ~/.cache/xess-xmx/driver-status` is owned by your user and fresh.
- **XeSS XMX default:** after Install XeSS XMX and one log-out, Per-game should say "XeSS on XMX (default)" for any
  game. Turn it off for one game: ClawDeck adds itself to that game's launch options and the line says "stock
  driver"; the game's XeSS then runs as DP4a.

- **Found on the Claw 2026-11-04 (Onimusha, Proton 11.0, kernel 7.2.9):**
  - **Log flood (14:10, after a hard reboot from a sleep that didn't wake):** steamos-manager's root daemon forwards
    every `split_lock_warn` ftrace hit from Steam's `CHTTPClientThread`s (~30,000 lines in the first 3 minutes,
    `ServiceUnknown` for each: the SLS log receiver isn't there on Bazzite), at ~14 % of a core. The 50 MB journal
    rotates within minutes, so the boot that didn't wake left no log. `split_lock_mitigate` is already 0 (no
    throttling). **To do:** see how the daemon sets up the trace (`strings /usr/lib/steamos-manager`), then a
    revertible Fixes switch that stops the forwarding; then catch the next failed wake in the journal.
  - **Speakers:** not a driver problem. With the MO27Q2 on USB-C, audio went to the monitor's DisplayPort output
    (shown in Steam as "Core Ultra … HD Audio"); unplugged, the RT721 Speaker sink is default and works. Nothing to
    fix in ClawDeck; Steam's audio output picker chooses.
  - xess-xmx-linux's session-wide driver file (`~/.config/environment.d/50-xess-xmx.conf`) reached Steam's own screen
    and every game: Onimusha (no XeSS) lost much of its frame rate, games recompiled shaders at every switch, and the
    one boot that went black ran with it. ClawDeck now moves that file aside after installing and loads the XMX driver
    per game (`xmxscope.py`): only games that ship XeSS or OptiScaler, or Per-game forced on. **Test:** install
    (Setup), reboot, Game Mode comes up; a XeSS game logs `clawdeck: XeSS XMX driver for this game` (PROTON_LOG=1);
    Onimusha doesn't.
  - Capcom's DRM kills Onimusha when its FidelityFX DLL is swapped (`FSR=xess`, RaiseFailFastException). Capcom games
    need OptiScaler + REFramework instead.
  - Onimusha flashes on every driver tried (stock Mesa 26.2.4, the package's 26.1.2 and 26.3.0-rc3): not ours.
  - Overlay watts: `energy_uj` is 0400 and BAT1 `current_now` reads about −64.6 A on the EX (HARDWARE-FINDINGS §6).
    Fixes → "Show watts in the overlay" (new); check the overlay shows CPU W and ClawDeck's battery line reads ~10 W idle.
  - Speakers silent although PCM runs (RT721 SDCA, sof-sdca-1amp): the vendor power gates stay off
    (stevedamnvan/msi-claw-8-ex-linux, EX-AUD-001/002). Not built yet: its exact sequence still has to be read.
  - GPU watts in the overlay: "Show watts in the overlay" also opens the uncore counter (`intel-rapl:0:1` on the EX).
    MangoHud with flightlessmango/MangoHud#2211 shows that counter as the GPU's watts; check GPU W follows the game.
  - VRR 30–120 makes Steam's menus choppy (the screen really runs at the game's 30 fps). New: Fixes → "VRR only in
    games" (src/vrr.ts): watches Steam's Perf state and sets is_vrr_enabled on while current_game_id ≠ 0, off
    otherwise, with skip_storage_update. **Test:** VRR fix installed; library scrolls sharp at 120 Hz; in a game the
    overlay's refresh follows the fps (30–120); Steam's own VRR toggle shows the change; the switch off = Steam's
    toggle in charge again. If Steam ignores the write, the Fixes line says "VRR switching failed".
- **0.2 on the Claw (none of this has run on hardware yet):**
  - Setup → Update XeSS XMX to 20261104, log out and in: "XeSS XMX 20261104: driver loads".
  - Per-game in a game with XeSS frame generation (Cyberpunk 2077): Frame generation 3x, restart the game, 3x on
    screen; Low latency line says "working" after a minute of play (`drive_c/igdext_xell.log` has "auto: waited in"
    lines); turn it off: the line says "off" next launch. Feel the difference.
  - Latest XeSS libraries in a game with an old XeSS; FSR games → "FSR on XeSS" in an FSR 3.1 game without XeSS.
  - Decky Framegen compatibility: a game Decky Framegen patched (fgmod in its launch options) shows "OptiScaler:
    Decky Framegen's, in this game" and isn't touched; "Stop using Decky Framegen here" takes fgmod out of the launch
    options, then Decky Framegen → Unpatch, and the recommended setup is offered again.
  - Autotune: on the charger it no longer starts unless "Autotune when plugged in" (Advanced) is on.
- **XeSS install "file not found"** (brief): the backend now reports the exact step and reason (download: URL and
  HTTP code; archive: what it unpacked where; install.sh: exit code and its last lines) in the toast and the red
  row, and logs it. Tap Install XeSS XMX once more and post the red line if it still fails.
- **Quick Access**: the four panels with the D-pad at 1280×800: nothing truncated in the profile picker, the read-back
  line changes after every pick, a fast flick through the profiles toasts the last one only.
- **VRR fix**: Fixes → Install, restart, `edid-decode` 30–120, 120 Hz mode still offered, games at 35–45 fps no
  longer judder. Remove puts the factory range back.
- **Self-test from the UI**: Setup → Run self-test with no game running; expect 12 PASS.
- Pin tested versions in `assets/versions.json` (all are `pending-verification`, so installs ask "latest, untested?").
- After the first upstream-watch run: test the listed versions, then `python3 tools/upstream_watch.py --update-lock`.
