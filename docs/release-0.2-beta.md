# ClawDeck 0.2.0 beta: release plan

The first build people download. A GitHub **pre-release** (beta), tagged `v0.2.0-beta.1` by the maintainer only after
every box below is ticked on a Claw (CLAUDE.md: no tag or release without the maintainer's test). Claw 8 EX only;
other Claws via the hardware survey.

## 1. Merge
- [x] Everything merged into main (rebase); CI green on main (7554efe, 2026-11-05).

## 2. Clean install on the Claw (reset first: `tools/claw-reset.sh`, then reboot)
- [ ] Install Decky Loader, then ClawDeck with the README's command; the icon is the ClawDeck logo, not a hammer.
- [ ] First run shows "Set up ClawDeck": step 1 XMX driver installs; step 2 VRR fix installs; restart; Done.
- [ ] Face-button fix and quiet log applied by themselves; overlay watts after one restart.

## 3. Per feature (each with a ✓ in the panel, nothing in red)
- [ ] **XMX**: CONTROL (Proton 11.0) and one GE-Proton/Experimental game: "✓ XMX cores used in the last launch".
- [ ] **XeSS 2 + 2x**, **FSR 4.1.2 + 2x**: picture, overlay shows the upscaler and XeFG, fps noted.
- [ ] **FSR 4.1.2 Lite**: offered only where it runs; picks the Lite entry (or hidden on Xe3 if it can't run).
- [ ] **Onimusha**: OptiScaler + onehoon's REFramework (release 15) install in one tap from a clean game folder; the
      game starts; DLSS → XeSS on XMX, frame generation XeFG.
- [ ] **Uninstall OptiScaler**: the game folder is as shipped (compare a file list before/after).
- [ ] **VRR**: menus at 120 Hz, a game unlocked (not 30/60) without touching Steam's switch; an external display left
      alone.
- [ ] **Power**: a profile picked in a game is still there at the next start, on battery and plugged in; the chip's
      limit (both RAPL interfaces) matches the panel (power recorder, `~/clawdeck-test/power.csv`).
- [ ] **Autotune (beta, off by default)**: when switched on, settles below the ceiling, fps loss ≤ ~2, "saves ~N W"
      shown; off, nothing of it runs (no MangoHud injected).
- [ ] **PL2**: Custom → "Set PL2 yourself" sets PL2; off, PL2 is PL1 + 2 W.
- [ ] **E-cores per game**: the game's threads stay on cores 2-9 (`taskset -p` on its pid).
- [x] **Frame pacing** (CONTROL, XeSS + XeFG 2x + XeLL, Bazzite 45.20261104): even and smooth with the stock
      driver, 2026-11-05. Confirm with Steam's overlay at level 4 (flat frame-time line).
- [ ] **Sleep/wake** with a game open, three times: no hang, power and VRR come back.
- [ ] **Quick Access**: opens on the first press; sections fold; a pick keeps the focus; toasts readable.

## 4. Docs and post
- [ ] README guide matches what's on screen (screenshots re-taken on the Claw if the preview differs).
- [ ] CREDITS.md lists every upstream (OptiScaler, OptiPatcher, nitec's FSR 4.1.2 build, xess-xmx-linux, REFramework,
      InputPlumber, wimlib for cabinet.dll, fakenvapi).
- [ ] Feedback paths live: Discussions on (Ideas, Q&A), the Game report form opens, README → Feedback links work.
- [ ] Reddit post (docs/posts/02-beta.md) with the release link; posted by the
      maintainer.

## 5. Release
- [ ] Bump `package.json` / `plugin.json` to 0.2.0; changelog in the release notes (from STATUS.md "Done").
- [ ] Maintainer tags `v0.2.0-beta.1`; CI's release job attaches ClawDeck.zip; mark it pre-release.

## Known limits to state in the notes
- Gyro on the 8 EX: not possible on Linux (MSI sensor firmware).
- Battery watts in Steam's overlay are wrong on this Claw (the kernel reads the battery's current ~100x too high);
  ClawDeck shows the chip's own watts (RAPL), which are right.
- FSR 4.1.2 needs nitec's files, which users fetch themselves (OptiScaler Discord).
- Onimusha: surfaces flicker in and out with or without mods (an RE Engine bug seen on other GPUs on Linux too).
- CONTROL with HDR on and FSR 4 + XeFG: the clouds can flicker (being looked into).
