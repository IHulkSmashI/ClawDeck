<!-- r/MSIClaw post for v0.2.0-beta.1. The maintainer posts it. -->

**ClawDeck beta is out: FSR 4.1.2, XeSS and frame gen on the XMX cores, on Bazzite, in one Decky plugin**

Hey all, the first ClawDeck beta is up: https://github.com/IHulkSmashI/ClawDeck/releases/tag/v0.2.0-beta.1

ClawDeck is a free Decky plugin for Intel Claws on Bazzite. Everything lives in one Quick Access panel:

- **FSR 4.1.2 on the XMX cores.** It downloads by itself and shows up for DirectX 12 games with DLSS or FSR. Best
  picture we've had on the Claw.
- **XeSS 2 on XMX** and **XeSS frame gen 2x/3x/4x**, with XeLL low latency always on. CONTROL runs 3x at around
  140 fps shown on our 8 EX.
- **One tap per game:** pick the game, the upscaler and frame gen, tap Install. Capcom games get REFramework so they
  start (Onimusha works). Uninstall puts the game back exactly as it was.
- **Power:** profiles 8–35 W, PL2, fan curves, charge limit, and Autotune (beta) to find the lowest watts that keep
  your frame rate.
- **Fixes:** VRR down to 30 Hz, the two MSI buttons on the 8 EX, real watts in the performance overlay.

Install: Decky → Developer → Install plugin from URL, and paste the ClawDeck.zip link from the release page. The
README has the steps.

Tested on our Claw 8 EX. If you have an 8 AI+, 7 AI+ or A1M, we'd love to hear how it goes.

Known stuff: RE Engine games (Onimusha, RE) have some flicker, that's Intel's Linux driver and they're on it. No gyro
on the 8 EX.

**Feedback is what we need most.** Comments here are fine, but GitHub is where we can track it:

- How a game runs: https://github.com/IHulkSmashI/ClawDeck/issues/new?template=game_report.yml
- Ideas: https://github.com/IHulkSmashI/ClawDeck/discussions/categories/ideas
- Questions: https://github.com/IHulkSmashI/ClawDeck/discussions/categories/q-a

It's free and stays free. There's a Ko-fi link on the GitHub page if you want to buy us a coffee.
