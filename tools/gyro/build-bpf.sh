#!/bin/sh
# Builds the Claw 8 EX IMU HID-BPF program from source (tools/gyro/MSI__Claw-ISH-IMU.bpf.c) with udev-hid-bpf's
# headers at a pinned commit, so the zip carries a binary built here, not one handed to us.
# usage: tools/gyro/build-bpf.sh <out.bpf.o>      needs clang, llvm-strip, libbpf headers, git
set -eu
OUT=${1:?out path}
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
git clone -q https://gitlab.freedesktop.org/libevdev/udev-hid-bpf.git "$WORK/uhb"
git -C "$WORK/uhb" checkout -q "${UHB_PIN:-bbaad63}"
clang -O2 -g -target bpf -D__TARGET_ARCH_x86 -I"$WORK/uhb/src/bpf" -I/usr/include/x86_64-linux-gnu \
  -c "$HERE/MSI__Claw-ISH-IMU.bpf.c" -o "$WORK/full.bpf.o"
mkdir -p "$(dirname "$OUT")"
llvm-strip -g -o "$OUT" "$WORK/full.bpf.o"
echo "built $OUT"
