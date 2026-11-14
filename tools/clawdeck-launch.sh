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
# say: a "clawdeck:" line to Steam's console and to this launch's log (<runtime>/launch/<appid>.log, one launch per
# file, read by Setup -> Export log). Before this, a bug report could not show why a game started on the stock driver
# (FH5 on a Claw 8 AI+, r/MSIClaw 2026-11-07). Only this script's own lines go there, never the game's output.
# xmx_pkg: the folder of xess-xmx-linux's package (igdext64.dll, run/shim-stamp). ANV_CM_KERNEL_DIR from xmx.env
# points at <package>/kernels; ClawDeck unpacks it one level down (xess-xmx-linux-<date>/xess-xmx-linux-<date>/), so
# a glob of one level found nothing and the shim steps never ran (CONTROL on GE-Proton, 2026-11-07)
xmx_pkg() {
  local d="${ANV_CM_KERNEL_DIR:-}"; d="${d%/kernels}"
  [ -n "$d" ] && [ -f "$d/igdext64.dll" ] && { echo "$d"; return; }
  for d in "$HOME"/.local/share/clawdeck/xess-xmx-linux-*/xess-xmx-linux-* "$HOME"/.local/share/clawdeck/xess-xmx-linux-*; do
    [ -f "$d/igdext64.dll" ] && { echo "$d"; return; }
  done
}
say() { echo "clawdeck: $*" >&2; [ -n "${CLAWDECK_LAUNCH_LOG:-}" ] && echo "clawdeck: $*" >> "$CLAWDECK_LAUNCH_LOG" 2>/dev/null; return 0; }
# ---- second stage: Decky Framegen's fgmod has copied OptiScaler and its FSR 4 into the game, then started us -----
# FSR 4.1.2 on XMX (Per-game: OptiScaler upscaler "FSR 4 XMX"): nitec's files from ~/fsr4-xmx go over fgmod's FSR 4,
# with ClawDeck's own LZMS cabinet.dll (tools/cabinet-lzms; the plugin keeps a copy next to this wrapper) and Wine's
# own cabinet.dll as cabinet_wine.dll next to it. What ClawDeck added is listed in
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
      say "FSR 4 XMX files removed from $dir"
    fi
    src="${CLAWDECK_FSR4_XMX_DIR:-$HOME/fsr4-xmx}"
    cab="${CLAWDECK_CABINET:-$HOME/.local/share/clawdeck/cabinet.dll}"
    if [ -n "${CLAWDECK_FSR4_XMX:-}" ]; then
      if [ -f "$src/amd_fidelityfx_upscaler_dx12.dll" ] && [ -f "$src/fsr-original.dll" ] && [ -f "$cab" ]; then
        : > "$list"
        cp -f "$src/amd_fidelityfx_upscaler_dx12.dll" "$dir/"     # fgmod's own FSR 4 is put back by fgmod each launch
        cp -f "$src/fsr-original.dll" "$dir/" && echo fsr-original.dll >> "$list"
        cp -f "$cab" "$dir/cabinet.dll" && echo cabinet.dll >> "$list"
        wcab="$src/cabinet_wine.dll"
        if [ ! -f "$wcab" ]; then                                 # Wine's own cabinet, from the Proton running this game
          tp="${STEAM_COMPAT_TOOL_PATHS:-}"
          for p in ${tp//:/ }; do
            [ -f "$p/files/lib/wine/x86_64-windows/cabinet.dll" ] && { wcab="$p/files/lib/wine/x86_64-windows/cabinet.dll"; break; }
          done
        fi
        [ -f "$wcab" ] && cp -f "$wcab" "$dir/cabinet_wine.dll" && echo cabinet_wine.dll >> "$list"
        ig=$(xmx_pkg); [ -n "$ig" ] && ig="$ig/igdext64.dll"
        [ -f "$src/igdext64.dll" ] && ig="$src/igdext64.dll"
        if [ -n "$ig" ] && [ ! -f "$dir/igdext64.dll" ]; then cp -f "$ig" "$dir/" && echo igdext64.dll >> "$list"; fi
        say "FSR 4 XMX ($CLAWDECK_FSR4_XMX) into $dir"
      else
        say "FSR 4 XMX: needs amd_fidelityfx_upscaler_dx12.dll and fsr-original.dll in $src, and ClawDeck's $cab; fgmod's FSR 4 stays"
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
export CLAWDECK_LAUNCH_LOG="$RUNTIME/launch/$appid.log"
if mkdir -p "$RUNTIME/launch" 2>/dev/null && : > "$CLAWDECK_LAUNCH_LOG" 2>/dev/null; then
  say "launch $(date '+%F %T'), appid $appid"
  p="${STEAM_COMPAT_TOOL_PATHS:-}"; say "proton ${p%%:*}"
  for a in "$@"; do case "$a" in *.exe|*.EXE) say "exe $a"; break;; esac; done
  say "per-game settings: $([ -f "$envfile" ] && tr '\n' ' ' < "$envfile" || echo none)"
else
  CLAWDECK_LAUNCH_LOG=""
fi
# when this game last started: Per-game compares the XMX driver's self-test with it ("XMX cores used in the last launch")
mkdir -p "$RUNTIME/launch" 2>/dev/null && : > "$RUNTIME/launch/$appid.started" 2>/dev/null
# Debug logs for this one launch (Per-game -> "Debug logs for the next launch", py_modules/clawdeck/debuglaunch.py):
# the marker is removed here, so the next start is normal again. OptiScaler's log lines from the debug launch before
# go back first.
dbg="$RUNTIME/launch/$appid.debug"
restore="$RUNTIME/launch/$appid.debug-restore"
opti_ini_set() {   # opti_ini_set <ini> <key> <value>: one [Log] key of OptiScaler.ini, changed in place
  sed -i "s|^$2=.*|$2=$3|" "$1" 2>/dev/null
}
if [ -f "$restore" ]; then
  while IFS='=' read -r k v; do
    case "$k" in ini) rini="$v";; LogToFile|LogLevel) [ -n "${rini:-}" ] && [ -f "$rini" ] && opti_ini_set "$rini" "$k" "$v";; esac
  done < "$restore"
  rm -f "$restore"
  say "debug launch before: OptiScaler's log settings put back"
fi
if [ -f "$dbg" ]; then
  ofolder=$(sed -n 's/^opti=//p' "$dbg" | head -1)
  rm -f "$dbg"
  ddir="$RUNTIME/launch/debug-$appid"
  rm -rf "$ddir"; mkdir -p "$ddir"
  date +%s > "$ddir/started"
  export PROTON_LOG=1 PROTON_LOG_DIR="$ddir" XESS_LOG=1 IGDEXT_TRACE=1 IGDEXT_XEFG_LOG=1 ANV_CM_DEBUG=1
  oini="$ofolder/OptiScaler.ini"
  if [ -n "$ofolder" ] && [ -f "$oini" ]; then
    { echo "ini=$oini"
      echo "LogToFile=$(sed -n 's/^LogToFile=//p' "$oini" | head -1)"
      echo "LogLevel=$(sed -n 's/^LogLevel=//p' "$oini" | head -1)"; } > "$restore"
    opti_ini_set "$oini" LogToFile true
    opti_ini_set "$oini" LogLevel 1
    echo "$ofolder" > "$ddir/opti_folder"
  fi
  what="Proton log, XeSS shim trace, driver messages"
  [ -f "$ddir/opti_folder" ] && what="$what, OptiScaler log"
  say "debug launch: $what for this launch only ($ddir)"
fi
if [ -f "$envfile" ]; then
  while IFS='=' read -r k v; do
    [ -n "$k" ] && [ "${k#\#}" = "$k" ] || continue
    # keep the user's own DLL overrides: ClawDeck's are added in front, not instead
    if [ "$k" = WINEDLLOVERRIDES ] && [ -n "${WINEDLLOVERRIDES:-}" ]; then v="$v;$WINEDLLOVERRIDES"; fi
    # and the user's own DXVK options (DXVK_CONFIG takes several, separated by ;)
    if [ "$k" = DXVK_CONFIG ] && [ -n "${DXVK_CONFIG:-}" ]; then v="$DXVK_CONFIG;$v"; fi
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
     [ -n "$(find "$STEAM_COMPAT_INSTALL_PATH" -maxdepth 5 \( -iname 'libxess*.dll' -o -iname 'libxell*.dll' -o -iname 'optiscaler.ini' \) -print -quit 2>/dev/null)" ]; then
    want=1
  fi
  if [ "$want" = 1 ]; then
    while IFS='=' read -r k v; do
      [ -n "$k" ] && [ "${k#\#}" = "$k" ] || continue
      export "$k=$v"
    done < "$xmxenv"
    say "XeSS XMX driver for this game (set before the container)"
    # The shim straight from ClawDeck's package, through Wine's Z: drive (the real Linux folder), for every Proton.
    # XeSS, XeSS-FG and FSR 4.1.2 look in INTC_ALT_DRIVER_EXTENSIONS_PATH first; the session file points it at the
    # prefix's driver store, and that copy depends on the Proton: Protons that bundle Intel's igdext64.dll (Proton
    # Experimental, GE-Proton, proton-cachyos) copy theirs over it at each launch, Proton 11.0 has no driver store at
    # all, and a new prefix has nothing yet. Then the game ran on the stock path: no XMX, a black screen with FSR 4.1.2
    # (FH5 on a Claw 8 AI+, 2026-11-07). A folder outside the prefix is the same for every Proton and every prefix.
    shimdir=$(xmx_pkg)
    if [ -n "$shimdir" ]; then
      # the prefix's copy when it is the shim (the path proven on Proton 11.0), else the package folder
      pcopy=$(ls "${STEAM_COMPAT_DATA_PATH:-/nonexistent}"/pfx/drive_c/windows/system32/[dD]river[sS]tore/[fF]ile[rR]epository/igd_faux.inf_1/igdext64.dll 2>/dev/null | head -1)
      if [ -n "$pcopy" ] && cmp -s "$pcopy" "$shimdir/igdext64.dll"; then
        say "XMX shim: this prefix has it"
      else
        real=$(cd "$shimdir" && pwd -P)
        export INTC_ALT_DRIVER_EXTENSIONS_PATH="Z:${real//\//\\}"
        say "XMX shim for any Proton: $INTC_ALT_DRIVER_EXTENSIONS_PATH (no shim in this prefix's driver store)"
      fi
      # GE-Proton overrides that path (C:\Windows\System32) and, like Proton Experimental and proton-cachyos, copies
      # its own bundled igdext64.dll into the prefix at every launch. So the Proton this game runs on gets the shim as
      # its bundled copy, the way xess-xmx-linux's install.sh does it for every Proton it finds (original kept as
      # .stock, its uninstall.sh puts it back; stamped like install.sh's copies, so its watcher leaves it alone).
      # Here at launch it also covers a Proton added since then. A Proton only root can write (a distribution
      # package) can't be changed: said in the launch log, and the Per-game XMX check shows the result.
      tp="${STEAM_COMPAT_TOOL_PATHS:-}"; tp="${tp%%:*}"
      bundled="$tp/files/lib/wine/igdext/x86_64-windows/igdext64.dll"
      if [ -n "$tp" ] && [ -f "$bundled" ] && ! cmp -s "$bundled" "$shimdir/igdext64.dll"; then
        if [ -w "$bundled" ] || chmod u+w "$bundled" 2>/dev/null; then
          if ! grep -aq 'IGDEXT_' "$bundled" && { [ ! -f "$bundled.stock" ] || ! cmp -s "$bundled" "$bundled.stock"; }; then
            chmod u+w "$bundled.stock" 2>/dev/null; cp -p "$bundled" "$bundled.stock"
          fi
          if cp -f "$shimdir/igdext64.dll" "$bundled"; then
            [ -f "$shimdir/run/shim-stamp" ] && touch -r "$shimdir/run/shim-stamp" "$bundled" 2>/dev/null
            say "XMX shim put into this Proton's own igdext64.dll ($tp; original kept as .stock)"
          fi
        else
          say "this Proton can't be changed ($bundled is read-only): it puts Intel's stock igdext64.dll into the game, so no XMX there. Pick another Proton for this game"
        fi
      elif [ -f "$bundled" ]; then
        say "this Proton's own igdext64.dll is already the XMX shim"
      fi
    else
      say "XMX shim: no igdext64.dll in ClawDeck's XeSS XMX package; the prefix's copy is used"
    fi
  else
    say "XMX driver not used: nothing in this game asks for it (no CLAWDECK_XMX=1, no XeSS or OptiScaler found)"
  fi
elif [ ! -f "$xmxenv" ]; then
  say "XMX driver not used: $xmxenv is missing (Setup -> Install XeSS XMX)"
else
  say "XMX driver not used: XMX is off for this game"
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
  # keep the user's own MANGOHUD_CONFIG keys if any were set; else nothing is shown. Not with no_display: MangoHud up
  # to 0.8.4 skips its per-frame update while the HUD is hidden and no log runs, and autostart_log starts from that
  # update, so the log never started and autotune had no frame rate in any game without XeLL (Enshrouded on the Claw
  # 8 EX, 2026-11-12; fixed after 0.8.4 by MangoHud 567d7aa6). An empty layout at zero alpha draws nothing.
  hide="legacy_layout=0,background_alpha=0,alpha=0"
  base=",${MANGOHUD_CONFIG:-$hide},"
  base="${base//,no_display,/,$hide,}"; base="${base#,}"
  export MANGOHUD_CONFIG="${base%,},output_folder=$LOGDIR,log_interval=1000,autostart_log=1,log_duration=0"
  say "MangoHud frame-time log for autotune: $LOGDIR"
elif [ "${CLAWDECK_NO_MANGOHUD:-0}" != 1 ] && [ -f "$RUNTIME/autotune.on" ]; then
  say "no MangoHud: autotune can only read this game's frame rate from XeLL"
fi
# E-cores only for this game (Per-game, CLAWDECK_CORES=e): the game and everything it starts inherit the affinity,
# the P-cores stay free; nothing to undo, it ends with the game. The E-cores on the L3 (Panther Lake: 2-9), not the
# LP-E cores without one (10-13), which are slower; a chip without that split gets all its Atom cores. On Windows
# this needs a reboot (r/MSIClaw 2026-11): on Linux it is per game, live.
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
    say "E-cores only ($list)"
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
