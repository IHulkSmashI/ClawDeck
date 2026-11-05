#!/usr/bin/env bash
# Builds cabinet.dll (x86-64 Windows, for Proton): the Compression API LZMS decompressor Wine lacks, plus forwards
# of Wine's own cabinet functions to cabinet_wine.dll (a renamed copy of the Proton's builtin cabinet.dll).
# Needs x86_64-w64-mingw32-gcc. Output: tools/cabinet-lzms/out/cabinet.dll (and bin/cabinet.dll) and the two test
# programs; test/run.sh tests them under Wine.
set -eu
cd "$(dirname "$0")"
mkdir -p out
echo '/* wimlib build config */' > out/config.h
CC=x86_64-w64-mingw32-gcc
$CC -O2 -Wall -shared -Iout -Iwimlib -DHAVE_CONFIG_H -o out/cabinet.dll cabinet.c \
  wimlib/lzms_decompress.c wimlib/lzms_common.c wimlib/decompress_common.c wimlib/compress_common.c wimlib/cpu_features.c \
  cabinet.def -static-libgcc -s -Wl,--no-insert-timestamp
$CC -O2 -Wall -o out/test_cabinet.exe test/test_cabinet.c
$CC -O2 -Wall -o out/test_forward.exe test/test_forward.c
sha256sum out/cabinet.dll
# where ClawDeck takes it from when it runs from a source checkout (CI puts the same file in the plugin zip's bin/)
mkdir -p ../../bin && cp out/cabinet.dll ../../bin/cabinet.dll
