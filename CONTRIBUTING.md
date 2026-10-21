# Contributing to ClawDeck

ClawDeck is MIT-licensed and built in the open. Pull requests from anyone with a Claw (or any handheld) are welcome.
How it works inside: [docs/DESIGN.md](docs/DESIGN.md). What is done and what is next: [STATUS.md](STATUS.md).

## Engineering rules

- **Battery first.** No timers unless autotune is on for a running game or a benchmark is recording; then one
  asyncio task at 1 Hz. Budget: under 0.5 % CPU and under 30 MB RSS over a 2-hour run, flat RSS over a 4-hour soak
  (`tools/soak.py`), measured before every release.
- **Fail safe.** Every sysfs write goes through `clawdeck.sysfs.write`, so it is recorded, revertible and dry-run
  aware. A failed write reverts. Everything reverts when the game closes. Nothing persists across reboot except the
  charge limit and, when the user turns them on, the 30–120 Hz VRR range (an EDID override with its own Undo) and
  the Claw 8 EX face-button hwdb entry (systemd's upstream entry, kept on uninstall because it only adds what newer
  systemd ships).
- **Dry run.** `CLAWDECK_DRY_RUN=1` or the Setup toggle logs what would be written (sysfs and Handheld Daemon).
- **No duplication, no fights.** Buttons and gyro: InputPlumber or Handheld Daemon, whichever the system runs, never
  both. TDP: Handheld Daemon when present, else the MSI firmware limits Steam's slider also uses. Clock paths:
  PowerControl's discovery. XeSS: xess-xmx-linux. Fix things upstream and call them from here. Another plugin that
  does the same job is detected and named, never overwritten.
- **Every control explains itself.** Each toggle and slider gets a `Hint`: what it does, why you'd turn it on, what
  it costs. Two or three short bullets, no jargon without a plain word beside it.
- **Every control is wired.** Every UI call has a backend method (a test checks this) and every backend call has a
  test.
- **One row per model.** A new Claw is one row in `py_modules/clawdeck/hw.py` plus the output of
  `tools/sysfs-survey.sh` in the PR.

## Working on it

    pnpm install
    pnpm build && pnpm typecheck     # the plugin UI
    python3 -m pytest -q tests       # the backend, against a fake sysfs tree
    pnpm preview                     # the panel in a browser with example data: preview/dist/ClawDeck-preview.html

Tools in `tools/`:

| Tool | What it's for |
|---|---|
| `clawdeck-launch.sh` | The per-game launch wrapper (MangoHud frame times for autotune, injector and HDR environment). |
| `sysfs-survey.sh` | Read-only survey of a new machine: what the kernel exposes, which services run. |
| `power-profile.sh` | Battery / performance / dock profiles from a terminal, without the plugin (`--dry-run`). |
| `suspend-cycle.sh` | Sleep soak: N suspend/resume cycles with deep-idle time per cycle. |
| `soak.py` | CPU %, peak memory and memory growth of the backend against the budget above. |
| `screenshots.mjs` | Retakes the README screenshots from the browser preview. |
| `render-banner.mjs` | Renders the logo, banner and download button from `assets/src/`. |
| `upstream_watch.py` | Newest upstream versions + live canaries for ClawDeck's parsers (see below). |

## Keeping up with upstream

ClawDeck depends on a lot of other people's projects, and each one can change under it. Three things keep it working:

1. **For users, at runtime** (`py_modules/clawdeck/updates.py`): OptiScaler, OptiPatcher, REFramework, lsfg-vk and
   Moonlight VRR always come from their newest release, checked at most once a day, verified against published
   checksums, with the previous version kept for one-tap rollback. Games are refreshed only after they close.
   The xess-xmx-linux driver is the exception: it is pinned in `assets/versions.json` and bumped after testing.
2. **Every day, on GitHub** (`.github/workflows/upstream-watch.yml` → `tools/upstream_watch.py`): the newest version
   of every upstream is compared with `assets/upstream.lock.json` (what was last tested), and ClawDeck's own parsers
   run against live upstream data (OptiScaler's compatibility list, OptiPatcher's list, the lsfg-vk builds page,
   Handheld Daemon's keys, Bazzite's Claw handling, gamescope-session's variables, release assets and checksums).
   Anything new or broken opens the "Upstream watch" issue; a broken canary also turns the run red.
3. **On every system update, on the Claw** (`py_modules/clawdeck/changes.py`): a new kernel, Mesa, controller
   service, XeSS driver state or Decky version re-runs the health check and tells the user what changed.

After testing new upstream versions on a Claw: `python3 tools/upstream_watch.py --update-lock`, commit the lock.
Dependabot keeps the build's own dependencies current.

## Workflow

1. Fork, branch, commit small.
2. Run the checks above.
3. Test on hardware if you can, and say in the PR what you tested on (model, OS build, kernel).
4. Open the PR. CI builds the zip, so you can sideload the exact build to test it.

## Support the work

[ko-fi.com/gamervault](https://ko-fi.com/gamervault). Never a paywall, never a nag; the plugin stays free.
