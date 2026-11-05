#!/usr/bin/env bash
# Install the latest main build of ClawDeck on this Claw (Decky plugin dir), from the sideload branch CI publishes.
# Usage: bash update-from-main.sh   (asks for sudo once; restarts Decky Loader)
set -euo pipefail
# the sideload branch's newest commit, so GitHub's raw cache (up to 5 minutes) can't hand out the build before it;
# the branch name if GitHub's API can't be reached
SHA=$(curl -fsSL https://api.github.com/repos/IHulkSmashI/ClawDeck/commits/sideload 2>/dev/null | sed -n 's/^  "sha": "\([0-9a-f]\{40\}\)".*/\1/p' | head -1)
URL=https://raw.githubusercontent.com/IHulkSmashI/ClawDeck/${SHA:-sideload}
DEST=${DECKY_PLUGINS:-$HOME/homebrew/plugins}
tmp=$(mktemp -d)
curl -fsSL "$URL/ClawDeck.zip" -o "$tmp/ClawDeck.zip"
curl -fsSL "$URL/BUILD.txt" || true
echo
# unpack first, swap after: a broken download must not leave the user without a plugin
unzip -qo "$tmp/ClawDeck.zip" -d "$tmp/new"
[ -f "$tmp/new/ClawDeck/main.py" ] || { echo "downloaded archive has no ClawDeck/main.py; nothing changed" >&2; exit 1; }
sudo rm -rf "$DEST/ClawDeck"
sudo mv "$tmp/new/ClawDeck" "$DEST/ClawDeck"
sudo chown -R "$USER:$USER" "$DEST/ClawDeck" 2>/dev/null || true
# a plain restart can leave the old PluginLoader process alive (seen on Bazzite: "Unit process remains running after
# unit stopped"), and the Quick Access menu then keeps talking to the old plugin code. Stop, kill leftovers, start.
sudo systemctl stop plugin_loader
sudo pkill -x PluginLoader 2>/dev/null || true   # by process name (not -f: that matched sudo's own command line)
sleep 1
sudo systemctl start plugin_loader
rm -rf "$tmp"
echo "ClawDeck updated in $DEST; Decky restarted. Open Quick Access -> Decky -> ClawDeck."
