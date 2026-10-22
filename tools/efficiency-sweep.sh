#!/bin/bash
# efficiency-sweep.sh - Panther Lake efficiency curve (HARDWARE-FINDINGS §22.1): steps the TDP, measures a game's
# benchmark at each step, writes a CSV. You start the benchmark by hand at each step; the script says when.
#
#   sudo tools/efficiency-sweep.sh                                  # 8 10 12 14 17 20 25 30 35 W, 60 s runs
#   sudo tools/efficiency-sweep.sh --label wukong --seconds 90
#   sudo tools/efficiency-sweep.sh --tdps "10 12 17" --power-saving-points ""
#   sudo tools/efficiency-sweep.sh --auto --settle 30               # a benchmark that loops by itself, no prompts
#
# Before: start the game through ClawDeck's launch wrapper (its MangoHud log is what is measured), fixed preset,
# XeSS on XMX, frame generation OFF, frame limit off, ClawDeck's Autotune off. Everything is reverted at the end.
# Output: docs/measurements/ptl-efficiency-<label>.csv (all options: --help).
set -u
[ "$(id -u)" = 0 ] || { echo "run with sudo: the TDP is a sysfs write"; exit 1; }
exec python3 -I "$(dirname "$(readlink -f "$0")")/efficiency_sweep.py" "$@"
