#!/bin/bash
# clawdeck-launch.sh - per-game launch wrapper. Steam launch options:   /path/to/clawdeck-launch.sh %command%
# Exports what the running game needs from ClawDeck without the plugin touching Steam's launch options itself:
#   - MangoHud frame-time log for autotune (1 s interval, CSV into ClawDeck's runtime folder)
#   - the injector's environment for the mode chosen for this game (WINEDLLOVERRIDES for OptiScaler)
# Env: CLAWDECK_NO_MANGOHUD=1 skips the overlay/log; CLAWDECK_RUNTIME overrides the runtime folder.
set -u
RUNTIME="${CLAWDECK_RUNTIME:-$HOME/homebrew/data/ClawDeck}"
LOGDIR="$RUNTIME/mangohud"
mkdir -p "$LOGDIR" 2>/dev/null
appid="${SteamAppId:-${SteamGameId:-0}}"
# per-game injector mode saved by the plugin (plain file: <runtime>/launch/<appid>.env, KEY=VALUE lines)
envfile="$RUNTIME/launch/$appid.env"
if [ -f "$envfile" ]; then
  while IFS='=' read -r k v; do
    [ -n "$k" ] && [ "${k#\#}" = "$k" ] || continue
    # keep the user's own DLL overrides: ClawDeck's are added in front, not instead
    if [ "$k" = WINEDLLOVERRIDES ] && [ -n "${WINEDLLOVERRIDES:-}" ]; then v="$v;$WINEDLLOVERRIDES"; fi
    export "$k=$v"
  done < "$envfile"
fi
# XeLL driver mode on: the shim appends to its logs in the prefix, so keep only this launch (the plugin reads them)
if [ -n "${IGDEXT_XELL_PROBE:-}" ] && [ -d "${STEAM_COMPAT_DATA_PATH:-/nonexistent}/pfx/drive_c" ]; then
  for f in igdext_trace.log igdext_xell.log; do
    [ -f "$STEAM_COMPAT_DATA_PATH/pfx/drive_c/$f" ] && mv -f "$STEAM_COMPAT_DATA_PATH/pfx/drive_c/$f" "$STEAM_COMPAT_DATA_PATH/pfx/drive_c/$f.prev"
  done
fi
if [ "${CLAWDECK_NO_MANGOHUD:-0}" != 1 ] && command -v mangohud >/dev/null 2>&1; then
  export MANGOHUD=1
  # keep the user's own MANGOHUD_CONFIG keys if any were set; no_display keeps the overlay off unless they want it
  base="${MANGOHUD_CONFIG:-no_display}"
  export MANGOHUD_CONFIG="${base%,},output_folder=$LOGDIR,log_interval=1000,autostart_log=1,log_duration=0"
fi
exec "$@"
