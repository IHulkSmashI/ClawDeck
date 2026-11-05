<!-- Reddit post for r/MSIClaw when 0.2.0 beta is tagged. The maintainer posts it; replace RELEASE_LINK. -->

**ClawDeck 0.2 beta is out: XeSS + frame gen + low latency on the XMX cores, on Bazzite**

Hey all, quick update on ClawDeck, the Decky plugin we've been building for the Claw on Bazzite.

The beta is up: RELEASE_LINK

What's in it:

- XeSS on the XMX cores, with XeSS frame gen and low latency (XeLL). Frame pacing in CONTROL is smooth on the latest
  Bazzite.
- FSR 4.1.2 on XMX too. You still grab nitec's two files yourself, everything else is handled.
- Per-game setup, OptiClick style: ClawDeck looks at what the game ships and picks the route. Capcom RE Engine games
  get REFramework so they don't crash (Onimusha works).
- Power: profiles, TDP slider, and PL2 control in Custom (or PL1 + 2 W like ClawTweaks). Autotune is there as a beta
  and off by default.
- Fixes: VRR down to 30 Hz, the face buttons on the 8 EX, and a heads up when a Bazzite update is waiting.

Known stuff: Onimusha has some surfaces flickering (it does that on Linux without mods too), CONTROL's clouds can
flicker with HDR on plus FSR 4 + frame gen, no gyro on the 8 EX.

**We really want feedback.** Which games you tried, what worked, what didn't. Comments here are fine, but the best
place is GitHub, where every post gets an answer:

- How a game runs: https://github.com/IHulkSmashI/ClawDeck/issues/new?template=game_report.yml (one short form)
- Ideas: https://github.com/IHulkSmashI/ClawDeck/discussions/categories/ideas
- Questions: https://github.com/IHulkSmashI/ClawDeck/discussions/categories/q-a

It's free and stays free. If you want to buy us a coffee, there's a Ko-fi link at the bottom of the GitHub page.
