#!/bin/bash
# sysfs-survey.sh - print everything ClawDeck's hardware table needs from a new machine. Run on the handheld and
# paste the output into a GitHub issue titled "Survey: <model>". Read-only.
echo "== dmi";      for f in sys_vendor product_name board_name bios_version; do printf '%-14s %s\n' "$f" "$(cat /sys/class/dmi/id/$f 2>/dev/null)"; done
echo "== kernel";   uname -r
echo "== cpu";      grep -m1 'model name' /proc/cpuinfo; nproc
echo "== mesa";     for l in /usr/lib64/libvulkan_intel.so /usr/lib/x86_64-linux-gnu/libvulkan_intel.so; do [ -f "$l" ] && { grep -a -o -m1 'Mesa [0-9][0-9.a-z-]*' "$l"; break; }; done
echo "== gpu";      for c in /sys/class/drm/card[0-9]; do echo "$c vendor=$(cat $c/device/vendor 2>/dev/null) driver=$(basename "$(readlink $c/device/driver 2>/dev/null)")"; ls $c/device/tile0/gt*/freq0/ 2>/dev/null | tr '\n' ' '; echo; for f in $c/device/tile0/gt*/freq0/{min,max,cur,rp0,rpe,rpn}_freq; do [ -f "$f" ] && printf '  %s=%s\n' "$(basename $f)" "$(cat $f)"; done; done
echo "== intel_pstate"; for f in max_perf_pct min_perf_pct no_turbo status; do printf '%-14s %s\n' "$f" "$(cat /sys/devices/system/cpu/intel_pstate/$f 2>/dev/null)"; done
echo "== powercap"; for d in /sys/class/powercap/intel-rapl*:*; do [ -d "$d" ] || continue; printf '%s name=%s' "$d" "$(cat $d/name 2>/dev/null)"; for c in $d/constraint_*_name; do [ -f "$c" ] && printf ' %s=%s/%suW' "$(cat $c)" "$(cat ${c%_name}_power_limit_uw 2>/dev/null)" "$(cat ${c%_name}_max_power_uw 2>/dev/null)"; done; echo; done
echo "== msi firmware"; ls /sys/class/firmware-attributes/msi-wmi-platform/attributes/ 2>/dev/null | tr '\n' ' '; echo; for a in ppt_pl1_spl ppt_pl2_sppt ppt_pl3_fppt; do f=/sys/class/firmware-attributes/msi-wmi-platform/attributes/$a; [ -d "$f" ] && printf '  %s current=%s min=%s max=%s\n' "$a" "$(cat $f/current_value)" "$(cat $f/min_value 2>/dev/null)" "$(cat $f/max_value 2>/dev/null)"; done
echo "== hwmon";    for h in /sys/class/hwmon/hwmon*; do printf '%s %s\n' "$h" "$(cat $h/name 2>/dev/null)"; done
for h in /sys/class/hwmon/hwmon*; do [ "$(cat $h/name 2>/dev/null)" = msi_wmi_platform ] || continue; echo "  msi_wmi_platform files:"; for f in $h/{fan,pwm,temp}*; do [ -f "$f" ] && printf '    %s=%s\n' "$(basename $f)" "$(cat $f 2>/dev/null)"; done; done
echo "== battery";  for b in /sys/class/power_supply/*; do printf '%s type=%s' "$b" "$(cat $b/type 2>/dev/null)"; [ -f $b/charge_control_end_threshold ] && printf ' charge_control_end_threshold=%s' "$(cat $b/charge_control_end_threshold)"; [ -f $b/charge_types ] && printf ' charge_types=%s' "$(cat $b/charge_types)"; echo; done
echo "== services"; for u in inputplumber powerstation steamos-manager; do printf '%-16s %s\n' "$u" "$(systemctl is-active $u 2>/dev/null)"; done; systemctl --user is-active steamos-manager 2>/dev/null | sed 's/^/steamos-manager (user) /'
echo "== hhd";      systemctl --user is-active hhd@* 2>/dev/null; systemctl is-active hhd@$(id -un) 2>/dev/null; python3 -c "import importlib.metadata as m; print('hhd', m.version('hhd'))" 2>/dev/null; ls -la /run/hhd/api 2>/dev/null
echo "== display";  cat /sys/class/drm/card*-eDP-*/modes 2>/dev/null | head -5; ls /sys/class/drm/ | grep -E 'card[0-9]-' | tr '\n' ' '; echo
for e in /sys/class/drm/card*-eDP-*/edid; do [ -s "$e" ] && python3 -I -c '
import hashlib, sys
d = open(sys.argv[1], "rb").read(); v = (d[8] << 8) | d[9]
pid = "".join(chr(((v >> s) & 31) + 64) for s in (10, 5, 0)) + "%02X%02X" % (d[11], d[10])
rng = [(d[o+5], d[o+6]) for o in (54, 72, 90, 108) if d[o:o+2] == b"\0\0" and d[o+3] == 0xFD]
print("  panel EDID", pid, len(d), "bytes, VRR range", rng[0] if rng else "?", "sha256", hashlib.sha256(d).hexdigest())' "$e"; done
grep -o 'drm.edid_firmware=[^ ]*\|firmware_class.path=[^ ]*' /proc/cmdline | sed 's/^/  kernel arg /'
echo "== tools";    for t in mangohud gamescope hhdctl ocloc; do printf '%-10s %s\n' "$t" "$(command -v $t || echo missing)"; done
echo "== os";       grep -E '^(NAME|VERSION_ID|VARIANT_ID)=' /etc/os-release; rpm-ostree status -b 2>/dev/null | sed -n 1,6p
