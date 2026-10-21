#!/bin/bash
# suspend-cycle.sh [cycles] [sleep_s] [awake_s] - suspend/resume soak for the Sleep module. Needs root (rtcwake).
# Each cycle: suspend for sleep_s with an RTC alarm, then stay awake awake_s. Logs CSV to ~/clawdeck-suspend.csv:
#   cycle, ok, seconds asleep, S0ix residency gained (s), battery % before/after, suspend_stats fail count
# A healthy cycle gains S0ix residency close to the time asleep. Paste the CSV into a GitHub issue.
set -u
N="${1:-10}"; SLEEP="${2:-30}"; AWAKE="${3:-15}"
OUT="${CLAWDECK_SUSPEND_LOG:-$HOME/clawdeck-suspend.csv}"
RES=/sys/devices/system/cpu/cpuidle/low_power_idle_system_residency_us
BAT=$(ls -d /sys/class/power_supply/BAT* 2>/dev/null | head -1)
command -v rtcwake >/dev/null || { echo "rtcwake not found"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
echo "cycle,ok,asleep_s,s0ix_gain_s,bat_before,bat_after,fail_count" > "$OUT"
for i in $(seq 1 "$N"); do
  r0=$(cat $RES 2>/dev/null || echo 0); b0=$(cat "$BAT/capacity" 2>/dev/null); t0=$(date +%s)
  if rtcwake -m mem -s "$SLEEP" >/dev/null 2>&1; then ok=1; else ok=0; fi
  t1=$(date +%s); r1=$(cat $RES 2>/dev/null || echo 0); b1=$(cat "$BAT/capacity" 2>/dev/null)
  f=$(cat /sys/power/suspend_stats/fail 2>/dev/null)
  gain=$(( (r1 - r0) / 1000000 ))
  echo "$i,$ok,$((t1 - t0)),$gain,$b0,$b1,$f" | tee -a "$OUT"
  sleep "$AWAKE"
done
echo "log: $OUT"
