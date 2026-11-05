#!/usr/bin/env bash
# cabinet.dll under Wine: data compressed with wimlib's own LZMS compressor must come back byte for byte through
# CreateDecompressor / Decompress / CloseDecompressor (buffered, several block sizes, incompressible blocks stored
# as is, and raw), and the export table must be right (test_forward). Run build.sh first.
# Needs: wine (64-bit), cc, libwim-dev. Exit status 0 only if everything passed.
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"; trap 'wineserver -k 2>/dev/null; rm -rf "$work"' EXIT
unset TMPDIR                                     # Wine 9 (Ubuntu) aborts in a fresh prefix with TMPDIR set
export WINEPREFIX="$work/prefix" WINEDEBUG=-all WINEDLLOVERRIDES="cabinet=n,b;mscoree,mshtml="
wine=$(command -v wine || command -v wine64 || ls /usr/lib/wine/wine64 2>/dev/null) || { echo "wine not found"; exit 1; }
cc -O2 -Wall -o "$work/mkvector" "$here/test/mkvector.c" -lwim
# the DLL as ClawDeck places it: ours as cabinet.dll, Wine's builtin next to it as cabinet_wine.dll
mkdir -p "$work/game"
cp "$here/out/cabinet.dll" "$here/out/test_cabinet.exe" "$here/out/test_forward.exe" "$work/game/"
wcab=$(ls /usr/lib/x86_64-linux-gnu/wine/x86_64-windows/cabinet.dll /usr/lib/wine/x86_64-windows/cabinet.dll \
          /opt/wine*/lib*/wine/x86_64-windows/cabinet.dll 2>/dev/null | head -1)
[ -n "$wcab" ] || { echo "Wine's x86_64 cabinet.dll not found"; exit 1; }
cp "$wcab" "$work/game/cabinet_wine.dll"
# a fresh prefix first (its first start runs wineboot; a test program as that first start can abort)
"$wine" cmd /c echo Wine prefix ready 2>/dev/null || true
wineserver -w || true
# inputs: text-like (compresses well), random (blocks stored as is), the two mixed, one odd size
v="$work/game"                                  # vectors next to the exe: plain relative names for Wine
for f in "$here"/wimlib/*.c "$here"/wimlib/wimlib.h; do cat "$f"; done > "$v/text"
while [ "$(stat -c %s "$v/text")" -lt 3000000 ]; do cat "$v/text" "$v/text" > "$v/t2"; mv "$v/t2" "$v/text"; done
head -c 300000 /dev/urandom > "$v/random"
cat "$v/text" "$v/random" "$v/text" > "$v/mixed"
head -c 1234567 "$v/mixed" > "$v/odd"
args=(); names=()
for f in text random mixed odd; do
  for bs in 1048576 65536; do
    "$work/mkvector" "$v/$f" "$bs" "$v/$f.$bs.lzms"
    args+=("$f.$bs.lzms" "$f.$bs.out"); names+=("$f.$bs")
  done
  [ "$f" = random ] && continue                  # raw mode holds only what LZMS shrinks
  "$work/mkvector" "$v/$f" raw "$v/$f.raw"
  args+=("raw:$(stat -c %s "$v/$f"):$f.raw" "$f.rawout"); names+=("$f.raw")
done
cd "$work/game"
"$wine" test_cabinet.exe cabinet.dll "${args[@]}"
fail=0
for n in "${names[@]}"; do
  src="${n%%.*}"; out="$v/$n.out"; [ "${n#*.}" = raw ] && out="$v/$src.rawout"
  if cmp -s "$v/$src" "$out"; then echo "match  $n"; else echo "DIFFER $n"; fail=1; fi
done
"$wine" test_forward.exe cabinet.dll || fail=1
[ "$fail" = 0 ] && echo "cabinet-lzms: all tests passed" || { echo "cabinet-lzms: FAILED"; exit 1; }
