#!/bin/bash
# clawdeck-launch.sh - per-game launch wrapper. Steam launch options:   /path/to/clawdeck-launch.sh %command%
# Exports what the running game needs from ClawDeck without the plugin touching Steam's launch options itself:
#   - MangoHud frame-time log for autotune (1 s interval, CSV into ClawDeck's runtime folder)
#   - the per-game environment the plugin saved (XeSS XMX off, frame generation multiplier, FSR on XeSS, XeSS
#     latest, low latency off, OptiScaler presets for Decky Framegen's fgmod)
#   - IGDEXT_XELL_LOG=1, so the XeSS shim logs XeLL's per-rendered-frame timing (autotune counts rendered frames,
#     not generated ones) and what its low latency did (the Per-game panel reads it)
# Chains with other wrappers: "clawdeck-launch.sh ~/fgmod/fgmod %command%" exports first, then runs fgmod.
# Env: CLAWDECK_NO_MANGOHUD=1 skips the overlay/log; CLAWDECK_RUNTIME overrides the runtime folder.
set -u
# ---- second stage: Decky Framegen's fgmod has copied OptiScaler and its FSR 4 into the game, then started us -----
# FSR 4.1.1 on XMX (Per-game: OptiScaler upscaler "FSR 4 XMX"): nitec's files from ~/fsr4-xmx go over fgmod's FSR 4,
# with Wine's own cabinet.dll as cabinet_wine.dll next to the LZMS one. What ClawDeck added is listed in
# .clawdeck-fsr4xmx so the next launch without FSR 4 XMX removes it again (fgmod puts its own FSR 4 back every launch).
if [ "${CLAWDECK_STAGE:-}" = post ]; then
  unset CLAWDECK_STAGE
  dir=""
  for a in "$@"; do case "$a" in *.exe|*.EXE) dir=$(dirname "$a"); break;; esac; done
  [ -z "$dir" ] && dir="${STEAM_COMPAT_INSTALL_PATH:-}"
  if [ -n "$dir" ] && [ -d "$dir" ]; then
    ue=$(find "$dir" -maxdepth 4 \( -iname '*-Win64-Shipping.exe' -o -iname '*-WinGDK-Shipping.exe' \) 2>/dev/null | head -1)
    [ -n "$ue" ] && dir=$(dirname "$ue")
    list="$dir/.clawdeck-fsr4xmx"
    if [ -f "$list" ] && [ -z "${CLAWDECK_FSR4_XMX:-}" ]; then
      while read -r f; do [ -n "$f" ] && rm -f "$dir/$f"; done < "$list"
      rm -f "$list"
      echo "clawdeck: FSR 4 XMX files removed from $dir" >&2
    fi
    src="${CLAWDECK_FSR4_XMX_DIR:-$HOME/fsr4-xmx}"
    if [ -n "${CLAWDECK_FSR4_XMX:-}" ]; then
      if [ -f "$src/amd_fidelityfx_upscaler_dx12.dll" ] && [ -f "$src/fsr-original.dll" ] && [ -f "$src/cabinet.dll" ]; then
        : > "$list"
        cp -f "$src/amd_fidelityfx_upscaler_dx12.dll" "$dir/"     # fgmod's own FSR 4 is put back by fgmod each launch
        for f in fsr-original.dll cabinet.dll; do cp -f "$src/$f" "$dir/" && echo "$f" >> "$list"; done
        wcab="$src/cabinet_wine.dll"
        if [ ! -f "$wcab" ]; then                                 # Wine's own cabinet, from the Proton running this game
          for p in ${STEAM_COMPAT_TOOL_PATHS//:/ }; do
            [ -f "$p/files/lib/wine/x86_64-windows/cabinet.dll" ] && { wcab="$p/files/lib/wine/x86_64-windows/cabinet.dll"; break; }
          done
        fi
        [ -f "$wcab" ] && cp -f "$wcab" "$dir/cabinet_wine.dll" && echo cabinet_wine.dll >> "$list"
        ig=$(ls -d "$HOME"/.local/share/clawdeck/xess-xmx-linux-*/igdext64.dll 2>/dev/null | tail -1)
        [ -f "$src/igdext64.dll" ] && ig="$src/igdext64.dll"
        if [ -n "$ig" ] && [ ! -f "$dir/igdext64.dll" ]; then cp -f "$ig" "$dir/" && echo igdext64.dll >> "$list"; fi
        echo "clawdeck: FSR 4 XMX ($CLAWDECK_FSR4_XMX) into $dir" >&2
      else
        echo "clawdeck: FSR 4 XMX: $src needs amd_fidelityfx_upscaler_dx12.dll, fsr-original.dll and cabinet.dll; fgmod's FSR 4 stays" >&2
      fi
    fi
  fi
  exec "$@"
fi
RUNTIME="${CLAWDECK_RUNTIME:-$HOME/homebrew/data/ClawDeck}"
LOGDIR="$RUNTIME/mangohud"
mkdir -p "$LOGDIR" 2>/dev/null
appid="${SteamAppId:-${SteamGameId:-0}}"
# per-game settings saved by the plugin (plain file: <runtime>/launch/<appid>.env, KEY=VALUE lines)
envfile="$RUNTIME/launch/$appid.env"
# when this game last started: Per-game compares the XMX driver's self-test with it ("XMX cores used in the last launch")
mkdir -p "$RUNTIME/launch" 2>/dev/null && : > "$RUNTIME/launch/$appid.started" 2>/dev/null
if [ -f "$envfile" ]; then
  while IFS='=' read -r k v; do
    [ -n "$k" ] && [ "${k#\#}" = "$k" ] || continue
    # keep the user's own DLL overrides: ClawDeck's are added in front, not instead
    if [ "$k" = WINEDLLOVERRIDES ] && [ -n "${WINEDLLOVERRIDES:-}" ]; then v="$v;$WINEDLLOVERRIDES"; fi
    export "$k=$v"
  done < "$envfile"
fi
# The XeSS XMX driver for this game, set HERE, before Steam's container starts: pressure-vessel builds the
# container's Vulkan driver list (VK_DRIVER_FILES) from this environment, and Proton's user_settings (the scope
# script) can't change a variable that is already set. Set only there, the game kept the stock driver: no XMX for
# XeSS, XeSS-FG or FSR 4.1.2 (CONTROL on the Claw 8 EX, 2026-11-04: /run/host/usr/lib64/libvulkan_intel.so loaded).
xmxenv="$HOME/.local/share/clawdeck/xmx.env"
if [ -f "$xmxenv" ] && [ "${CLAWDECK_XMX:-}" != 0 ]; then
  want="${CLAWDECK_XMX:-}"
  [ -z "$want" ] && [ -f "$HOME/.local/share/clawdeck/xmx-every-game" ] && want=1
  if [ -z "$want" ] && [ -d "${STEAM_COMPAT_INSTALL_PATH:-/nonexistent}" ] && \
     [ -n "$(find "$STEAM_COMPAT_INSTALL_PATH" -maxdepth 5 \( -iname 'libxess*.dll' -o -iname 'optiscaler.ini' \) -print -quit 2>/dev/null)" ]; then
    want=1
  fi
  if [ "$want" = 1 ]; then
    while IFS='=' read -r k v; do
      [ -n "$k" ] && [ "${k#\#}" = "$k" ] || continue
      export "$k=$v"
    done < "$xmxenv"
    echo "clawdeck: XeSS XMX driver for this game (set before the container)" >&2
  fi
fi
if [ -z "${IGDEXT_XELL_LOG:-}" ]; then export IGDEXT_XELL_LOG=1; fi
# the shim appends to its logs in the prefix, so keep only this launch (the plugin reads them)
if [ -n "${IGDEXT_XELL_LOG:-}" ] && [ -d "${STEAM_COMPAT_DATA_PATH:-/nonexistent}/pfx/drive_c" ]; then
  for f in igdext_trace.log igdext_xell.log; do
    [ -f "$STEAM_COMPAT_DATA_PATH/pfx/drive_c/$f" ] && mv -f "$STEAM_COMPAT_DATA_PATH/pfx/drive_c/$f" "$STEAM_COMPAT_DATA_PATH/pfx/drive_c/$f.prev"
  done
fi
# MangoHud's frame-time log is only for Autotune: inject it only while Autotune is on (<runtime>/autotune.on, written
# by the plugin). It used to go into every wrapped game, on top of Steam's own overlay (mangoapp) - one more layer in
# every present for nothing when Autotune is off (audit 2026-11-05)
if [ "${CLAWDECK_NO_MANGOHUD:-0}" != 1 ] && [ -f "$RUNTIME/autotune.on" ] && command -v mangohud >/dev/null 2>&1; then
  export MANGOHUD=1
  # keep the user's own MANGOHUD_CONFIG keys if any were set; no_display keeps the overlay off unless they want it
  base="${MANGOHUD_CONFIG:-no_display}"
  export MANGOHUD_CONFIG="${base%,},output_folder=$LOGDIR,log_interval=1000,autostart_log=1,log_duration=0"
fi
# E-cores only for this game (Per-game, CLAWDECK_CORES=e): the game and everything it starts inherit the affinity,
# the P-cores stay free; nothing to undo, it ends with the game. The E-cores on the L3 (Panther Lake: 2-9), not the
# LP-E cores without one (10-13), which are slower; a chip without that split gets all its Atom cores. On Windows
# this needs a reboot (ClawTweaks' author, r/MSIClaw 2026-11): on Linux it is per game, live.
pre=()
if [ "${CLAWDECK_CORES:-}" = e ] && command -v taskset >/dev/null 2>&1 && [ -r /sys/devices/cpu_atom/cpus ]; then
  list=""; all=""
  for part in $(tr ',' ' ' < /sys/devices/cpu_atom/cpus); do
    a=${part%-*}; b=${part#*-}
    for c in $(seq "$a" "$b"); do
      all="$all,$c"
      [ -e "/sys/devices/system/cpu/cpu$c/cache/index3" ] && list="$list,$c"
    done
  done
  list=${list#,}; [ -z "$list" ] && list=${all#,}
  if [ -n "$list" ]; then
    echo "clawdeck: E-cores only ($list)" >&2
    pre=(taskset -c "$list")
  fi
fi
# Decky Framegen's fgmod next: run it with ClawDeck's second stage in front of the game, so files ClawDeck adds
# (FSR 4 XMX) go in after fgmod's own and anything left from an earlier launch is cleaned up
if [ $# -ge 2 ] && [ "$(basename "$1")" = fgmod ]; then
  fg="$1"; shift
  export CLAWDECK_STAGE=post
  exec "${pre[@]}" "$fg" "$0" "$@"
fi
exec "${pre[@]}" "$@"
