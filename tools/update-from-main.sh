#!/usr/bin/env bash
# Install the latest main build of ClawDeck on this Claw (Decky plugin dir), from the sideload branch CI publishes.
# Usage: bash update-from-main.sh   (asks for sudo once; restarts Decky Loader)
set -euo pipefail
URL=https://raw.githubusercontent.com/IHulkSmashI/ClawDeck/sideload
DEST=${DECKY_PLUGINS:-$HOME/homebrew/plugins}
tmp=$(mktemp -d)
curl -fsSL "$URL/ClawDeck.zip" -o "$tmp/ClawDeck.zip"
curl -fsSL "$URL/BUILD.txt" || true
echo
sudo rm -rf "$DEST/ClawDeck"
sudo unzip -qo "$tmp/ClawDeck.zip" -d "$DEST"
sudo chown -R "$USER:$USER" "$DEST/ClawDeck" 2>/dev/null || true
# a plain restart can leave the old PluginLoader process alive (seen on Bazzite: "Unit process remains running after
# unit stopped"), and the Quick Access menu then keeps talking to the old plugin code. Stop, kill leftovers, start.
sudo systemctl stop plugin_loader; sudo pkill -f homebrew/services/PluginLoader 2>/dev/null; sleep 1; sudo systemctl start plugin_loader
rm -rf "$tmp"
echo "ClawDeck updated in $DEST; Decky restarted. Open Quick Access -> Decky -> ClawDeck."
