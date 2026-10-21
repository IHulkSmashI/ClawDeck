#!/bin/bash
# power-profile.sh - battery / performance / dock power profiles for Intel Claws without the plugin (workstream 2).
# Same values as ClawDeck's hw.py PROFILES for the Claw 8 EX. Needs root for sysfs; TDP goes through Handheld Daemon.
#   sudo tools/power-profile.sh battery|performance|dock|reset [--dry-run]
#   tools/power-profile.sh status
# Nothing persists across reboot. 'reset' puts HHD back on its Balanced mode and the CPU/GPU limits to hardware range.
set -u
PROFILE="${1:-status}"; DRY=0; [ "${2:-}" = "--dry-run" ] && DRY=1
PSTATE=/sys/devices/system/cpu/intel_pstate
FREQ=$(ls -d /sys/class/drm/card[0-9]/device/tile0/gt0/freq0 2>/dev/null | head -1)

w() {   # w <path> <value>
  [ -e "$1" ] || { echo "  skip $1 (not present)"; return 0; }
  if [ $DRY = 1 ]; then echo "  dry-run: $1 <- $2 (now $(cat "$1"))"; else echo "$2" > "$1" && echo "  $1 = $2"; fi
}
hhd() { # hhd KEY=VAL ...
  command -v hhdctl >/dev/null || { echo "  hhdctl missing: TDP not set (install/upgrade Handheld Daemon)"; return 0; }
  if [ $DRY = 1 ]; then echo "  dry-run: hhdctl set $*"; else hhdctl set "$@" >/dev/null && echo "  hhd: $*"; fi
}
gpu_range() {  # back to the hardware range (max first, so min <= max holds at every step)
  [ -n "$FREQ" ] || { echo "  skip GPU clocks (no xe freq0 directory)"; return 0; }
  w "$FREQ/max_freq" "$(cat $FREQ/rp0_freq)"; w "$FREQ/min_freq" "$(cat $FREQ/rpn_freq)"
}

case "$PROFILE" in
  status)
    echo "TDP: $(hhdctl get tdp.msi.tdp.mode tdp.msi.tdp.custom.tdp --values 2>/dev/null | tr '\n' ' ')"
    echo "CPU: max_perf_pct=$(cat $PSTATE/max_perf_pct 2>/dev/null) no_turbo=$(cat $PSTATE/no_turbo 2>/dev/null)"
    [ -n "$FREQ" ] && echo "GPU: $(cat $FREQ/min_freq)-$(cat $FREQ/max_freq) MHz (hw $(cat $FREQ/rpn_freq)-$(cat $FREQ/rp0_freq), now $(cat $FREQ/cur_freq))"
    exit 0 ;;
  battery)     tdp=20; cpu=80 ;;
  performance) tdp=30; cpu=100 ;;
  dock)        tdp=35; cpu=100 ;;
  reset)       tdp=; cpu=100 ;;
  *) echo "usage: $0 battery|performance|dock|reset|status [--dry-run]"; exit 1 ;;
esac
[ "$(id -u)" = 0 ] || [ $DRY = 1 ] || { echo "run with sudo (or add --dry-run)"; exit 1; }
echo "== $PROFILE"
if [ -n "$tdp" ]; then hhd tdp.msi.tdp.mode=custom tdp.msi.tdp.custom.tdp=$tdp tdp.msi.tdp.custom.boost=true
else hhd tdp.msi.tdp.mode=balanced; fi
w $PSTATE/max_perf_pct $cpu
w $PSTATE/no_turbo 0
gpu_range
exit 0
