#!/bin/bash
# claw-reset.sh - take everything ClawDeck (and the test setup) put on this Claw back out, for a clean first-run test.
#
#     bash claw-reset.sh            show what it would remove (nothing is changed)
#     bash claw-reset.sh --do       remove it (asks for sudo once), then reboot
#
# Removes: ClawDeck's OptiScaler and REFramework from every game it set up (game files put back), the OptiScaler
# download, the per-game XMX files in every Proton, xess-xmx-linux (its own uninstall.sh), ClawDeck's launch files and
# settings, the VRR screen fix (kernel argument + file), the face-button fix, the split-lock log fix, the overlay
# watts rule, Decky Framegen, and ClawDeck itself. Steam's launch options are left: the launch wrapper file stays as a
# pass-through, so a game whose options still name it starts normally.
set -u
DO=0; [ "${1:-}" = --do ] && DO=1
say() { echo "• $*"; }
run() { if [ $DO = 1 ]; then "$@" || echo "  (failed: $*)"; else echo "  would run: $*"; fi; }

PLUG="$HOME/homebrew/plugins"
DATA="$HOME/.local/share/clawdeck"
STEAM="$HOME/.local/share/Steam"

if [ $DO = 1 ]; then sudo -v || exit 1; fi

say "OptiScaler / REFramework that ClawDeck put into games"
for m in $(find "$STEAM/steamapps/common" /run/media/*/*/SteamLibrary/steamapps/common -maxdepth 7 -path '*/.clawdeck-optiscaler/manifest.json' 2>/dev/null); do
  dir=$(dirname "$(dirname "$m")")
  echo "  $dir"
  if [ $DO = 1 ]; then
    python3 -I - "$dir" <<'PY'
import json, os, shutil, sys
d = sys.argv[1]; st = os.path.join(d, ".clawdeck-optiscaler"); m = json.load(open(os.path.join(st, "manifest.json")))
for rel in m.get("added", []):
    p = os.path.join(d, rel)
    if os.path.isfile(p): os.remove(p)
for rel in m.get("backed", []):
    b = os.path.join(st, "backup", rel)
    if os.path.exists(b): os.replace(b, os.path.join(d, rel))
for sub in ("D3D12_Optiscaler", "plugins"):
    try: os.rmdir(os.path.join(d, sub))
    except OSError: pass
shutil.rmtree(st, ignore_errors=True)
print("    OptiScaler out, game files back")
PY
    if [ -f "$dir/.clawdeck-reframework.json" ]; then rm -f "$dir/dinput8.dll" "$dir/.clawdeck-reframework.json"; echo "    REFramework out"; fi
  fi
done
for f in $(find "$STEAM/steamapps/common" -maxdepth 7 -name '.clawdeck-fsr4xmx' 2>/dev/null); do
  d=$(dirname "$f"); echo "  FSR 4 XMX files in $d"
  [ $DO = 1 ] && { while read -r x; do [ -n "$x" ] && rm -f "$d/$x"; done < "$f"; rm -f "$f"; }
done

say "per-game XMX files in Proton (user_settings.user.py written by ClawDeck)"
for p in "$STEAM"/steamapps/common/Proton* "$STEAM"/compatibilitytools.d/*; do
  u="$p/user_settings.user.py"
  if [ -f "$u" ] && grep -q "clawdeck xmx scope" "$u"; then
    echo "  $p"
    run rm -f "$u"
    [ -f "$p/user_settings.clawdeck-prev.py" ] && run mv "$p/user_settings.clawdeck-prev.py" "$u"
  fi
done

say "xess-xmx-linux (its own uninstaller) and the parked session file"
for u in "$DATA"/xess-xmx-linux-*/uninstall.sh "$DATA"/xess-xmx-linux-*/*/uninstall.sh; do
  [ -f "$u" ] && { echo "  $u"; run bash "$u"; }
done
for f in "$HOME/.config/environment.d/50-xess-xmx.conf" "$HOME/.config/environment.d/50-xess-xmx.conf.clawdeck-per-game" "$HOME/50-xess-xmx.conf.off"; do
  [ -e "$f" ] && { echo "  $f"; run rm -f "$f"; }
done
run systemctl --user unset-environment VK_DRIVER_FILES VKD3D_DISABLE_EXTENSIONS ANV_CM_KERNEL_DIR ANV_CM_HELPER force_vk_vendor INTC_ALT_DRIVER_EXTENSIONS_PATH

say "ClawDeck's data: OptiScaler download, XMX settings, launch files, settings, logs"
for d in "$DATA" "$HOME/homebrew/data/ClawDeck" "$HOME/homebrew/settings/ClawDeck"; do
  [ -e "$d" ] && { echo "  $d"; run sudo rm -rf "$d"; }
done
# the launch wrapper stays as a pass-through, so games whose launch options name it still start
if [ $DO = 1 ]; then
  mkdir -p "$DATA"; printf '#!/bin/sh\nexec "$@"\n' > "$DATA/clawdeck-launch.sh"; chmod 755 "$DATA/clawdeck-launch.sh"
fi

say "system changes (root)"
# the VRR fix in the order ClawDeck's own undo uses: the initramfs tracking and the kernel argument first, the file
# last (rpm-ostree rebuilds the initramfs overlay and fails if a tracked file is already gone)
for f in /etc/firmware/edid/clawdeck-*.bin; do
  [ -e "$f" ] || continue
  echo "  VRR screen fix: initramfs tracking of $f"; run sudo rpm-ostree initramfs-etc --untrack="$f"
done
if rpm-ostree kargs 2>/dev/null | tr ' ' '\n' | grep -q '^drm.edid_firmware=.*clawdeck'; then
  k=$(rpm-ostree kargs | tr ' ' '\n' | grep '^drm.edid_firmware=.*clawdeck')
  echo "  kernel argument $k"; run sudo rpm-ostree kargs --delete="$k"
fi
if ls /etc/firmware/edid/clawdeck-*.bin >/dev/null 2>&1; then echo "  VRR screen fix file"; run sudo rm -f /etc/firmware/edid/clawdeck-*.bin; fi
for f in /etc/inputplumber/devices.d/50-msi_claw8_ex_ai_cg3em.yaml /etc/inputplumber/capability_maps.d/msiclaw_claw8ex.yaml \
         /etc/udev/hwdb.d/61-clawdeck-msi-claw-8-ex.hwdb /etc/udev/hwdb.d/61-msi-claw-8-ex.hwdb \
         /etc/systemd/system/steamos-manager.service.d/50-clawdeck-quiet-split-lock.conf /etc/tmpfiles.d/clawdeck-overlay-power.conf; do
  [ -e "$f" ] && { echo "  $f"; run sudo rm -f "$f"; }
done
run sudo systemctl daemon-reload
run sudo chmod 0400 /sys/class/powercap/intel-rapl:0/energy_uj /sys/class/powercap/intel-rapl-mmio:0/energy_uj

say "Decky plugins: ClawDeck and Decky Framegen"
for p in "$PLUG/ClawDeck" "$PLUG/Decky-Framegen"; do [ -e "$p" ] && { echo "  $p"; run sudo rm -rf "$p"; }; done
[ -e "$HOME/fgmod" ] && { echo "  $HOME/fgmod"; run rm -rf "$HOME/fgmod"; }

if [ $DO = 1 ]; then
  echo; echo "Done. Kept: ~/fsr4-xmx (nitec's files), ~/clawdeck-test. Reboot now (sudo reboot), then install ClawDeck fresh."
else
  echo; echo "Nothing changed. Run again with --do to remove all of the above."
fi
