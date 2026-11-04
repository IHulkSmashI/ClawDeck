# cabinet-lzms

A `cabinet.dll` for Proton that adds what Wine's lacks: the Compression API decompressor
(`CreateDecompressor` / `Decompress` / `CloseDecompressor`, ordinals 40–45) for `COMPRESS_ALGORITHM_LZMS`, in
buffered and raw mode. The FSR 4 on XMX upscaler builds from the OptiScaler community unpack their GPU kernels this
way; until now that needed Windows' own `cabinet.dll`.

Everything else (FDI/FCI, `Extract`, ...) is forwarded to `cabinet_wine.dll`, a renamed copy of the running Proton's
builtin `cabinet.dll` placed next to this one, so a game that uses those keeps working.

Use, in the game's folder (where the FSR 4 DLL is): `cabinet.dll` from `out/`, the Proton's
`files/lib/wine/x86_64-windows/cabinet.dll` copied as `cabinet_wine.dll`, and `WINEDLLOVERRIDES=cabinet=n,b` in the
launch options.

Container format (buffered mode): 24-byte header — u32 magic `0xC0E5510A` (bytes `0A 51 E5 C0`), u16 header size 24,
u8 (unchecked), u8 algorithm (5), u64 uncompressed size, u64 block size — then per block a u32 stored size and the
block, LZMS-compressed, or stored as is when that size equals the block's output size.

Tested: `build.sh`, then under Wine with `WINEDLLOVERRIDES=cabinet=n,b`, `test_cabinet.exe` unpacks the six kernel
resources of the "B Boost" FSR 4 XMX build (2026-10-29) byte-identical to wimlib's own decoder (168 MB in ~1.5 s);
`test_forward.exe` resolves the forwarded functions in `cabinet_wine.dll`.

License: LGPL-2.1-or-later. The LZMS decoder is wimlib's (`wimlib/`, by Eric Biggers, LGPL-2.1+ option, see
`wimlib/COPYING`), from wimlib commit cd5e231.
