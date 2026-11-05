# Test plan: Black Myth: Wukong on the Claw 8 EX (Bazzite)

Goal: XeSS, XeSS frame generation, low latency and FSR 4 all on the XMX cores, with numbers we can compare to Windows.
Use the free **Black Myth: Wukong Benchmark Tool** (Steam app 3132990); the full game (2358720) works the same way.

## Before the first run (once)

1. xess-xmx-linux **20261104** installed (ClawDeck Setup → Update XeSS XMX, or its `install.sh`), then log out and in.
   Check: `cat ~/.cache/xess-xmx/driver-status` says the patched driver loads.
2. Decky Framegen installed, opened once, **Setup OptiScaler Mod** tapped (`ls ~/fgmod/fgmod` exists).
3. A ClawDeck checkout on the Claw for the report script: `git clone https://github.com/IHulkSmashI/ClawDeck ~/ClawDeck`
   (`cd ~/ClawDeck && git pull` later).
4. Same conditions for every run: power cable in, the same fixed TDP (17 W, Autotune **off**), Steam's frame limit
   **off**, 1920×1200, preset **Medium**, the same output resolution in the benchmark. Run each one twice and keep the
   second result (the first launch after a change compiles kernels).

After each run: `~/ClawDeck/tools/game-report.sh 3132990 > ~/wukong-R<n>.txt` and note the benchmark's average fps.

## The runs (Steam → the benchmark → Properties → Launch options)

| Run | Launch options | In the benchmark | What it shows |
|---|---|---|---|
| R1 | `%command%` | XeSS Balanced, frame generation off | the game's own XeSS on XMX: the report's XMX section has no `fallback` |
| R2 | `IGDEXT_XELL_LOG=1 Dx12Upscaler=xess FrameGen_FGOutput=xefg ~/fgmod/fgmod %command%` | DLSS Balanced, DLSS frame generation on | OptiClick's Intel setup on Linux: OptiScaler's XeSS + XeSS frame generation, both on XMX; low latency **on** |
| R3 | R2 with `IGDEXT_XELL_CAP=off` in front | same | the same with low latency **off**: compare the `xell latency` lines of R2 and R3 |
| R4 | R2 with `XESS_FG=3` in front | same | 3x, as OptiClick sets on Arc |
| R5 | FSR 4 on XMX (below) | DLSS Balanced, DLSS frame generation on | FSR 4 upscaling + XeSS frame generation + low latency, all on XMX |

R5 setup (by hand for now; ClawDeck will do it per game once tested):

1. In OptiScaler's folder in the game (where `OptiScaler.ini` is, after one R2 launch): put the B Boost build there as
   `amd_fidelityfx_upscaler_dx12.dll` (keep any original as `.orig`), and `fsr-original.dll` = xess-xmx-linux's
   `ffx/amd_fidelityfx_upscaler_dx12.dll`, renamed.
2. ClawDeck's open `cabinet.dll` (`~/homebrew/plugins/ClawDeck/bin/cabinet.dll`, from `tools/cabinet-lzms`; no Windows file needed) into the same folder, and the
   Proton's own one next to it as `cabinet_wine.dll`:
   `cp "<Proton>/files/lib/wine/x86_64-windows/cabinet.dll" <OptiScaler's folder>/cabinet_wine.dll`
   (`<Proton>` = the one the benchmark runs with, e.g. `~/.local/share/Steam/steamapps/common/Proton - Experimental`).
3. Launch options: `WINEDLLOVERRIDES=cabinet=n,b FSR4_XMX_NO_DIALOG=1 IGDEXT_XELL_LOG=1 Dx12Upscaler=fsr31 FrameGen_FGOutput=xefg ~/fgmod/fgmod %command%`
   (add `FSR4_XMX_LITE=1` for the Lite model).
4. In OptiScaler's overlay (Insert key or a keyboard on the Claw): the upscaler must read FSR 4, frame generation XeFG.

## Windows comparison (same day, same TDP and settings)

Boot Windows, OptiClick on the benchmark (XeSS + XeFG 3x), same preset. Note the average fps; with Intel Graphics
Software's overlay or PresentMon, note the latency it shows. (The Linux number is XeLL's own "simulation start ->
present end"; a Windows tool that reads the same XeLL reports is planned so both sides use one counter.)

## What to send back

The five `~/wukong-R<n>.txt` files, the average fps of each run (Linux and Windows), and anything that looked wrong
(flicker, HUD ghosting, crash). Attach them to the chat or a GitHub issue.
