#!/bin/bash
# pacer-priority.sh - test: give a running game's frame-pacing threads real-time priority (VRR + frame generation
# stutter). Streamline's pacer (sl.pacer) and XeFG's present thread time when each generated frame goes out; under
# Wine they run at normal priority and get scheduled late, which VRR shows as a regular hitch (vkd3d-proton #2325).
#   sudo bash pacer-priority.sh list      the game's threads (name, policy, priority)
#   sudo bash pacer-priority.sh on        SCHED_FIFO 50 for the pacing threads (until the game exits)
#   sudo bash pacer-priority.sh off       back to normal
set -u
pids=$(pgrep -f 'S:\\steamapps|\.exe$|\.exe ' | head -40)
want='pacer|present|xefg|fg_|flip|swap|sl\.|dxgi'
for p in $pids; do
  [ -d /proc/$p/task ] || continue
  for t in /proc/$p/task/*; do
    tid=${t##*/}; n=$(cat $t/comm 2>/dev/null)
    case "${1:-list}" in
      list) echo "$p $tid $(chrt -p $tid 2>/dev/null | tr '\n' ' ' | sed 's/pid [0-9]*.s //g') $n" ;;
      on) echo "$n" | grep -qiE "$want" && chrt -f -p 50 $tid && echo "rt: $tid $n" ;;
      off) echo "$n" | grep -qiE "$want" && chrt -o -p 0 $tid && echo "normal: $tid $n" ;;
    esac
  done
done | sort -u
