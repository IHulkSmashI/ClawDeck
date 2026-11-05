# cabinet-lzms

ClawDeck's own `cabinet.dll` for Proton. It adds what Wine's lacks, Windows' Compression API decompressor for
`COMPRESS_ALGORITHM_LZMS`, so the FSR 4 on XMX upscaler builds from the OptiScaler community (nitec's FSR 4.1.2)
unpack their GPU kernels without a copy of Windows' `cabinet.dll`.

## What nitec's DLL imports

`amd_fidelityfx_upscaler_dx12.dll` (B Boost / FSR 4.1.2 XMX build, 2026-10-29) imports from `Cabinet.dll` by ordinal
only: **#40 CreateDecompressor, #43 Decompress, #45 CloseDecompressor**, on six LZMS resources (168 MB unpacked).

## What this DLL exports

| Ordinal | Function | Here |
|---|---|---|
| 1–4, 10–14, 20–24 | `GetDllVersion`, `DllGetVersion`, `Extract`, `DeleteExtractedFiles`, FCI*, FDI* | forwarded to `cabinet_wine.dll` |
| 30–35 | `CreateCompressor` … `CloseCompressor` | stubs: FALSE, `ERROR_NOT_SUPPORTED` |
| 40, 43, 45 | `CreateDecompressor`, `Decompress`, `CloseDecompressor` | **implemented**: LZMS, buffered and raw (`COMPRESS_RAW`) |
| 44 | `ResetDecompressor` | implemented (no state to reset) |
| 41, 42 | `Set`/`QueryDecompressorInformation` | FALSE, `ERROR_NOT_SUPPORTED` |

Other algorithms (MSZIP, XPRESS, XPRESS_HUFF) fail in `CreateDecompressor` with `ERROR_NOT_SUPPORTED`.
`cabinet_wine.dll` is Wine's own builtin `cabinet.dll` (`<Proton>/files/lib/wine/x86_64-windows/cabinet.dll`)
renamed. ClawDeck copies it next to ours, so a game that also uses cabinet files (FDI/FCI) keeps working. The
forwards are resolved only when something imports them: nitec's DLL loads ours even without `cabinet_wine.dll`.

Buffered container (the default mode): a 24-byte header (u32 magic `0xC0E5510A`, bytes `0A 51 E5 C0`; u16 header
size 24; u8 unchecked; u8 algorithm 5; u64 uncompressed size; u64 block size). Then for each block: a u32 stored size
and the block, LZMS-compressed, or stored as is when that size equals the block's output size. A call with no output
buffer fails with `ERROR_INSUFFICIENT_BUFFER` and reports the size, as Windows does.

## How ClawDeck uses it

CI builds it (`.github/workflows/build.yml`, job `cabinet-lzms`) and the plugin zip carries it as `bin/cabinet.dll`,
with this source. For FSR 4 on XMX, ClawDeck puts it next to the game's exe as `cabinet.dll`, with Wine's as
`cabinet_wine.dll`. The game's launch environment gets `WINEDLLOVERRIDES=cabinet=n,b`. Both files are in the game's
manifest, and "Uninstall" takes them out. For games set up through Decky Framegen, the launch wrapper takes the DLL
from `~/.local/share/clawdeck/cabinet.dll` (copied there at every plugin start, removed when the plugin is uninstalled).

## Build and test

```
tools/cabinet-lzms/build.sh       # x86_64-w64-mingw32-gcc; out/cabinet.dll, a copy in the repo's bin/
tools/cabinet-lzms/test/run.sh    # wine (64-bit), cc, libwim-dev
```

`test/run.sh` packs text, random data (stored blocks), a mix and an odd size with wimlib's own LZMS compressor
(`test/mkvector.c`, buffered with 1 MiB and 64 KiB blocks, and raw). It unpacks them under Wine through ordinals
40/43/45 (`test/test_cabinet.exe`), compares byte for byte, then checks the export table, the forwards into
`cabinet_wine.dll` and the error codes (`test/test_forward.exe`). CI runs it on every push.

Also checked by hand: the six kernel resources of nitec's B Boost build unpack byte-identical to wimlib's own
decoder, under Wine (168 MB in about 4 s). Those resources aren't redistributable, so they aren't in the repository.

## License

LGPL-2.1-or-later (see `LICENSE`). The LZMS decoder and its helpers in `wimlib/` are by Eric Biggers, from wimlib
commit cd5e231, with their license headers unchanged:

- `lzms_decompress.c` and `lzms_common.c`: LGPL-2.1-or-later.
- `compress_common.c`, `decompress_common.c`, `cpu_features.c` and some headers (`bitops.h`, `compiler.h`,
  `endianness.h`, ...): MIT, as each file says.
- Headers without their own notice: wimlib's `COPYING`, which offers the library under LGPL-2.1-or-later as long as
  it isn't linked with libntfs-3g. This DLL isn't.

All of these are compatible with LGPL-2.1-or-later for the DLL as a whole. (wimlib's library is LGPL-2.1+, not v3:
its GPLv3+ option covers the programs.) The rest of ClawDeck is MIT. The DLL is a separate library that ClawDeck copies
into game folders, never linked into ClawDeck's code. The plugin zip carries this folder's source next to
`bin/cabinet.dll`.
