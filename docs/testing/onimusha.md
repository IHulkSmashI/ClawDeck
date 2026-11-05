# Test plan: Onimusha: Way of the Sword on the Claw 8 EX (Bazzite)

Goal: FSR 4 upscaling and XeSS frame generation together on the XMX cores, with low latency, in a Capcom RE Engine
game. Use the game (Steam 2638890) or Capcom's free benchmark from its store page; `game-report.sh onimusha` finds
either by name.

Why this game is harder than Wukong: it has DLSS and FSR 3.1 but no XeSS, its frame generation is DLSS-FG (through
Streamline), and OptiScaler works in it only with REFramework (Capcom's anti-modding DRM). OptiScaler's notes also say
REFramework + XeSS frame generation stutters. So we try xess-xmx-linux's own FSR route first, which needs neither.

## Once

Same as `wukong.md` → "Before the first run" (xess-xmx-linux 20261104 installed and logged in again, the ClawDeck
checkout on `intel-presets`). Same conditions every run: charger in, fixed 17 W, Autotune off, Steam's frame limit off,
1920×1200, the same preset. Run each one twice, keep the second.

After each run: `~/ClawDeck/tools/game-report.sh onimusha > ~/oni-O<n>.txt`, and note the average fps.

## O1: what the game ships (decides the route)

Launch options `%command%`, in the game: FSR (3.1) upscaling, Frame Interpolation off. Then look at the report's
**FidelityFX DLLs** section:

* `amd_fidelityfx_loader_dx12.dll` + `amd_fidelityfx_upscaler_dx12.dll` (+ maybe `amd_fidelityfx_framegeneration_dx12.dll`):
  FSR 3.1.4+, the route below works.
* only `amd_fidelityfx_dx12.dll`: FSR 3.1.0–3.1.3; xess-xmx-linux swaps it for AMD's loader itself (O2 works, O4 needs
  the upscaler step after that swap: tell us and we adapt it).
* none: the FSR is built into the game; only OptiScaler (O5) can reach it.

## The runs

| Run | Launch options | In the game | What it shows |
|---|---|---|---|
| O2 | `FSR=xess FSR_FG=inject IGDEXT_XELL_LOG=1 %command%` (`FSR_FG=xess` instead if O1 found a framegeneration DLL) | FSR upscaling | XeSS SR + XeSS frame generation on XMX through the game's FSR, low latency on; no OptiScaler, no REFramework |
| O3 | O2 with `IGDEXT_XELL_CAP=off` in front | same | low latency off: compare the `xell latency` lines with O2 |
| O4 | FSR 4 on XMX (below) | FSR upscaling | FSR 4 (B Boost) upscaling + XeSS frame generation + low latency, all on XMX |
| O5 | only if O4 can't work: OptiScaler + REFramework (below) | DLSS or FSR, Frame Interpolation on | the OptiClick route |

### O4 setup (FSR 4 on XMX + XeSS frame generation)

Needs the game to have its own `amd_fidelityfx_upscaler_dx12.dll` (O1), and a frame generation DLL for `FSR_FG=xess`.
If it has no frame generation DLL, `FSR_FG=inject` can't be used here (it needs `FSR=xess`, which would replace FSR 4
with XeSS): skip to O5 and tell us.

1. In the folder of the game's `amd_fidelityfx_upscaler_dx12.dll`: rename it to `amd_fidelityfx_upscaler_dx12.dll.orig`,
   put the B Boost build there under that name, and `fsr-original.dll` = xess-xmx-linux's
   `ffx/amd_fidelityfx_upscaler_dx12.dll`, renamed.
2. ClawDeck's `cabinet.dll` there too (`~/homebrew/plugins/ClawDeck/bin/cabinet.dll`), and the Proton's own as `cabinet_wine.dll`:
   `cp "<Proton>/files/lib/wine/x86_64-windows/cabinet.dll" <that folder>/cabinet_wine.dll`.
3. Launch options: `WINEDLLOVERRIDES=cabinet=n,b FSR4_XMX_NO_DIALOG=1 FSR_FG=xess IGDEXT_XELL_LOG=1 %command%`
   (`FSR4_XMX_LITE=1` in front for the Lite model).
4. In the game: FSR upscaling, Frame Interpolation on. The game may now offer "FSR 4" by name.
5. Undo: delete the B Boost file, rename `.orig` back, remove `fsr-original.dll`, `cabinet.dll`, `cabinet_wine.dll`.

If the game refuses to start after step 1 (Capcom's DRM checking its files), undo and go to O5.

### O5 setup (OptiScaler + REFramework, the OptiClick route)

1. REFramework nightly for this game (praydog/REFramework-nightly, the Onimusha asset): its `dinput8.dll` next to
   the game exe. Change REFramework's menu key from Insert (OptiScaler uses it) to Delete.
2. OptiScaler through Decky Framegen, as `dxgi.dll`. Decky Framegen's OptiScaler is 0.9.4, which doesn't apply this
   game's fixes by itself, so set them in the launch options:
   `WINEDLLOVERRIDES=dinput8=n,b RestoreComputeSignature=true Dxgi=false Dx12Upscaler=xess FrameGen_FGOutput=xefg IGDEXT_XELL_LOG=1 ~/fgmod/fgmod %command%`
   (FSR 4 on XMX: `Dx12Upscaler=fsr31`, plus steps 1–2 of O4 in OptiScaler's folder and `cabinet=n,b` in the overrides).
3. In the game: DLSS (or FSR) upscaling, Frame Interpolation on.
4. Watch for the known REFramework + XeSS frame generation stutter; note it if you see it.

## What to send back

The `~/oni-O<n>.txt` files, each run's average fps, and anything wrong (stutter, flicker, HUD ghosting, a crash, the
game refusing to start). Windows comparison: the same benchmark with OptiClick, same TDP and preset.
