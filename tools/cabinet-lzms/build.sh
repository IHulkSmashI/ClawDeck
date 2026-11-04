#!/usr/bin/env bash
# Builds cabinet.dll (x86-64 Windows, for Proton): the Compression API LZMS decompressor Wine lacks, plus forwards
# of Wine's own cabinet functions to cabinet_wine.dll (a renamed copy of the Proton's builtin cabinet.dll).
# Needs x86_64-w64-mingw32-gcc. Output: tools/cabinet-lzms/out/cabinet.dll
set -eu
cd "$(dirname "$0")"
mkdir -p out
echo '/* wimlib build config */' > out/config.h
x86_64-w64-mingw32-gcc -O2 -shared -Iout -Iwimlib -DHAVE_CONFIG_H -o out/cabinet.dll cabinet.c \
  wimlib/lzms_decompress.c wimlib/lzms_common.c wimlib/decompress_common.c wimlib/compress_common.c wimlib/cpu_features.c \
  cabinet.def -static-libgcc
x86_64-w64-mingw32-gcc -O2 -o out/test_cabinet.exe test_cabinet.c
x86_64-w64-mingw32-gcc -O2 -o out/test_forward.exe test_forward.c
sha256sum out/cabinet.dll
