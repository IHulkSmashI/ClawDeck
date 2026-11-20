#!/usr/bin/env bash
# cabinet.dll under Wine: data compressed with wimlib's own LZMS compressor must come back byte for byte through
# CreateDecompressor / Decompress / CloseDecompressor (buffered, several block sizes, incompressible blocks stored
# as is, and raw), and the export table must be right (test_forward). Run build.sh first.
# Needs: wine (64-bit), cc, libwim-dev. Exit status 0 only if everything passed.
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
# the EXIT trap must not change the exit status: under set -e a failing command there ended a run in which every
# test passed with its own status (127 where wineserver is not on PATH)
stop_wine() { [ -z "${wineserver:-}" ] || "$wineserver" -k 2>/dev/null || true; }
work="$(mktemp -d)"; trap 'stop_wine; rm -rf "$work"' EXIT
unset TMPDIR                                     # Wine 9 (Ubuntu) aborts in a fresh prefix with TMPDIR set
export WINEPREFIX="$work/prefix" WINEDEBUG=-all WINEDLLOVERRIDES="cabinet=n,b;mscoree,mshtml="
wine=$(command -v wine || command -v wine64 || ls /usr/lib/wine/wine64 2>/dev/null) || { echo "wine not found"; exit 1; }
wineserver=${WINESERVER:-$(command -v wineserver || true)}      # WINESERVER: the variable Wine itself reads
cc -O2 -Wall -o "$work/mkvector" "$here/test/mkvector.c" -lwim
# the DLL as ClawDeck places it: ours as cabinet.dll, Wine's builtin next to it as cabinet_wine.dll (below)
mkdir -p "$work/game"
cp "$here/out/cabinet.dll" "$here/out/test_cabinet.exe" "$here/out/test_forward.exe" "$work/game/"
# a fresh prefix first (its first start runs wineboot; a test program as that first start can abort), with a time
# limit: an incomplete Wine (Fedora's wine-core without wine-common) makes wineboot retry a missing file forever
first_start_s=120
timeout "$first_start_s" "$wine" cmd /c echo Wine prefix ready 2>/dev/null || true
# not on PATH everywhere (Ubuntu 26.04's wine): then the server this wine started for the prefix, which Wine finds
# next to its own loader, wherever the distribution keeps it
if [ -z "$wineserver" ]; then
  for pid in $(pgrep wineserver); do
    if tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep -qxF "WINEPREFIX=$WINEPREFIX"; then
      wineserver=$(readlink "/proc/$pid/exe" || true); break
    fi
  done
fi
echo "wineserver: ${wineserver:-not found, so no wait for the first start of the prefix}"
status=0; [ -z "$wineserver" ] || timeout "$first_start_s" "$wineserver" -w || status=$?
if [ "$status" = 124 ]; then
  echo "Wine's first start of the prefix did not end within $first_start_s s (an incomplete Wine install?). Wine processes:"
  ps -eo pid,etimes,stat,args | grep -i -E '[w]ine|[.]exe' || true
  exit 1
fi
# Wine's own cabinet.dll: wineboot copies Wine's builtin into the new prefix, wherever the distribution keeps Wine's
# DLLs (a search of /usr and /opt for it took up to 3.5 minutes on GitHub's runners)
wcab="$WINEPREFIX/drive_c/windows/system32/cabinet.dll"
[ -s "$wcab" ] || { echo "Wine's cabinet.dll is not in the new prefix"; exit 1; }
cp "$wcab" "$work/game/cabinet_wine.dll"
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
