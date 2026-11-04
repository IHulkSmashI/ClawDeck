#!/usr/bin/env bash
# Read-only report after a game run: what xess-xmx-linux (XMX, low latency, XeLL latency), OptiScaler and Decky
# Framegen did in that game's last launch. Changes nothing.
#   tools/game-report.sh                 Black Myth: Wukong Benchmark Tool (3132990) or the game (2358720)
#   tools/game-report.sh <appid>         any Steam game
#   tools/game-report.sh onimusha        any installed game whose name contains this (case-insensitive)
# Paste the output into the GitHub issue or the chat.
set -u
ids=("$@")
[ $# -eq 0 ] && ids=(3132990 2358720)

libs=("$HOME/.local/share/Steam" "$HOME/.steam/steam")
vdf="$HOME/.local/share/Steam/steamapps/libraryfolders.vdf"
[ -f "$vdf" ] && while IFS= read -r p; do libs+=("$p"); done < <(sed -n 's/^\s*"path"\s*"\(.*\)"/\1/p' "$vdf")

say() { printf '\n== %s\n' "$*"; }
last() { # last N matching lines of a file, or a note
    local f=$1 pat=$2 n=${3:-6}
    if [ -f "$f" ]; then grep -aE "$pat" "$f" | tail -n "$n"; else echo "(no $(basename "$f"))"; fi
}

say "system"
grep -E '^(NAME|VERSION)=' /etc/os-release 2>/dev/null
uname -r
for d in ~/.local/share/xess-xmx-linux* ~/xess-xmx-linux*; do [ -d "$d" ] && echo "xess-xmx-linux: $d"; done
[ -f ~/.cache/xess-xmx/driver-status ] && { echo "driver-status:"; sed 's/^/  /' ~/.cache/xess-xmx/driver-status | head -5; }
[ -f ~/fgmod/install-manifest.json ] && echo "Decky Framegen OptiScaler: $(sed -n 's/.*"version"\s*:\s*"\([^"]*\)".*/\1/p' ~/fgmod/install-manifest.json | head -1)"

# names -> app ids, from the installed games' manifests
resolved=()
for a in "${ids[@]}"; do
    if [[ $a =~ ^[0-9]+$ ]]; then resolved+=("$a"); continue; fi
    for l in "${libs[@]}"; do
        for m in "$l"/steamapps/appmanifest_*.acf; do
            [ -f "$m" ] || continue
            grep -qiE "^\s*\"name\"\s*\".*$a" "$m" && resolved+=("$(basename "$m" .acf | sed 's/appmanifest_//')")
        done
    done
done
mapfile -t ids < <(printf '%s\n' "${resolved[@]}" | awk 'NF && !seen[$0]++')

for id in "${ids[@]}"; do
    pfx=""; game=""
    for l in "${libs[@]}"; do
        [ -d "$l/steamapps/compatdata/$id/pfx" ] && pfx="$l/steamapps/compatdata/$id/pfx"
        m="$l/steamapps/appmanifest_$id.acf"
        [ -f "$m" ] && game="$l/steamapps/common/$(sed -n 's/^\s*"installdir"\s*"\(.*\)"/\1/p' "$m")"
    done
    [ -z "$pfx" ] && [ -z "$game" ] && continue
    say "app $id"
    echo "prefix: ${pfx:-not found}"; echo "game:   ${game:-not found}"
    env="${CLAWDECK_RUNTIME:-$HOME/homebrew/data/ClawDeck}/launch/$id.env"; [ -f "$env" ] && { echo "ClawDeck launch env:"; sed 's/^/  /' "$env" | grep -v '^  #'; }

    if [ -n "$pfx" ]; then
        say "XMX path (igdext_trace.log)"
        last "$pfx/drive_c/igdext_trace.log" 'fallback|XMX|self-test|kernel' 6
        say "low latency (igdext_xell.log)"
        last "$pfx/drive_c/igdext_xell.log" 'low latency:|auto: frame generation' 3
        last "$pfx/drive_c/igdext_xell.log" 'auto: waited in' 3
        say "XeLL latency, simulation start -> present end"
        last "$pfx/drive_c/igdext_xell.log" 'xell latency:' 5
        say "rendered frame rate (xellSleep, one line per 100 rendered frames)"
        last "$pfx/drive_c/igdext_xell.log" 'xellSleep stats' 5
        [ -f "$pfx/drive_c/compile_failed.txt" ] && echo "!! compile_failed.txt present"
    fi
    if [ -n "$game" ] && [ -d "$game" ]; then
        say "OptiScaler in the game folder"
        log=$(find "$game" -maxdepth 6 -iname 'OptiScaler.log' 2>/dev/null | head -1)
        ini=$(find "$game" -maxdepth 6 -iname 'OptiScaler.ini' 2>/dev/null | head -1)
        echo "ini: ${ini:-none}"; echo "log: ${log:-none}"
        [ -n "$ini" ] && grep -aE '^(Dx12Upscaler|FrameGen_Enabled|FGOutput|FrameGen_FGOutput|FGInput|Fsr4ForceEnableInt8|Dxgi)=' "$ini" | head
        [ -n "$log" ] && grep -aiE 'version|upscaler|xefg|fsr4|error' "$log" | tail -n 12
        say "FidelityFX DLLs (FSR 4 on XMX: a large amd_fidelityfx_upscaler_dx12.dll and fsr-original.dll)"
        find "$game" -maxdepth 6 \( -iname 'amd_fidelityfx*.dll' -o -iname 'fsr-original.dll' -o -iname 'cabinet.dll' -o -iname 'libxess*.dll' -o -iname 'libxell.dll' \) -printf '%10s  %p\n' 2>/dev/null | head -20
    fi
    [ -f "$HOME/steam-$id.log" ] && { say "Proton log (PROTON_LOG=1): xess-xmx lines"; grep -aE 'xess-xmx|XESS|FSR|igdext' "$HOME/steam-$id.log" | tail -n 15; }
done
exit 0
