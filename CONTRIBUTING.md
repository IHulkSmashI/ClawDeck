# Contributing to ClawDeck

ClawDeck is MIT-licensed and built in the open. Pull requests from anyone with a Claw (or any handheld) are welcome.

**Ground rules**
- Battery first: no polling, no timers, unless a game is running and the user turned the feature on.
- Every sysfs write goes through `clawdeck.sysfs.write` so it is revertible and dry-run aware.
- Don't re-implement what an upstream project does: InputPlumber / Handheld Daemon (buttons, gyro, RGB), PowerStation / steamos-manager / Handheld Daemon (TDP), PowerControl (clock paths), xess-xmx-linux (XeSS XMX). Fix it there and call it from here.
- Every UI control gets a `Hint`: what it does, why you'd turn it on, what it costs. Two or three bullets, no paragraphs.
- Tests: `python3 -m pytest -q tests` must pass; add a test for backend behaviour you change.
- A new device is one row in `hw.py` plus the survey output in the PR.

**Workflow**
1. Fork, branch, commit small.
2. `pnpm install && pnpm build && pnpm typecheck` for the UI; `pytest` for the backend.
3. Test on hardware if you can and say in the PR what you tested on (model, OS build, kernel).
4. Open the PR; CI builds the zip you can sideload to test the exact artifact.

**Support the work**: [ko-fi.com/gamervault](https://ko-fi.com/gamervault). Never a paywall, never a nag; the plugin stays free.
