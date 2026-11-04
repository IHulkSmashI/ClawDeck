#!/usr/bin/env bash
# One-time setup for the FSR 4 / XeSS-on-XMX tests (docs/testing/onimusha.md, wukong.md), run as you, not root.
#   1. installs xess-xmx-linux 20261104 into ~/.local/share/clawdeck (removing an older one ClawDeck installed there)
#   2. stages the FSR 4 on XMX files in ~/fsr4-xmx/: the B Boost build (from ~/Downloads), fsr-original.dll (from
#      xess-xmx-linux's ffx/), ClawDeck's cabinet.dll (from ~/Downloads) and the Proton's own as cabinet_wine.dll
# Everything it does is printed. Undo: the package's uninstall.sh, and rm -r ~/fsr4-xmx.
set -euo pipefail
TAG=20261104
URL="https://github.com/HHHHanasak1/xess-xmx-linux/releases/download/$TAG/xess-xmx-linux-$TAG.zip"
ROOT="$HOME/.local/share/clawdeck"
PKG="$ROOT/xess-xmx-linux-$TAG"
DL="$HOME/Downloads"
OUT="$HOME/fsr4-xmx"
[ "$(id -u)" -ne 0 ] || { echo "Run this as yourself, not with sudo."; exit 1; }

echo "== 1. xess-xmx-linux $TAG"
if [ -x "$PKG/install.sh" ]; then
    echo "already unpacked in $PKG"
else
    # older packages, also one folder deeper (ClawDeck 0.1 unpacked the release's top folder inside its own)
    for old in "$ROOT"/*/uninstall.sh "$ROOT"/*/*/uninstall.sh; do
        [ -f "$old" ] || continue
        d=$(dirname "$old"); case "$d" in "$PKG"|"$PKG"/*) continue;; esac
        echo "removing the older package in $d (its uninstall.sh)"; (cd "$d" && bash ./uninstall.sh)
    done
    mkdir -p "$ROOT"; tmp=$(mktemp -d)
    echo "downloading $URL"; curl -fL --progress-bar -o "$tmp/pkg.zip" "$URL"
    mkdir -p "$PKG"; (cd "$PKG" && unzip -q "$tmp/pkg.zip")
    rm -rf "$tmp"
    # the zip may hold one top folder
    if [ ! -f "$PKG/install.sh" ]; then inner=$(dirname "$(find "$PKG" -maxdepth 2 -name install.sh | head -1)"); [ -n "$inner" ] && mv "$inner"/* "$PKG"/ ; fi
fi
echo "running install.sh (takes a minute)"; (cd "$PKG" && bash ./install.sh)

echo; echo "== 2. FSR 4 on XMX files in $OUT"
mkdir -p "$OUT"
dll=""
for f in "$DL/amd_fidelityfx_upscaler_dx12.dll" "$DL"/amd_fidelityfx_upscaler_dx12_b_boost/amd_fidelityfx_upscaler_dx12.dll; do [ -f "$f" ] && dll=$f && break; done
if [ -z "$dll" ]; then
    z=$(ls "$DL"/amd_fidelityfx_upscaler_dx12_b_boost*.7z 2>/dev/null | head -1 || true)
    [ -n "$z" ] || { echo "!! B Boost .7z not found in $DL"; exit 1; }
    tmp=$(mktemp -d)
    if command -v 7z >/dev/null; then 7z x -y -o"$tmp" "$z" >/dev/null
    elif command -v 7za >/dev/null; then 7za x -y -o"$tmp" "$z" >/dev/null
    elif command -v bsdtar >/dev/null; then bsdtar -xf "$z" -C "$tmp"
    else echo "!! no 7z here: in Dolphin right-click $z → Extract → Extract here, then run this again"; exit 1; fi
    dll=$(find "$tmp" -iname 'amd_fidelityfx_upscaler_dx12.dll' | head -1)
    [ -n "$dll" ] || { echo "!! the .7z had no amd_fidelityfx_upscaler_dx12.dll"; exit 1; }
fi
cp -f "$dll" "$OUT/amd_fidelityfx_upscaler_dx12.dll"; echo "B Boost build: $(stat -c %s "$OUT/amd_fidelityfx_upscaler_dx12.dll") bytes"

orig=$(find "$PKG" -ipath '*ffx/amd_fidelityfx_upscaler_dx12.dll' | head -1)
[ -n "$orig" ] || { echo "!! $PKG has no ffx/amd_fidelityfx_upscaler_dx12.dll (install.sh fetches it: check its output above)"; exit 1; }
cp -f "$orig" "$OUT/fsr-original.dll"; echo "fsr-original.dll from $orig"

[ -f "$DL/cabinet.dll" ] || { echo "!! ClawDeck's cabinet.dll not in $DL"; exit 1; }
cp -f "$DL/cabinet.dll" "$OUT/cabinet.dll"; echo "cabinet.dll (ClawDeck's) sha256 $(sha256sum "$OUT/cabinet.dll" | cut -c1-16)"

wcab=""
for p in "$HOME/.local/share/Steam/steamapps/common/Proton - Experimental" "$HOME"/.local/share/Steam/compatibilitytools.d/* "$HOME"/.local/share/Steam/steamapps/common/Proton*; do
    [ -f "$p/files/lib/wine/x86_64-windows/cabinet.dll" ] && wcab="$p/files/lib/wine/x86_64-windows/cabinet.dll" && break
done
[ -n "$wcab" ] || { echo "!! no Proton with files/lib/wine/x86_64-windows/cabinet.dll found"; exit 1; }
cp -f "$wcab" "$OUT/cabinet_wine.dll"; echo "cabinet_wine.dll from $wcab"

echo; ls -l "$OUT"
echo
echo "Done. Now LOG OUT and back in (Steam → Power → Switch to Desktop / log out), so games pick up the driver."
echo "Active package (must be $TAG only): $(grep -o "xess-xmx-linux-[0-9]*" ~/.config/environment.d/50-xess-xmx.conf 2>/dev/null | sort -u | tr '\n' ' ')"
echo "Then: cat ~/.cache/xess-xmx/driver-status   and the O1 run in docs/testing/onimusha.md."
