#!/bin/bash
# claw-watch.sh - record what a game does on the Claw while you play, for the ClawDeck test sessions.
#
# Start it in Desktop Mode (it keeps running when you switch to Game Mode: it runs as a systemd user service):
#     bash claw-watch.sh start [minutes]      # default 60 minutes, then it stops by itself
#     bash claw-watch.sh stop
#     bash claw-watch.sh status
# Then play. Back in Desktop Mode:  python3 claw-report.py   (reads the newest recording)
#
# Every second, into ~/clawdeck-test/<date-time>/samples.csv: the running game, package power (RAPL), battery W,
# GPU clock, CPU temperature, the TDP limit, and the frame rate from ClawDeck's MangoHud log. Once per game launch,
# into games/<appid>.txt: the game's environment (XMX driver, DLL overrides, FSR route, OptiScaler settings), what
# ClawDeck put in its folder, and at the end the XeLL and OptiScaler logs. Read-only: it changes nothing.
set -u
OUT_ROOT="$HOME/clawdeck-test"
UNIT=clawdeck-watch

case "${1:-}" in
  start)
    mins="${2:-60}"
    systemctl --user stop "$UNIT" 2>/dev/null
    systemd-run --user --unit="$UNIT" --collect --quiet bash "$(readlink -f "$0")" run "$mins" \
      && echo "recording for $mins minutes into $OUT_ROOT (stop: bash $0 stop). Switch to Game Mode and play."
    exit ;;
  stop) systemctl --user stop "$UNIT" && echo stopped; exit ;;
  status) systemctl --user status "$UNIT" --no-pager | head -5; ls -t "$OUT_ROOT" 2>/dev/null | head -3; exit ;;
  run) ;;
  *) sed -n '2,13p' "$0"; exit 1 ;;
esac

mins="${2:-60}"
dir="$OUT_ROOT/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$dir/games"
RT="$HOME/homebrew/data/ClawDeck"
pkg=/sys/class/powercap/intel-rapl:0/energy_uj
[ -r "$pkg" ] || pkg=/sys/class/powercap/intel-rapl-mmio:0/energy_uj
pl1=$(ls /sys/class/powercap/intel-rapl-mmio:0/constraint_0_power_limit_uw /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw 2>/dev/null | head -1)
gpu=$(ls /sys/class/drm/card*/device/tile0/gt0/freq0/act_freq 2>/dev/null | head -1)
[ -z "$gpu" ] && gpu=$(ls /sys/class/drm/card*/gt_act_freq_mhz 2>/dev/null | head -1)
temp=""
for z in /sys/class/thermal/thermal_zone*; do [ "$(cat "$z/type" 2>/dev/null)" = x86_pkg_temp ] && temp="$z/temp"; done
bat=$(ls -d /sys/class/power_supply/BAT* 2>/dev/null | head -1)

echo "t,game,appid,pkg_w,bat_w,gpu_mhz,cpu_c,pl1_w,fps,frametime_ms" > "$dir/samples.csv"
{ echo "started $(date -Is), $mins min"; uname -r; grep -h '"version"' "$HOME/homebrew/plugins/ClawDeck/package.json" 2>/dev/null
  ls "$HOME/.local/share/clawdeck/" 2>/dev/null | tr '\n' ' '; echo; } > "$dir/session.txt"

game_pid() {      # the running Windows game: a process under steamapps/common ending in .exe (not Proton's own tools)
  for p in $(pgrep -u "$USER" -f 'steamapps/common/.*\.exe' 2>/dev/null); do
    c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)
    case "$c" in *steam.exe*|*explorer.exe*|*wineserver*|*services.exe*|*winedevice*|*plugplay*|*rpcss*|*tabtip*) continue;; esac
    case "$c" in *steamapps/common/Proton*|*SteamLinuxRuntime*) continue;; esac
    echo "$p"; return
  done
}

snapshot() {      # once per launch: what the game was started with
  local p=$1 appid=$2 f="$dir/games/$2.txt"
  [ -f "$f" ] && return
  {
    echo "== $(date -Is) appid $appid pid $p"
    tr '\0' ' ' < /proc/$p/cmdline; echo
    echo "== environment (ClawDeck / XMX / OptiScaler / XeSS)"
    tr '\0' '\n' < /proc/$p/environ 2>/dev/null | grep -E '^(VK_DRIVER_FILES|VKD3D_|ANV_|INTC_|force_vk|WINEDLLOVERRIDES|FSR|FSR_FG|XESS|XESS_FG|IGDEXT|CLAWDECK|FSR4_XMX|Dx12Upscaler|FrameGen_|SteamAppId|STEAM_COMPAT_INSTALL_PATH|PROTON_|MANGOHUD|DXVK|VKD3D)' | sort
    echo "== launch file"; cat "$RT/launch/$appid.env" 2>/dev/null
    gd=$(tr '\0' '\n' < /proc/$p/environ 2>/dev/null | sed -n 's/^STEAM_COMPAT_INSTALL_PATH=//p')
    echo "== game folder: $gd"
    if [ -n "$gd" ]; then
      find "$gd" -maxdepth 5 \( -name '.clawdeck-optiscaler' -o -iname 'OptiScaler.ini' -o -iname 'dinput8.dll' -o -iname 'libxess*.dll' \
        -o -iname 'amd_fidelityfx*.dll' -o -iname 'dxgi.dll' -o -iname 'winmm.dll' -o -iname '.clawdeck-reframework.json' -o -iname 'cabinet*.dll' \) 2>/dev/null | sed "s|$gd/||"
      for m in $(find "$gd" -maxdepth 6 -path '*.clawdeck-optiscaler/manifest.json' 2>/dev/null); do echo "-- $m"; cat "$m"; echo; done
    fi
    echo "== GPU driver files loaded (XMX driver = the package's Mesa)"
    grep -oE '/[^ ]*(libvulkan_intel|mesa)[^ ]*' /proc/$p/maps 2>/dev/null | sort -u | head -5
  } > "$f" 2>&1
}

finish_game() {   # after the game exits: its logs
  local appid=$1 gd=$2 pfx=$3 f="$dir/games/$1.txt"
  {
    echo "== $(date -Is) game ended"
    for l in igdext_xell.log igdext_trace.log; do
      [ -f "$pfx/pfx/drive_c/$l" ] && { echo "-- $l (last 40 lines)"; tail -40 "$pfx/pfx/drive_c/$l"; }
    done
    for o in $(find "$gd" -maxdepth 6 -iname 'OptiScaler.log' 2>/dev/null); do
      echo "-- $o (upscaler, frame generation, errors)"
      grep -iE 'upscaler|xefg|xess|fsr4|fsr 4|framegen|fg output|fginput|error|fail|xell|reflex|fakenvapi|spoof|version' "$o" | tail -60
    done
    [ -f "$pfx/steam-$appid.log" ] && { echo "-- proton log (clawdeck lines)"; grep -iE 'clawdeck|xess-xmx|err:' "$pfx/../steam-$appid.log" | tail -20; }
  } >> "$f" 2>&1
}

e0=$(cat "$pkg" 2>/dev/null || echo 0); t0=$(date +%s%N)
end=$(( $(date +%s) + mins * 60 ))
cur_app="" cur_dir="" cur_pfx=""
while [ "$(date +%s)" -lt "$end" ]; do
  sleep 1
  p=$(game_pid)
  appid="" name=""
  if [ -n "$p" ]; then
    appid=$(tr '\0' '\n' < /proc/$p/environ 2>/dev/null | sed -n 's/^SteamAppId=//p' | head -1)
    name=$(basename "$(tr '\0' '\n' < /proc/$p/cmdline | head -1 | tr '\\' '/')")
    [ -n "$appid" ] && snapshot "$p" "$appid"
    if [ "$appid" != "$cur_app" ]; then
      [ -n "$cur_app" ] && finish_game "$cur_app" "$cur_dir" "$cur_pfx"
      cur_app=$appid
      cur_dir=$(tr '\0' '\n' < /proc/$p/environ 2>/dev/null | sed -n 's/^STEAM_COMPAT_INSTALL_PATH=//p')
      cur_pfx=$(tr '\0' '\n' < /proc/$p/environ 2>/dev/null | sed -n 's/^STEAM_COMPAT_DATA_PATH=//p')
    fi
  elif [ -n "$cur_app" ]; then
    finish_game "$cur_app" "$cur_dir" "$cur_pfx"; cur_app=""
  fi
  e1=$(cat "$pkg" 2>/dev/null || echo 0); t1=$(date +%s%N)
  w=$(awk -v a="$e0" -v b="$e1" -v s="$t0" -v e="$t1" 'BEGIN{d=b-a; if(d<0)d+=2^32; dt=(e-s)/1e9; if(dt>0) printf "%.2f", d/1e6/dt}')
  e0=$e1; t0=$t1
  bw=""
  if [ -n "$bat" ]; then
    i=$(cat "$bat/current_now" 2>/dev/null); v=$(cat "$bat/voltage_now" 2>/dev/null); st=$(cat "$bat/status" 2>/dev/null)
    bw=$(awk -v i="${i:-0}" -v v="${v:-0}" -v s="$st" 'BEGIN{w=i*v/1e12; if(w<0)w=-w; if(w>200)w/=100; if(s=="Discharging")w=-w; printf "%.1f", w}')
  fi
  g=$(cat "$gpu" 2>/dev/null); c=$(( $(cat "$temp" 2>/dev/null || echo 0) / 1000 )); l=$(( $(cat "$pl1" 2>/dev/null || echo 0) / 1000000 ))
  fps="" ft=""
  if [ -n "$appid" ]; then      # ClawDeck's MangoHud log for this game (1 s rows: fps, frametime first)
    ml=$(ls -t "$RT"/mangohud/*.csv 2>/dev/null | head -1)
    [ -n "$ml" ] && read -r fps ft < <(tail -1 "$ml" | awk -F, '{print $1, $2}')
  fi
  echo "$(date +%s),${name//,/ },$appid,$w,$bw,$g,$c,$l,$fps,$ft" >> "$dir/samples.csv"
done
[ -n "$cur_app" ] && finish_game "$cur_app" "$cur_dir" "$cur_pfx"
echo "ended $(date -Is)" >> "$dir/session.txt"
journalctl --user -b --no-pager 2>/dev/null | grep -iE 'clawdeck' | tail -40 > "$dir/user-journal.txt"
cp "$(ls -t "$HOME"/homebrew/logs/ClawDeck/*.log 2>/dev/null | head -1)" "$dir/clawdeck.log" 2>/dev/null
