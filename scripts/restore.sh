#!/bin/bash
# Unpack a scripts/backup.sh file into this computer's home folder and restart Buddy.
#   scripts/restore.sh ~/desktop-buddy-backup-2026-09-28.tar.gz
# Files that already exist here are kept next to the restored ones as NAME.~1~.
set -eo pipefail
[ -f "$1" ] || { echo "usage: $0 BACKUP.tar.gz"; exit 2; }
echo "Restoring into $HOME:"
tar -tzf "$1" | grep -v '/$' | sed 's#^\./#  #' | grep -v '^  Documents/Meeting Minutes/.*/' || true

RUNNING=0
if systemctl --user is-active -q desktop-buddy 2>/dev/null; then
    RUNNING=1
    systemctl --user stop desktop-buddy          # so he doesn't write to the files being replaced
fi
tar -xzf "$1" -C "$HOME" --backup=numbered
chmod 600 "$HOME/.config/desktop-buddy/api_key" 2>/dev/null || true
[ "$RUNNING" = 1 ] && systemctl --user start desktop-buddy

echo
echo "Restored. Check with:  buddy doctor"
