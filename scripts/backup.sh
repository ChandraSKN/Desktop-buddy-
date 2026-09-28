#!/bin/bash
# Pack up Buddy's personal data (not the code, which is in git) into one file to carry to
# another computer, where scripts/restore.sh unpacks it.
#   scripts/backup.sh                    -> ~/desktop-buddy-backup-<date>.tar.gz
#   scripts/backup.sh --with-whatsapp    also the linked WhatsApp session (otherwise re-link
#                                        on the new computer; it's a 30-second QR scan)
#   scripts/backup.sh FILE.tar.gz        choose where it goes
# The file holds your API key and calendar link: keep it private (it's made owner-only).
# Voices, speech models and the screen-share permission are not included; setup.sh and the
# first recording recreate them.
set -eo pipefail
umask 077
OUT="$HOME/desktop-buddy-backup-$(date +%F).tar.gz"
WHATSAPP=0
for arg in "$@"; do
    case "$arg" in
        --with-whatsapp) WHATSAPP=1 ;;
        -*) echo "unknown option: $arg"; exit 2 ;;
        *) OUT="$arg" ;;
    esac
done

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cd "$HOME"
copy() { [ -e "$1" ] && mkdir -p "$STAGE/$(dirname "$1")" && cp -a "$1" "$STAGE/$1" && echo "  + $1"; true; }
# SQLite files are copied with SQLite's own backup, so a running Buddy can't leave a half-written copy
copy_db() {
    [ -e "$1" ] || return 0
    mkdir -p "$STAGE/$(dirname "$1")"
    python3 -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close()" \
        "$1" "$STAGE/$1"
    echo "  + $1"
}

echo "Backing up:"
copy .config/desktop-buddy/api_key                 # Claude API key
copy .config/DesktopBuddy/DesktopBuddy.conf        # settings, incl. the Outlook calendar link
copy_db .local/share/desktop-buddy/memory.db       # what Buddy remembers, his reminders, minutes index
copy .local/share/desktop-buddy/recordings         # older meeting recordings
copy "Documents/Meeting Minutes"                   # minutes, transcripts, recordings
[ "$WHATSAPP" = 1 ] && copy_db .local/share/desktop-buddy/whatsapp.db

tar -czf "$OUT" -C "$STAGE" .
chmod 600 "$OUT"
echo
echo "Saved $OUT ($(du -h "$OUT" | cut -f1)). It contains your API key: copy it over privately"
echo "(USB stick, scp), then on the new computer run:  scripts/restore.sh $(basename "$OUT")"
